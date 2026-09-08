"""Stock Reconciliation submit hooks (separate module so workers pick them up
without a full server restart when serve(..., no_reload=True))."""

import frappe
from frappe.utils import flt


def after_submit(self, method=None):
	"""Run after Server Scripts so DN auto-SR cannot leave Batch length/pieces wrong.

	Site Server Script "Update Batch details" copies SR pieces/average_length
	onto Batch. DN auto-SR often has average_length=0, which wipes Length Size
	and wrongly sets Length/Pieces to the delivered count.

	- DN-linked rows: restore Length Size if wiped; never trust SR pieces.
	- Manual SR: allow non-empty length / pieces updates only.
	"""
	dn_batches = set()

	for row in self.items:
		if not row.batch_no:
			continue

		if row.get("delivery_note_ref"):
			dn_batches.add(row.batch_no)
			_restore_batch_length_if_wiped(row.batch_no, row)
			continue

		updates = {}
		if row.pieces is not None:
			updates["pieces"] = row.pieces
		if row.get("assorted_length"):
			updates["assorted_length"] = row.assorted_length
		if flt(row.get("average_length")):
			updates["average_length"] = flt(row.average_length)

		if updates:
			frappe.db.set_value("Batch", row.batch_no, updates, update_modified=False)

	# Pieces for DN batches are finalized when DN Piece SLEs post (often after
	# this SR, since SR is created in DN before_submit). Still recalculate here
	# so a later standalone SR cancel/re-submit stays consistent.
	if dn_batches:
		from madhav.doc_events.stock_ledger_entry import recalculate_batch_pieces

		for batch_no in dn_batches:
			recalculate_batch_pieces(batch_no)


def _restore_batch_length_if_wiped(batch_no, sr_row=None):
	"""If Batch.average_length was zeroed, restore from SR/bundle/Batch history."""
	if flt(frappe.db.get_value("Batch", batch_no, "average_length")):
		return

	length = 0
	if sr_row:
		length = flt(getattr(sr_row, "length", 0)) or flt(
			getattr(sr_row, "average_length", 0)
		)

	if not length:
		row = frappe.db.sql(
			"""
			SELECT length FROM `tabSerial and Batch Entry`
			WHERE batch_no = %s AND IFNULL(length, 0) > 0
			ORDER BY modified DESC LIMIT 1
			""",
			batch_no,
		)
		length = flt(row[0][0]) if row else 0

	if not length:
		row = frappe.db.sql(
			"""
			SELECT average_length FROM `tabStock Entry Detail`
			WHERE batch_no = %s AND IFNULL(average_length, 0) > 0
			ORDER BY modified DESC LIMIT 1
			""",
			batch_no,
		)
		length = flt(row[0][0]) if row else 0

	if length:
		frappe.db.set_value(
			"Batch", batch_no, "average_length", length, update_modified=False
		)
