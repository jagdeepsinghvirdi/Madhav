import frappe
from frappe.utils import flt


def create_piece_stock_ledger_entry(sle_doc, method):
	"""Create Piece SLE(s) and keep Batch.pieces in sync.

	Does NOT touch Batch.average_length (Length Size) — that stays static.

	When stock moves via Serial and Batch Bundle, ERPNext often leaves
	SLE.batch_no blank. Older code then wrote one PSLE with no batch, so
	Batch.Length/Pieces never reduced on Delivery Note. Resolve batches
	from the bundle and post one PSLE per batch.
	"""
	if not frappe.db.get_value("Item", sle_doc.item_code, "required_stock_in_pieces"):
		return

	if sle_doc.is_cancelled:
		# Cancelling the SLE must cancel existing Piece SLE rows for this
		# voucher — do NOT insert another signed row (that doubles the hit).
		frappe.db.sql(
			"""
			UPDATE `tabPiece Stock Ledger Entry`
			SET is_cancelled = 1
			WHERE voucher_type = %s AND voucher_no = %s AND is_cancelled = 0
			""",
			(sle_doc.voucher_type, sle_doc.voucher_no),
		)
		affected_batches = _batches_touched_by_voucher(sle_doc.voucher_type, sle_doc.voucher_no)
		for batch_no in affected_batches:
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

	batch_piece_map = _get_batch_piece_allocation(sle_doc, signed_piece_qty)
	if not batch_piece_map:
		return

	for batch_no, batch_piece_qty in batch_piece_map.items():
		if not batch_piece_qty:
			continue
		# A batch whose current Batch.pieces predates the Piece Ledger (set
		# via receipt/manual entry with no corresponding PSLE row) has no
		# history to resum from — its very first delivery's own row would
		# become the ENTIRE ledger, so a batch showing pieces=20 with zero
		# ledger rows resums to -20 after a 20-piece delivery, and 0 after
		# that delivery is cancelled, instead of correctly landing on 0 and
		# reverting to 20. Seed a one-time anchor row equal to the batch's
		# current pieces value before applying any delta.
		_ensure_baseline_piece_sle(sle_doc.item_code, sle_doc.warehouse, batch_no, sle_doc.company)
		_create_piece_sle_row(sle_doc, batch_no, batch_piece_qty)

	for batch_no in batch_piece_map:
		recalculate_batch_pieces(batch_no)


def _ensure_baseline_piece_sle(item_code, warehouse, batch_no, company):
	"""
	recalculate_batch_pieces() resums from the Piece Stock Ledger alone.
	Seed one anchor row equal to the batch's current pieces value before
	applying any delta — only fires once per batch (checks for any
	existing row first, cancelled or not, so it never re-seeds after the
	batch has real history).
	"""
	if not batch_no:
		return
	if frappe.db.exists("Piece Stock Ledger Entry", {"batch_no": batch_no, "docstatus": 1}):
		return

	current_pieces = flt(frappe.db.get_value("Batch", batch_no, "pieces"))
	if not current_pieces:
		return

	frappe.get_doc({
		"doctype": "Piece Stock Ledger Entry",
		"posting_date": frappe.utils.nowdate(),
		"posting_time": "00:00:00",
		"item_code": item_code,
		"warehouse": warehouse,
		"voucher_type": "Batch",
		"voucher_no": batch_no,
		"actual_qty": current_pieces,
		"company": company,
		"unit_of_measure": "Piece",
		"is_cancelled": 0,
		"batch_no": batch_no,
		"docstatus": 1,
	}).insert(ignore_permissions=True)


def _batches_touched_by_voucher(voucher_type, voucher_no):
	"""Batches linked to this voucher via PSLE.batch_no or via SABB entries."""
	named = frappe.db.sql(
		"""
		SELECT DISTINCT batch_no
		FROM `tabPiece Stock Ledger Entry`
		WHERE voucher_type = %s AND voucher_no = %s
		  AND IFNULL(batch_no, '') != ''
		""",
		(voucher_type, voucher_no),
	)
	from_bundle = frappe.db.sql(
		"""
		SELECT DISTINCT sbe.batch_no
		FROM `tabPiece Stock Ledger Entry` psle
		INNER JOIN `tabSerial and Batch Entry` sbe
			ON sbe.parent = psle.serial_and_batch_bundle
		WHERE psle.voucher_type = %s AND psle.voucher_no = %s
		  AND IFNULL(sbe.batch_no, '') != ''
		""",
		(voucher_type, voucher_no),
	)
	return {r[0] for r in named + from_bundle if r and r[0]}


