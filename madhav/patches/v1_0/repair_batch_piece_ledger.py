"""Repair Piece Stock Ledger rows written by the superseded piece rules.

The piece fixes in delivery_note.py and stock_ledger_entry.py are write-time:
they change what a NEW posting records.  recalculate_batch_pieces() resums the
Piece Stock Ledger from scratch every time, so any wrong row already written
keeps being counted forever - nothing self-heals, and batches created before
the fixes keep reporting inflated or negative piece counts.

This one-off patch corrects those rows.  It is deliberately narrow:

  * it only ever cancels or replaces rows this app SYNTHESISED - the baseline
    anchor rows (voucher_type 'Batch') and the reconciliation piece rows
    (voucher_type 'Stock Reconciliation');
  * it never edits a piece row belonging to a real voucher (Stock Entry,
    Purchase Receipt, ...) except to retire an overdraft that would otherwise
    leave a batch physically impossible, and then only by the exact overdraft;
  * it cancels rather than deletes, so the correction is auditable;
  * it is idempotent - a second run finds nothing to do.

Both quantity and piece figures come from the matched Serial and Batch Entry,
never from the bundle-wide sle.actual_qty.
"""

import frappe
from frappe.utils import flt, cint


def execute():
    if not frappe.db.table_exists("Piece Stock Ledger Entry"):
        return

    repaired = {"anchors": 0, "reconciliations": 0, "overdrafts": 0, "resynced": 0}
    touched = set()

    for batch_no in _batches_with_piece_rows():
        if _repair_stale_anchor(batch_no):
            repaired["anchors"] += 1
            touched.add(batch_no)
        if _repair_reconciliation_rows(batch_no):
            repaired["reconciliations"] += 1
            touched.add(batch_no)

    # Recompute before looking at overdrafts: correcting a reconciliation row
    # often clears the negative on its own.
    _recalculate(touched)

    for batch_no in _batches_with_piece_rows():
        if _repair_overdraft(batch_no):
            repaired["overdrafts"] += 1
            touched.add(batch_no)

    _recalculate(touched)

    # Finally resync every batch whose master piece count has drifted from its
    # own piece ledger.  recalculate_batch_pieces() IS the app's definition of
    # Batch.pieces, so this only re-applies the existing rule - it invents
    # nothing.  Batches with NO piece rows at all are deliberately skipped:
    # those predate the piece ledger entirely and resumming them would wipe a
    # legitimate legacy count to zero.
    for batch_no in _batches_with_piece_rows():
        ledger = flt(frappe.db.sql(
            """
            select coalesce(sum(actual_qty), 0)
            from `tabPiece Stock Ledger Entry`
            where batch_no = %s and docstatus = 1 and is_cancelled = 0
            """,
            batch_no,
        )[0][0])
        if cint(frappe.db.get_value("Batch", batch_no, "pieces")) != cint(ledger):
            repaired["resynced"] = repaired.get("resynced", 0) + 1
            touched.add(batch_no)
            _recalculate([batch_no])

    frappe.db.commit()

    if touched:
        frappe.log_error(
            title="Batch piece ledger repaired",
            message=frappe.as_json(
                {"counts": repaired, "batches": sorted(touched)}, indent=2
            ),
        )
    print(
        "repair_batch_piece_ledger: %s stale anchors, %s reconciliation rows, "
        "%s overdrafts, %s master resyncs across %s batch(es)"
        % (repaired["anchors"], repaired["reconciliations"],
           repaired["overdrafts"], repaired.get("resynced", 0), len(touched))
    )


# ------------------------------------------------------------------ helpers
def _batches_with_piece_rows():
    return frappe.db.sql_list(
        """
        select distinct batch_no from `tabPiece Stock Ledger Entry`
        where docstatus = 1 and is_cancelled = 0
          and ifnull(batch_no, '') != ''
        """
    )


def _rows(batch_no):
    return frappe.db.sql(
        """
        select name, voucher_type, voucher_no, actual_qty
        from `tabPiece Stock Ledger Entry`
        where batch_no = %s and docstatus = 1 and is_cancelled = 0
        order by posting_date, posting_time, creation
        """,
        batch_no,
        as_dict=True,
    )


def _cancel(row_name, reason):
    frappe.db.set_value(
        "Piece Stock Ledger Entry", row_name, "is_cancelled", 1, update_modified=False
    )
    frappe.logger().info("repair_batch_piece_ledger: cancelled %s (%s)" % (row_name, reason))


