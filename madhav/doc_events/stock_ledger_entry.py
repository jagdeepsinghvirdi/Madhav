import frappe
from frappe.utils import now,flt
from erpnext.stock.serial_batch_bundle import get_batchwise_qty


def create_piece_stock_ledger_entry(sle_doc, method):
    # Check if piece_qty exists in the parent document, else skip
    if not frappe.db.get_value("Item",sle_doc.item_code,"required_stock_in_pieces"):
        return
    piece_qty = get_piece_qty(sle_doc)
    
    if piece_qty is None:
        return

    # Determine if the piece_qty should be negative based on context
    piece_qty = adjust_piece_qty_sign(sle_doc, piece_qty)
    # Create the new Piece Stock Ledger Entry
    piece_doc = frappe.new_doc("Piece Stock Ledger Entry")
    piece_doc.update({
        "posting_date": sle_doc.posting_date,
        "posting_time": sle_doc.posting_time,
        "item_code": sle_doc.item_code,
        "warehouse": sle_doc.warehouse,
        "voucher_type": sle_doc.voucher_type,
        "voucher_no": sle_doc.voucher_no,
        # "voucher_detail_no": sle_doc.voucher_detail_no,
        # "stock_uom": sle_doc.stock_uom,
        # "piece_qty": piece_qty,
        "serial_and_batch_bundle": sle_doc.serial_and_batch_bundle,
        "actual_qty": piece_qty,
        "incoming_rate": sle_doc.incoming_rate,
        "company": sle_doc.company,
        "unit_of_measure": "Piece",
        "is_cancelled": sle_doc.is_cancelled,
         "batch_no": sle_doc.batch_no,
        "docstatus" : sle_doc.docstatus
        # "stock_value": sle_doc.stock_value,
        # "stock_value_difference": sle_doc.stock_value_difference,
    })
    piece_doc.insert(ignore_permissions=True)
    update_batch_piece_on_sle(sle_doc, piece_qty)


def get_piece_qty(sle_doc):
    
    """Try to fetch piece from relevant child table"""
    voucher_type = sle_doc.voucher_type
    detail_no = sle_doc.voucher_detail_no
    if not voucher_type or not detail_no:
        return None

    # Mapping of parent doctype -> child doctype
    mapping = {
        "Purchase Receipt": "Purchase Receipt Item",
        "Purchase Invoice": "Purchase Invoice Item",
        "Sales Invoice": "Sales Invoice Item",
        "Delivery Note": "Delivery Note Item",
        "Stock Entry": "Stock Entry Detail"
    }

    child_doctype = mapping.get(voucher_type)
    if not child_doctype:
        return None

    return frappe.db.get_value(child_doctype, detail_no, "pieces")


def adjust_piece_qty_sign(sle_doc, piece_qty):
    """Make piece_qty negative for outgoing transactions"""
    if sle_doc.voucher_type == "Delivery Note":
        return -1 * abs(piece_qty)
    
    if sle_doc.voucher_type == "Sales Invoice":
        return -1 * abs(piece_qty)

    if sle_doc.voucher_type == "Purchase Receipt" and frappe.db.get_value("Purchase Receipt",sle_doc.voucher_no,"is_return") == 1:
        return -1 * abs(piece_qty)
    
    if sle_doc.voucher_type == "Stock Entry":
        # Get Stock Entry purpose
        purpose = frappe.db.get_value("Stock Entry", sle_doc.voucher_no, "purpose")

        if purpose in ["Material Issue", "Send to Subcontractor"]:
            return -1 * abs(piece_qty)
        elif purpose in ["Material Receipt", "Receive from Subcontractor"]:
            return abs(piece_qty)
        else:
            # For Material Transfer and others:
            # Incoming warehouse gets positive, outgoing gets negative
            return piece_qty if sle_doc.actual_qty > 0 else -1 * abs(piece_qty)

    # Default: assume incoming
    return abs(piece_qty)


def update_batch_piece_on_sle(sle_doc, piece_qty):
	"""
	Update Batch.pieces safely using row lock.
	Called once per Piece Stock Ledger Entry.
	"""

	if not sle_doc.batch_no:
		return

	doctype = frappe.qb.DocType("Batch")

	query = (
		frappe.qb.from_(doctype)
		.select(doctype.pieces)
		.where(doctype.name == sle_doc.batch_no)
		.for_update()
	)

	current = query.run()
	current_qty = flt(current[0][0]) if current else 0

	# Reverse on cancel
	if sle_doc.is_cancelled:
		piece_qty = -piece_qty

	frappe.db.set_value(
		"Batch",
		sle_doc.batch_no,
		"pieces",
		current_qty + piece_qty,
		update_modified=False
	)
