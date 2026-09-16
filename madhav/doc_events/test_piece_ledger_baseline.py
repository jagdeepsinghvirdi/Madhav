"""Piece ledger baseline must not double-count legacy null-batch rows.

Client case (For Mill EXTRA): manufacture PSLE has blank batch_no; DN submit
used to seed a second Batch-voucher +N baseline, so Batch.pieces inflated
(7+7-4=10) and cancel restored above original (14).
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from madhav.doc_events.stock_ledger_entry import (
	_batch_has_piece_ledger_history,
	_ensure_baseline_piece_sle,
	_has_legacy_null_batch_psle,
	recalculate_batch_pieces,
)


class TestPieceLedgerBaseline(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_legacy_null_batch_blocks_baseline_seed(self):
		batch_no = "TEST-LEGACY-BASELINE-BATCH"
		item_code = "TEST-LEGACY-BASELINE-ITEM"
		warehouse = "Stores - _TC"
		company = "_Test Company"

		if not frappe.db.exists("Item", item_code):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": item_code,
					"item_group": "All Item Groups",
					"stock_uom": "Nos",
					"is_stock_item": 1,
					"required_stock_in_pieces": 1,
				}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("Batch", batch_no):
			frappe.get_doc(
				{
					"doctype": "Batch",
					"batch_id": batch_no,
					"item": item_code,
					"pieces": 7,
				}
			).insert(ignore_permissions=True)
		else:
			frappe.db.set_value("Batch", batch_no, "pieces", 7, update_modified=False)

		sabb = frappe.get_doc(
			{
				"doctype": "Serial and Batch Bundle",
				"item_code": item_code,
				"warehouse": warehouse,
				"company": company,
				"type_of_transaction": "Inward",
				"voucher_type": "Stock Entry",
				"voucher_no": "TEST-STE-LEGACY-BASELINE",
				"entries": [{"batch_no": batch_no, "qty": 1.795, "pieces": 7}],
			}
		)
		sabb.flags.ignore_validate = True
		sabb.flags.ignore_permissions = True
		sabb.insert()

		# Legacy manufacture-style PSLE: blank batch_no, linked via SABB only.
		frappe.get_doc(
			{
				"doctype": "Piece Stock Ledger Entry",
				"posting_date": frappe.utils.nowdate(),
				"posting_time": "00:00:00",
				"item_code": item_code,
				"warehouse": warehouse,
				"voucher_type": "Stock Entry",
				"voucher_no": "TEST-STE-LEGACY-BASELINE",
				"serial_and_batch_bundle": sabb.name,
				"actual_qty": 7,
				"company": company,
				"unit_of_measure": "Piece",
				"is_cancelled": 0,
				"batch_no": "",
				"docstatus": 1,
			}
		).insert(ignore_permissions=True)

		self.assertTrue(_has_legacy_null_batch_psle(batch_no))
		self.assertTrue(_batch_has_piece_ledger_history(batch_no))

		_ensure_baseline_piece_sle(
			item_code, warehouse, batch_no, company, "Delivery Note", "TEST-DN-LEGACY"
		)

		baselines = frappe.db.get_all(
			"Piece Stock Ledger Entry",
			filters={
				"batch_no": batch_no,
				"voucher_type": "Batch",
				"is_cancelled": 0,
			},
		)
		self.assertEqual(baselines, [], "must not seed baseline over legacy null-batch PSLE")

		# Outward DN row with batch_no set — remaining pieces must be 3, not 10.
		frappe.get_doc(
			{
				"doctype": "Piece Stock Ledger Entry",
				"posting_date": frappe.utils.nowdate(),
				"posting_time": "12:00:00",
				"item_code": item_code,
				"warehouse": warehouse,
				"voucher_type": "Delivery Note",
				"voucher_no": "TEST-DN-LEGACY",
				"actual_qty": -4,
				"company": company,
				"unit_of_measure": "Piece",
				"is_cancelled": 0,
				"batch_no": batch_no,
				"docstatus": 1,
			}
		).insert(ignore_permissions=True)

		# Pre-fix corruption: an erroneous baseline already on the ledger.
		frappe.get_doc(
			{
				"doctype": "Piece Stock Ledger Entry",
				"posting_date": frappe.utils.nowdate(),
				"posting_time": "00:00:00",
				"item_code": item_code,
				"warehouse": warehouse,
				"voucher_type": "Batch",
				"voucher_no": batch_no,
				"actual_qty": 7,
				"company": company,
				"unit_of_measure": "Piece",
				"is_cancelled": 0,
				"batch_no": batch_no,
				"docstatus": 1,
			}
		).insert(ignore_permissions=True)

		recalculate_batch_pieces(batch_no)
		self.assertEqual(flt(frappe.db.get_value("Batch", batch_no, "pieces")), 3)

		# Cancel DN → pieces back to original 7, not 14.
		frappe.db.sql(
			"""
			UPDATE `tabPiece Stock Ledger Entry`
			SET is_cancelled = 1
			WHERE voucher_type = 'Delivery Note' AND voucher_no = 'TEST-DN-LEGACY'
			"""
		)
		recalculate_batch_pieces(batch_no)
		self.assertEqual(flt(frappe.db.get_value("Batch", batch_no, "pieces")), 7)

	def test_dn_cancel_sync_finds_batches_when_item_sabb_cleared(self):
		"""After DN cancel, item batch_no/SABB are blank — sync must still
		heal pieces via Piece SLE voucher rows (Mill EXTRA path)."""
		from madhav.doc_events.delivery_note import _batches_touched_by_delivery_note

		batch_no = "TEST-DN-CANCEL-SYNC-BATCH"
		item_code = "TEST-DN-CANCEL-SYNC-ITEM"
		company = "_Test Company"
		dn_name = "TEST-DN-CANCEL-SYNC"

		if not frappe.db.exists("Item", item_code):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": item_code,
					"item_group": "All Item Groups",
					"stock_uom": "Nos",
					"is_stock_item": 1,
					"required_stock_in_pieces": 1,
				}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("Batch", batch_no):
			frappe.get_doc(
				{
					"doctype": "Batch",
					"batch_id": batch_no,
					"item": item_code,
					"pieces": 10,
				}
			).insert(ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "Piece Stock Ledger Entry",
				"posting_date": frappe.utils.nowdate(),
				"posting_time": "12:00:00",
				"item_code": item_code,
				"warehouse": "Stores - _TC",
				"voucher_type": "Delivery Note",
				"voucher_no": dn_name,
				"actual_qty": -10,
				"company": company,
				"unit_of_measure": "Piece",
				"is_cancelled": 1,
				"batch_no": batch_no,
				"docstatus": 1,
			}
		).insert(ignore_permissions=True)

		doc = frappe._dict(
			name=dn_name,
			doctype="Delivery Note",
			items=[frappe._dict(batch_no=None, serial_and_batch_bundle=None)],
		)
		self.assertIn(batch_no, _batches_touched_by_delivery_note(doc))
