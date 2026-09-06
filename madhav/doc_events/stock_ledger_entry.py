import frappe
from frappe.utils import flt


def create_piece_stock_ledger_entry(sle_doc, method):
    if not frappe.db.get_value("Item", sle_doc.item_code, "required_stock_in_pieces"):
        return

    if sle_doc.is_cancelled:
        # This SLE call represents cancellation of the original stock
        # ledger entries for this voucher. get_piece_qty/adjust_piece_qty_sign
        # only know the source doc's static pieces field and voucher_type -
        # they cannot distinguish a creation call from a cancellation call,
        # so computing a fresh signed value here would create another entry
        # with the SAME sign as the original, compounding rather than
        # reversing it (observed: a second -20 row instead of a +20
        # reversal, leaving the original -20 row permanently active).
        # Cancel the existing Piece SLE row(s) for this exact voucher directly.
        frappe.db.sql(
            """
            UPDATE `tabPiece Stock Ledger Entry`
            SET is_cancelled = 1
            WHERE voucher_type = %s AND voucher_no = %s AND is_cancelled = 0
            """,
            (sle_doc.voucher_type, sle_doc.voucher_no),
        )
        affected_batches = frappe.db.sql(
            """
            SELECT DISTINCT batch_no FROM `tabPiece Stock Ledger Entry`
            WHERE voucher_type = %s AND voucher_no = %s AND batch_no IS NOT NULL
            """,
            (sle_doc.voucher_type, sle_doc.voucher_no),
        )
        for (batch_no,) in affected_batches:
            recalculate_batch_pieces(batch_no)
        return

    if sle_doc.voucher_type == "Stock Reconciliation":
        return

    piece_qty = get_piece_qty(sle_doc)
    if piece_qty is None:
        return

    signed_piece_qty = adjust_piece_qty_sign(sle_doc, piece_qty)
    if not signed_piece_qty:
        return

    # Batch-tracked items almost always move stock through a Serial and
    # Batch Bundle - the SLE header's own batch_no field is frequently
    # left blank even for a genuinely single-batch transaction (the real
    # batch/qty split lives on the bundle's own entries). Resolve the
    # actual batch(es) and their relative qty share from the bundle when
    # one exists, falling back to sle_doc.batch_no only when there is no
    # bundle at all.
    batch_qty_shares = _get_batch_qty_shares(sle_doc)
    if not batch_qty_shares:
        return

    total_share_qty = sum(batch_qty_shares.values()) or 1
    batch_nos = list(batch_qty_shares.keys())

    if len(batch_nos) == 1:
        # Single batch: apply the whole pieces value directly - no ratio
        # split, no rounding drift.
        _create_piece_sle_row(sle_doc, batch_nos[0], signed_piece_qty)
    else:
        # Multiple batches under one bundle: split proportionally by
        # each batch's qty share, absorbing rounding remainder on the
        # first batch so the pieces sum stays exact.
        allocated = []
        for batch_no in batch_nos:
            ratio = batch_qty_shares[batch_no] / total_share_qty
            allocated.append(round(signed_piece_qty * ratio))

        leftover = signed_piece_qty - sum(allocated)
        if allocated:
            allocated[0] += leftover

        for batch_no, batch_piece_qty in zip(batch_nos, allocated):
            if not batch_piece_qty:
                continue
            _create_piece_sle_row(sle_doc, batch_no, batch_piece_qty)

    for batch_no in batch_nos:
        recalculate_batch_pieces(batch_no)


def _get_batch_qty_shares(sle_doc):
    """Return {batch_no: qty_share} for this SLE, preferring the Serial
    and Batch Bundle's own entries (which reliably carry batch_no) over
    the SLE header's batch_no field (which is often blank)."""
    if sle_doc.serial_and_batch_bundle:
        entries = frappe.get_all(
            "Serial and Batch Entry",
            filters={"parent": sle_doc.serial_and_batch_bundle},
            fields=["batch_no", "qty"],
        )
        return {e.batch_no: abs(flt(e.qty)) for e in entries if e.batch_no}

    if sle_doc.batch_no:
        return {sle_doc.batch_no: 1.0}

    return {}