def _batch_had_stock_before_voucher(batch_no, voucher_type, voucher_no):
    """Mirrors the guard now in stock_ledger_entry._ensure_baseline_piece_sle."""
    if not voucher_type or not voucher_no:
        return True
    prior = frappe.db.sql(
        """
        SELECT COALESCE(SUM(SIGN(sle.actual_qty) * ABS(sbe.qty)), 0)
        FROM `tabStock Ledger Entry` sle
        INNER JOIN `tabSerial and Batch Entry` sbe
            ON sbe.parent = sle.serial_and_batch_bundle
        WHERE sbe.batch_no = %(b)s AND sle.is_cancelled = 0
          AND NOT (sle.voucher_type = %(vt)s AND sle.voucher_no = %(vn)s)
        """,
        {"b": batch_no, "vt": voucher_type, "vn": voucher_no},
    )[0][0]
    return flt(prior) > 0


def _recalculate(batches):
    from madhav.doc_events.stock_ledger_entry import recalculate_batch_pieces

    for batch_no in batches:
        recalculate_batch_pieces(batch_no)


# ------------------------------------------------------------------ repairs
def _repair_stale_anchor(batch_no):
    """Retire a baseline anchor the current rule would never have seeded.

    The anchor exists so a batch holding stock with no piece history does not
    resum to a negative on its first outward movement.  Seeded against a batch
    that held nothing, it simply double-counts the movement bringing the stock
    in, which is what reported more pieces than the batch physically holds.
    """
    rows = _rows(batch_no)
    anchors = [r for r in rows if r.voucher_type == "Batch"]
    if not anchors:
        return False

    trigger = next((r for r in rows if r.voucher_type != "Batch"), None)
    if not trigger:
        # nothing but an anchor: no movement has happened, leave it alone
        return False

    if _batch_had_stock_before_voucher(batch_no, trigger.voucher_type, trigger.voucher_no):
        return False

    for anchor in anchors:
        _cancel(anchor.name,
                "batch held no stock before %s %s" % (trigger.voucher_type, trigger.voucher_no))
    return True


def _repair_reconciliation_rows(batch_no):
    """Re-derive each reconciliation piece row under the current rule.

    A batch gains or loses a piece only when it gains or loses a whole piece's
    weight; the superseded rule rounded the absolute target up, so a sub-piece
    weight adjustment invented a whole piece.
    """
    dims = frappe.db.get_value(
        "Batch", batch_no, ["average_length", "section_weight"], as_dict=True
    ) or frappe._dict()
    piece_weight = flt(dims.get("average_length")) * flt(dims.get("section_weight")) / 1000.0
    if piece_weight <= 0:
        return False

    changed = False
    for row in _rows(batch_no):
        if row.voucher_type != "Stock Reconciliation":
            continue
        sri = frappe.db.get_value(
            "Stock Reconciliation Item",
            {"parent": row.voucher_no, "batch_no": batch_no},
            ["difference_qty"],
            as_dict=True,
        )
        if not sri:
            continue

        want = int(flt(sri.difference_qty) / piece_weight)
        if cint(row.actual_qty) == cint(want):
            continue

        _cancel(row.name, "piece delta %s superseded by %s" % (row.actual_qty, want))
        if want:
            _clone_piece_row(row.name, want)
        changed = True
    return changed


def _clone_piece_row(source_name, actual_qty):
    """Replacement row carrying the corrected piece movement.

    Only fields the doctype actually defines are copied - the Piece Stock
    Ledger Entry schema varies between sites.
    """
    src = frappe.get_doc("Piece Stock Ledger Entry", source_name)
    doc = frappe.new_doc("Piece Stock Ledger Entry")
    payload = {"actual_qty": actual_qty, "unit_of_measure": "Piece",
               "is_cancelled": 0, "docstatus": 1}
    for field in ("posting_date", "posting_time", "item_code", "warehouse",
                  "voucher_type", "voucher_no", "serial_and_batch_bundle",
                  "incoming_rate", "company", "batch_no"):
        if doc.meta.has_field(field):
            payload[field] = src.get(field)
    doc.update(payload)
    doc.flags.ignore_permissions = True
    doc.insert()


def _repair_overdraft(batch_no):
    """Bring a batch that sums below zero back to exactly zero.

    A negative piece count is physically impossible: it means an outward row
    took more pieces than the batch held, which the superseded rule allowed by
    rounding a topped-up weight up past the batch's real piece count.  Reduce
    the most recent outward row by exactly the overdraft - never more, and
    never turning it into an inward row - so the batch lands on zero instead of
    a negative.  Nothing is invented: the correction is bounded by the shortfall
    itself.
    """
    rows = _rows(batch_no)
    if not rows:
        return False
    total = sum(flt(r.actual_qty) for r in rows)
    if total >= 0:
        return False

    overdraft = -total
    for row in reversed(rows):
        if flt(row.actual_qty) >= 0:
            continue
        reducible = min(overdraft, abs(flt(row.actual_qty)))
        if reducible <= 0:
            continue
        new_qty = flt(row.actual_qty) + reducible
        _cancel(row.name, "outward row exceeded the batch's pieces by %s" % reducible)
        if new_qty:
            _clone_piece_row(row.name, new_qty)
        overdraft -= reducible
        if overdraft <= 0:
            break
    return True