def _get_batch_piece_allocation(sle_doc, signed_piece_qty):
	"""Return {batch_no: signed_pieces} for this SLE.

	Prefer Serial and Batch Entry.pieces when present; otherwise split the
	voucher piece qty by each batch's qty share.
	"""
	sign = 1 if flt(signed_piece_qty) >= 0 else -1

	if sle_doc.serial_and_batch_bundle:
		entries = frappe.get_all(
			"Serial and Batch Entry",
			filters={"parent": sle_doc.serial_and_batch_bundle},
			fields=["batch_no", "qty", "pieces"],
			order_by="idx asc",
		)
		sle_outward = flt(sle_doc.actual_qty) < 0
		directional = []
		for e in entries:
			if not e.batch_no:
				continue
			qty = flt(e.qty)
			if qty == 0:
				continue
			if sle_outward and qty > 0:
				continue
			if (not sle_outward) and qty < 0:
				continue
			directional.append(e)
		if not directional:
			directional = [e for e in entries if e.batch_no]

		if not directional:
			return {}

		with_pieces = {
			e.batch_no: abs(flt(e.pieces)) for e in directional if abs(flt(e.pieces)) > 0
		}
		if with_pieces:
			return {b: sign * p for b, p in with_pieces.items()}

		qty_shares = {e.batch_no: abs(flt(e.qty)) for e in directional}
		total = sum(qty_shares.values()) or 1
		batch_nos = list(qty_shares.keys())
		if len(batch_nos) == 1:
			return {batch_nos[0]: signed_piece_qty}

		allocated = []
		for batch_no in batch_nos:
			ratio = qty_shares[batch_no] / total
			allocated.append(round(signed_piece_qty * ratio))
		leftover = signed_piece_qty - sum(allocated)
		if allocated:
			allocated[0] += leftover
		return {b: q for b, q in zip(batch_nos, allocated) if q}

	if sle_doc.batch_no:
		return {sle_doc.batch_no: signed_piece_qty}

	return {}


def _create_piece_sle_row(sle_doc, batch_no, piece_qty):
	piece_doc = frappe.new_doc("Piece Stock Ledger Entry")
	piece_doc.update(
		{
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
		}
	)
	piece_doc.insert(ignore_permissions=True)


def recalculate_batch_pieces(batch_no):
	"""Recompute Batch.pieces from active Piece SLEs.

	Never changes average_length (Length Size).

	Includes:
	  - rows with batch_no set, and
	  - legacy null-batch rows whose Serial/Batch Bundle is single-batch
	    for this batch (old DN/SE bug).
	Excludes is_cancelled = 1 rows.
	"""
	if not batch_no:
		return

	total = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(psle.actual_qty), 0)
		FROM `tabPiece Stock Ledger Entry` psle
		WHERE psle.docstatus = 1
		  AND psle.is_cancelled = 0
		  AND (
			psle.batch_no = %s
			OR (
				IFNULL(psle.batch_no, '') = ''
				AND psle.serial_and_batch_bundle IS NOT NULL
				AND EXISTS (
					SELECT 1 FROM `tabSerial and Batch Entry` sbe
					WHERE sbe.parent = psle.serial_and_batch_bundle
					  AND sbe.batch_no = %s
				)
				AND (
					SELECT COUNT(DISTINCT sbe2.batch_no)
					FROM `tabSerial and Batch Entry` sbe2
					WHERE sbe2.parent = psle.serial_and_batch_bundle
					  AND IFNULL(sbe2.batch_no, '') != ''
				) = 1
			)
		  )
		""",
		(batch_no, batch_no),
	)[0][0]

	frappe.db.set_value("Batch", batch_no, "pieces", flt(total), update_modified=False)


def get_piece_qty(sle_doc):
	"""Fetch pieces from the voucher item row linked to this SLE."""
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

	return abs(piece_qty)


def update_batch_piece_on_sle(sle_doc, piece_qty):
	"""Backward-compatible wrapper — prefer recalculate_batch_pieces."""
	batch_no = sle_doc.batch_no
	if not batch_no and sle_doc.serial_and_batch_bundle:
		batch_no = frappe.db.get_value(
			"Serial and Batch Entry",
			{"parent": sle_doc.serial_and_batch_bundle},
			"batch_no",
		)
	recalculate_batch_pieces(batch_no)