def _create_piece_sle_row(sle_doc, batch_no, piece_qty):
    piece_doc = frappe.new_doc("Piece Stock Ledger Entry")
    piece_doc.update({
        "posting_date": sle_doc.posting_date,
        "posting_time": sle_doc.posting_time,
        "item_code": sle_doc.item_code,
        "warehouse": sle_doc.warehouse,
        "voucher_type": sle_doc.voucher_type,
        "voucher_no": sle_doc.voucher_no,
        "serial_and_batch_bundle": sle_doc.serial_and_batch_bundle,
        "actual_qty": piece_qty,
        "incoming_rate": sle_doc.incoming_rate,
        "company": sle_doc.company,
        "unit_of_measure": "Piece",
        "is_cancelled": sle_doc.is_cancelled,
        "batch_no": batch_no,
        "docstatus": sle_doc.docstatus,
    })
    piece_doc.insert(ignore_permissions=True)


def recalculate_batch_pieces(batch_no):
    """Recompute Batch.pieces as the sum of ALL currently-active Piece
    Stock Ledger Entry rows for this batch, rather than incrementally
    adding/subtracting a delta to whatever value is currently stored.

    Must explicitly exclude is_cancelled=1 rows: a cancelled Piece SLE's
    own docstatus stays 1 and its original actual_qty is left untouched
    -- cancellation here is represented by the is_cancelled flag, not by
    a fresh offsetting reversal row. Summing all docstatus=1 rows without
    this filter double-counts every cancelled delivery's original
    (negative) qty, producing a batch pieces value far below the true
    figure (observed: -40 instead of 10 after a single cancelled +
    single active 20-piece delivery on a 30-piece batch).
    """
    if not batch_no:
        return

    total = frappe.db.sql(
        """
        SELECT COALESCE(SUM(actual_qty), 0)
        FROM `tabPiece Stock Ledger Entry`
        WHERE batch_no = %s AND docstatus = 1 AND is_cancelled = 0
        """,
        batch_no,
    )[0][0]

    frappe.db.set_value("Batch", batch_no, "pieces", flt(total), update_modified=False)


def get_piece_qty(sle_doc):
    """Try to fetch piece count from the relevant child table row."""
    voucher_type = sle_doc.voucher_type
    detail_no = sle_doc.voucher_detail_no
    if not voucher_type or not detail_no:
        return None

    mapping = {
        "Purchase Receipt": "Purchase Receipt Item",
        "Purchase Invoice": "Purchase Invoice Item",
        "Sales Invoice": "Sales Invoice Item",
        "Delivery Note": "Delivery Note Item",
        "Stock Entry": "Stock Entry Detail",
    }

    child_doctype = mapping.get(voucher_type)
    if not child_doctype:
        return None

    return frappe.db.get_value(child_doctype, detail_no, "pieces")


def adjust_piece_qty_sign(sle_doc, piece_qty):
    """Make piece_qty negative for outgoing transactions."""
    if sle_doc.voucher_type == "Delivery Note":
        return -1 * abs(piece_qty)

    if sle_doc.voucher_type == "Sales Invoice":
        return -1 * abs(piece_qty)

    if sle_doc.voucher_type == "Purchase Receipt" and frappe.db.get_value(
        "Purchase Receipt", sle_doc.voucher_no, "is_return"
    ) == 1:
        return -1 * abs(piece_qty)

    if sle_doc.voucher_type == "Stock Entry":
        purpose = frappe.db.get_value("Stock Entry", sle_doc.voucher_no, "purpose")

        if purpose in ["Material Issue", "Send to Subcontractor"]:
            return -1 * abs(piece_qty)
        elif purpose in ["Material Receipt", "Receive from Subcontractor"]:
            return abs(piece_qty)
        else:
            return piece_qty if sle_doc.actual_qty > 0 else -1 * abs(piece_qty)

    # Default: assume incoming
    return abs(piece_qty)