import frappe
from frappe.utils import flt


def on_submit(self, method=None):
    """Sync DN item weight after SR for Deliver-as-Qty overage.

    Only update qty / incoming_rate — never pieces (physical PC must stay).
    Match DN rows via bundle + batch + delivery_note_qty so duplicate
    item/batch lines on one DN update the correct row.
    """
    matched_dn_items = set()

    for row in self.items:
        if not row.delivery_note_ref:
            continue

        dn_item_name = _find_dn_item_for_sr_row(row, matched_dn_items)
        if not dn_item_name:
            continue

        matched_dn_items.add(dn_item_name)
        frappe.db.set_value(
            "Delivery Note Item",
            dn_item_name,
            {
                "qty": flt(row.qty),
                "incoming_rate": flt(row.valuation_rate),
            },
            update_modified=False,
        )


def _find_dn_item_for_sr_row(row, matched=None):
    """Resolve the exact DN item this SR row belongs to."""
    matched = matched or set()
    dn_name = row.delivery_note_ref
    item_code = row.item_code
    batch_no = row.batch_no or ""
    dn_qty = flt(getattr(row, "delivery_note_qty", 0))

    candidates = []

    if batch_no:
        not_matched = ""
        params = {
            "dn_name": dn_name,
            "item_code": item_code,
            "batch_no": batch_no,
        }
        if matched:
            not_matched = "AND dni.name NOT IN %(matched)s"
            params["matched"] = tuple(matched)

        candidates = frappe.db.sql(
            f"""
            SELECT dni.name, dni.qty, dni.pieces, dni.length_size, dni.warehouse
            FROM `tabDelivery Note Item` dni
            INNER JOIN `tabSerial and Batch Entry` sbe
                ON sbe.parent = dni.serial_and_batch_bundle
                AND sbe.parenttype = 'Serial and Batch Bundle'
            WHERE dni.parent = %(dn_name)s
              AND dni.item_code = %(item_code)s
              AND sbe.batch_no = %(batch_no)s
              {not_matched}
            """,
            params,
            as_dict=True,
        )
    else:
        candidates = frappe.get_all(
            "Delivery Note Item",
            filters={
                "parent": dn_name,
                "item_code": item_code,
                "name": ["not in", list(matched) or [""]],
            },
            fields=["name", "qty", "pieces", "length_size", "warehouse"],
        )

    if not candidates:
        return None

    if len(candidates) == 1:
        return candidates[0].name

    # Prefer the DN line whose pre-SR qty matches delivery_note_qty on this SR row.
    if dn_qty:
        for cand in candidates:
            if abs(flt(cand.qty) - dn_qty) < 0.0001:
                return cand.name

    # Secondary tie-breakers: warehouse, pieces, length (stable order by name).
    warehouse = getattr(row, "warehouse", None)
    if warehouse:
        wh_matches = [c for c in candidates if c.warehouse == warehouse]
        if len(wh_matches) == 1:
            return wh_matches[0].name
        if wh_matches:
            candidates = wh_matches

    sr_pieces = flt(getattr(row, "pieces", 0))
    if sr_pieces:
        piece_matches = [c for c in candidates if abs(flt(c.pieces) - sr_pieces) < 0.0001]
        if len(piece_matches) == 1:
            return piece_matches[0].name
        if piece_matches:
            candidates = piece_matches

    sr_length = flt(getattr(row, "length", 0)) or flt(getattr(row, "average_length", 0))
    if sr_length:
        len_matches = [
            c for c in candidates if abs(flt(c.length_size) - sr_length) < 0.0001
        ]
        if len(len_matches) == 1:
            return len_matches[0].name
        if len_matches:
            candidates = len_matches

    # Still ambiguous — do not update the wrong row.
    frappe.log_error(
        title="Stock Reconciliation - Ambiguous DN Item Match",
        message=frappe.as_json(
            {
                "delivery_note": dn_name,
                "item_code": item_code,
                "batch_no": batch_no,
                "delivery_note_qty": dn_qty,
                "candidates": [c.name for c in candidates],
            }
        ),
    )
    return None


def validate(self, method=None):

    for row in self.items:
        if row.delivery_note_ref:
            total_amount = flt(row.amount)

            if not row.current_rate:
                row.current_rate = row.current_valuation_rate or row.valuation_rate

            diff_qty = flt(row.difference_qty)
            new_qty = flt(row.current_qty) + diff_qty
            if new_qty > 0:
                row.qty = new_qty
                # Preserve value when stock already has a valuation; never wipe to 0
                # when current_amount is missing (e.g. empty warehouse at posting time).
                if flt(row.current_amount):
                    row.valuation_rate = flt(row.current_amount) / new_qty
                elif not flt(row.valuation_rate):
                    row.valuation_rate = (
                        flt(row.current_valuation_rate)
                        or flt(row.current_rate)
                        or flt(frappe.get_cached_value("Item", row.item_code, "valuation_rate"))
                        or 1
                    )

            # row.amount = total_amount
