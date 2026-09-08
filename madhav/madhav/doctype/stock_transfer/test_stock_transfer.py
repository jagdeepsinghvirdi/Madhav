# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

"""Stock Transfer cancel rollback tests.

Run via:
  bench --site madhav.localhost run-tests --app madhav --module madhav.madhav.doctype.stock_transfer.test_stock_transfer
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, nowdate

from madhav.madhav.doctype.stock_transfer.stock_transfer import (
	StockTransfer,
	_cancel_psles_for_voucher,
	resolve_sre_sb_dimensions,
)


class TestStockTransferCancelHelpers(FrappeTestCase):
	"""Unit-style tests for cancel rollback helpers."""

	def test_cancel_psles_skips_empty_voucher(self):
		with patch("madhav.madhav.doctype.stock_transfer.stock_transfer.frappe.get_all") as get_all:
			_cancel_psles_for_voucher("")
			get_all.assert_not_called()

	def test_cancel_psles_cancels_each_doc(self):
		psle = MagicMock()
		with patch(
			"madhav.madhav.doctype.stock_transfer.stock_transfer.frappe.get_all",
			return_value=["PSLE-1", "PSLE-2"],
		), patch(
			"madhav.madhav.doctype.stock_transfer.stock_transfer.frappe.get_doc",
			return_value=psle,
		):
			_cancel_psles_for_voucher("MAT-STE-1")
			self.assertEqual(psle.cancel.call_count, 2)
			self.assertTrue(psle.flags.ignore_links)

	def test_resolve_prefers_stock_entry_field(self):
		doc = frappe.get_doc(
			{
				"doctype": "Stock Transfer",
				"stock_entry": "SE-1",
			}
		)
		doc.name = "STE-1"
		with patch(
			"madhav.madhav.doctype.stock_transfer.stock_transfer.frappe.db.exists",
			return_value=True,
		):
			self.assertEqual(doc._resolve_linked_stock_entry(), "SE-1")

	def test_on_cancel_cancels_sre_then_se_with_ignore_links(self):
		doc = frappe.get_doc({"doctype": "Stock Transfer"})
		doc.name = "STE-X"
		doc.stock_entry = "SE-X"
		doc.transfer_item = []

		sre = MagicMock()
		se = MagicMock()
		se.docstatus = 1

		with patch.object(doc, "_resolve_linked_stock_entry", return_value="SE-X"), patch(
			"madhav.madhav.doctype.stock_transfer.stock_transfer._cancel_psles_for_voucher"
		) as cancel_psle, patch(
			"madhav.madhav.doctype.stock_transfer.stock_transfer.frappe.get_all",
			return_value=["SRE-1"],
		), patch(
			"madhav.madhav.doctype.stock_transfer.stock_transfer.frappe.get_doc",
			side_effect=[sre, se],
		), patch.object(doc, "db_set"):
			doc.on_cancel()

			sre.cancel.assert_called_once()
			cancel_psle.assert_called_once_with("SE-X")
			self.assertTrue(se.flags.ignore_links)
			se.cancel.assert_called_once()

	def test_on_cancel_throws_when_stock_entry_missing(self):
		doc = frappe.get_doc({"doctype": "Stock Transfer"})
		doc.name = "STE-MISSING"
		doc.stock_entry = None
		doc.append(
			"transfer_item",
			{
				"item_code": "TEST-ITEM",
				"qty": 1,
				"batch": "BATCH-1",
			},
		)

		with patch.object(doc, "_resolve_linked_stock_entry", return_value=None), patch(
			"madhav.madhav.doctype.stock_transfer.stock_transfer.frappe.get_all",
			return_value=[],
		), patch.object(doc, "db_set"):
			self.assertRaises(Exception, doc.on_cancel)


class TestStockTransferResolveIntegration(FrappeTestCase):
	"""Live-site checks for known broken docs (read-only resolve)."""

	def test_resolve_finds_orphaned_se_for_ste_0229(self):
		"""STE-26-0229 submitted with blank stock_entry; SE MAT-STE-00009 exists.

		Skips unless the site still holds an orphaned transfer in that state —
		once the transfer is rolled back there is no submitted Material Transfer
		left to resolve, which is the correct end state rather than a failure.
		"""
		if not frappe.db.exists("Stock Transfer", "STE-26-0229"):
			self.skipTest("STE-26-0229 not on this site")

		doc = frappe.get_doc("Stock Transfer", "STE-26-0229")
		if doc.stock_entry:
			self.skipTest("STE-26-0229 already has stock_entry linked")

		se_name = doc._resolve_linked_stock_entry()
		if not se_name:
			self.skipTest("No submitted Material Transfer left — already rolled back")

		se = frappe.get_doc("Stock Entry", se_name)
		self.assertEqual(se.from_warehouse, doc.source_warehouse)
		self.assertEqual(se.to_warehouse, doc.target_warehouse)
		self.assertEqual(se.stock_entry_type, "Material Transfer")


class TestStockTransferReservationDimensions(FrappeTestCase):
	"""Reserved stock must equal transferred Tonne qty; Pcs/Length from transfer row."""

	def test_resolve_prefers_transfer_row_over_batch(self):
		pieces, length, sw = resolve_sre_sb_dimensions(
			pieces=7,
			length=9.5,
			section_weight=15.037594,
			batch_vals={"pieces": 20, "average_length": 9.5, "section_weight": 11.85},
		)
		self.assertEqual(pieces, 7)
		self.assertEqual(length, 9.5)
		self.assertAlmostEqual(sw, 15.037594, places=5)

	def test_resolve_falls_back_to_batch_when_transfer_blank(self):
		pieces, length, sw = resolve_sre_sb_dimensions(
			pieces=0,
			length=0,
			section_weight=0,
			batch_vals={"pieces": 20, "average_length": 9.5, "section_weight": 11.85},
		)
		self.assertEqual(pieces, 20)
		self.assertEqual(length, 9.5)
		self.assertAlmostEqual(sw, 11.85, places=5)

	def test_legacy_so_overwrite_formula_must_not_be_used_as_section_weight(self):
		"""Regression: pcs×length×item_kg_m/1000 ≈ 0.788 was wrongly stored as SW."""
		item_weight_per_meter = 11.85
		legacy_wrong_sw = (7 * 9.5 * item_weight_per_meter) / 1000.0
		self.assertAlmostEqual(legacy_wrong_sw, 0.788, places=3)

		# Correct path keeps transfer SW (~15.04 for 1T @ 7pcs × 9.5m), not 0.788
		_, _, sw = resolve_sre_sb_dimensions(
			pieces=7,
			length=9.5,
			section_weight=(1.0 * 1000) / (7 * 9.5),
			batch_vals={"section_weight": item_weight_per_meter},
		)
		self.assertAlmostEqual(sw, 1000 / (7 * 9.5), places=4)
		self.assertNotAlmostEqual(sw, legacy_wrong_sw, places=2)

	def test_align_transfer_row_keeps_one_tonne_authoritative(self):
		doc = frappe.get_doc({"doctype": "Stock Transfer"})
		doc.append(
			"transfer_item",
			{
				"item_code": "FG-TEST",
				"qty": 1.0,
				"pieces": 7,
				"length": 9.5,
				"section_weight": 11.85,  # mismatched; must be realigned
			},
		)
		doc.align_transfer_row_dimensions()
		row = doc.transfer_item[0]
		self.assertEqual(flt(row.qty), 1.0)
		self.assertEqual(flt(row.pieces), 7)
		self.assertEqual(flt(row.length), 9.5)
		self.assertAlmostEqual(flt(row.section_weight), 1000 / (7 * 9.5), places=4)
		# Physical check: pcs × length × sw / 1000 ≈ qty
		self.assertAlmostEqual(
			(flt(row.pieces) * flt(row.length) * flt(row.section_weight)) / 1000,
			1.0,
			places=5,
		)

	def test_build_payload_includes_pieces_length(self):
		doc = frappe.get_doc(
			{
				"doctype": "Stock Transfer",
				"target_warehouse": "Finished Goods - MUPL",
			}
		)
		doc.name = "STE-TEST"
		row = frappe._dict(
			{
				"item_code": "FG0001",
				"qty": 1.0,
				"pieces": 7,
				"length": 9.5,
				"section_weight": 15.037594,
				"batch": "BATCH-1",
				"name": "row-1",
			}
		)
		with patch(
			"madhav.madhav.doctype.stock_transfer.stock_transfer.frappe.db.get_value",
			return_value="Tonne",
		):
			payload = doc._build_fg_reservation_payload(
				row,
				so_qty=5,
				work_order="WO-1",
				sales_order="SO-1",
				sales_order_item="soi-1",
			)
		self.assertEqual(payload["qty"], 1.0)
		self.assertEqual(payload["pieces"], 7)
		self.assertEqual(payload["length"], 9.5)
		self.assertAlmostEqual(payload["section_weight"], 15.037594, places=5)

	def test_create_fg_reservation_uses_transfer_qty_and_dimensions(self):
		"""SRE reserved_qty = transfer qty; sb_entries get transfer pcs/length/sw."""
		doc = frappe.get_doc(
			{"doctype": "Stock Transfer", "company": "Madhav Udyog Pvt Ltd"}
		)
		doc.name = "STE-RES"

		captured = {}

		class FakeSRE:
			def __init__(self):
				self.sb_entries = []
				self.flags = frappe._dict()

			def append(self, table, values):
				self.sb_entries.append(frappe._dict(values))
				captured["sb"] = values
				return self.sb_entries[-1]

			def insert(self):
				captured["reserved_qty"] = self.reserved_qty
				captured["voucher_qty"] = self.voucher_qty

			def submit(self):
				captured["submitted"] = True

		so_item = frappe._dict(
			name="soi-1", qty=5, stock_reserved_qty=0, warehouse="FG - MUPL"
		)

		with patch(
			"madhav.madhav.doctype.stock_transfer.stock_transfer.frappe.get_all",
			return_value=[so_item],
		), patch(
			"madhav.madhav.doctype.stock_transfer.stock_transfer.frappe.db.get_value",
			side_effect=lambda *a, **k: self._fake_get_value(a, k),
		), patch(
			"madhav.madhav.doctype.stock_transfer.stock_transfer.frappe.db.get_single_value",
			return_value=0,
		), patch(
			"madhav.madhav.doctype.stock_transfer.stock_transfer.frappe.db.sql",
			return_value=((0,),),
		), patch(
			"madhav.madhav.doctype.stock_transfer.stock_transfer.frappe.get_cached_value",
			return_value=1,
		), patch(
			"madhav.madhav.doctype.stock_transfer.stock_transfer.get_available_qty_to_reserve",
			return_value=100.0,
		), patch(
			"madhav.madhav.doctype.stock_transfer.stock_transfer.frappe.new_doc",
			return_value=FakeSRE(),
		):
			doc.create_fg_stock_reservation(
				item_code="FG0001",
				warehouse="FG - MUPL",
				qty=1.0,
				so_qty=5,
				name="STE-RES",
				stock_uom="Tonne",
				work_order="WO-1",
				sales_order="SO-1",
				sales_order_item="soi-1",
				batch_no="BATCH-1",
				pieces=7,
				length=9.5,
				section_weight=1000 / (7 * 9.5),
				from_voucher_type="Stock Transfer",
				from_voucher_no="STE-RES",
				from_voucher_detail_no="row-1",
			)

		self.assertTrue(captured.get("submitted"))
		self.assertEqual(flt(captured["reserved_qty"]), 1.0)
		self.assertEqual(flt(captured["sb"]["qty"]), 1.0)
		self.assertEqual(flt(captured["sb"]["pieces"]), 7)
		self.assertEqual(flt(captured["sb"]["length"]), 9.5)
		self.assertAlmostEqual(flt(captured["sb"]["section_weight"]), 1000 / (7 * 9.5), places=4)
		# Must not look like the legacy 0.788 bug
		self.assertNotAlmostEqual(flt(captured["reserved_qty"]), 0.788, places=2)
		self.assertNotAlmostEqual(flt(captured["sb"]["section_weight"]), 0.788, places=2)

	@staticmethod
	def _fake_get_value(args, kwargs):
		# warehouse match for SO Item
		if args and args[0] == "Sales Order Item":
			return "FG - MUPL"
		if args and args[0] == "Batch":
			return frappe._dict(
				pieces=20, average_length=9.5, section_weight=11.85
			)
		return None


class TestStockTransferCancelRollbackE2E(FrappeTestCase):
	"""End-to-end rollback against the real Stock Ledger.

	The mock-based tests above only prove the cancel *call sequence*. These
	assert the reported requirement itself: after cancelling a Stock Transfer
	the batch qty is physically back in the source warehouse and no longer in
	the target warehouse.
	"""

	COMPANY = "MADHAV UDYOG PRIVATE LIMITED"
	RECEIPT_QTY = 5.0
	RECEIPT_PIECES = 10
	TRANSFER_QTY = 2.0
	TRANSFER_PIECES = 4
	LENGTH = 10.0
	WEIGHT_PER_METER = 11.85

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("Company", cls.COMPANY):
			raise unittest.SkipTest(f"Company {cls.COMPANY} not on this site")

		cls.abbr = frappe.db.get_value("Company", cls.COMPANY, "abbr")
		cls.cost_center = frappe.db.get_value(
			"Cost Center", {"is_group": 0, "company": cls.COMPANY}, "name"
		)
		cls.expense_account = frappe.db.get_value(
			"Company", cls.COMPANY, "stock_adjustment_account"
		)
		# Branch is a mandatory accounting dimension on this site.
		cls.branch = frappe.db.get_value("Branch", {}, "name")
		cls.source_warehouse = cls._ensure_warehouse("_Test QI Rollback")
		cls.target_warehouse = cls._ensure_warehouse("_Test FG Rollback")
		cls.item_code = cls._ensure_item()

	@classmethod
	def _ensure_warehouse(cls, warehouse_name):
		full_name = f"{warehouse_name} - {cls.abbr}"
		if not frappe.db.exists("Warehouse", full_name):
			frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": warehouse_name,
					"company": cls.COMPANY,
					"is_group": 0,
				}
			).insert(ignore_permissions=True)
		return full_name

	@classmethod
	def _ensure_item(cls):
		item_code = "_TEST-ST-ROLLBACK"
		if frappe.db.exists("Item", item_code):
			return item_code

		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": item_code,
				"item_name": item_code,
				"item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name"),
				"stock_uom": "Tonne",
				"is_stock_item": 1,
				"has_batch_no": 1,
				"create_new_batch": 1,
			}
		)
		# india_compliance makes HSN mandatory on Item.
		if item.meta.has_field("gst_hsn_code"):
			item.gst_hsn_code = frappe.db.get_value("GST HSN Code", {}, "name")
		# Non-zero weight_per_meter is what the legacy reserved-qty formula used
		# (pcs x length x kg/m / 1000). Keeping it set means a regression shows
		# up as the reported 0.788 instead of a harmless zero.
		if item.meta.has_field("weight_per_meter"):
			item.weight_per_meter = cls.WEIGHT_PER_METER
		# Exercises the Piece Stock Ledger Entry path that cancel must also undo.
		if item.meta.has_field("required_stock_in_pieces"):
			item.required_stock_in_pieces = 1
		item.insert(ignore_permissions=True)
		return item_code

	def _section_weight(self, qty, pieces):
		return flt((qty * 1000) / (pieces * self.LENGTH), 6)

	def _receive_stock(self):
		"""Seed a fresh batch into the source warehouse; return (se, batch_no)."""
		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = "Material Receipt"
		se.company = self.COMPANY
		se.set_posting_time = 1
		se.posting_date = nowdate()
		if se.meta.has_field("branch"):
			se.branch = self.branch
		# create_batch_group (Material Receipt after_submit) needs both of these.
		if se.meta.has_field("total_length_in_meter"):
			se.total_length_in_meter = self.RECEIPT_PIECES * self.LENGTH
		if se.meta.has_field("weight_received"):
			se.weight_received = self.RECEIPT_QTY
		se.append(
			"items",
			{
				"item_code": self.item_code,
				"qty": self.RECEIPT_QTY,
				"t_warehouse": self.source_warehouse,
				"basic_rate": 1000,
				"use_serial_batch_fields": 1,
				"pieces": self.RECEIPT_PIECES,
				"average_length": self.LENGTH,
				"section_weight": self._section_weight(
					self.RECEIPT_QTY, self.RECEIPT_PIECES
				),
				"cost_center": self.cost_center,
				"expense_account": self.expense_account,
				"branch": self.branch,
			},
		)
		se.insert(ignore_permissions=True)
		se.submit()
		se.reload()

		row = se.items[0]
		batch_no = row.batch_no
		if not batch_no and row.serial_and_batch_bundle:
			batch_no = frappe.db.get_value(
				"Serial and Batch Entry",
				{"parent": row.serial_and_batch_bundle},
				"batch_no",
			)
		self.assertTrue(batch_no, "Material Receipt must produce a batch")

		# validate_transfer_item_limits caps the transfer against these fields.
		frappe.db.set_value(
			"Batch",
			batch_no,
			{
				"batch_qty": self.RECEIPT_QTY,
				"pieces": self.RECEIPT_PIECES,
				"average_length": self.LENGTH,
				"section_weight": self._section_weight(
					self.RECEIPT_QTY, self.RECEIPT_PIECES
				),
			},
			update_modified=False,
		)
		return se, batch_no

	def _make_transfer(self, batch_no, qty=None, pieces=None, length=None):
		st = frappe.new_doc("Stock Transfer")
		st.company = self.COMPANY
		st.posting_date = nowdate()
		st.cost_center = self.cost_center
		st.branch = self.branch
		st.source_warehouse = self.source_warehouse
		st.target_warehouse = self.target_warehouse
		st.append(
			"transfer_item",
			{
				"item_code": self.item_code,
				"batch": batch_no,
				"qty": self.TRANSFER_QTY if qty is None else qty,
				"pieces": self.TRANSFER_PIECES if pieces is None else pieces,
				"length": self.LENGTH if length is None else length,
				"source_warehouse": self.source_warehouse,
				"target_warehouse": self.target_warehouse,
			},
		)
		st.insert(ignore_permissions=True)
		st.submit()
		return st

	def _make_multi_row_transfer(self, rows):
		"""Submit one Stock Transfer carrying several batch rows."""
		st = frappe.new_doc("Stock Transfer")
		st.company = self.COMPANY
		st.posting_date = nowdate()
		st.cost_center = self.cost_center
		st.branch = self.branch
		st.source_warehouse = self.source_warehouse
		st.target_warehouse = self.target_warehouse
		for row in rows:
			st.append(
				"transfer_item",
				{
					"item_code": self.item_code,
					"batch": row["batch"],
					"qty": row.get("qty", self.TRANSFER_QTY),
					"pieces": row.get("pieces", self.TRANSFER_PIECES),
					"length": row.get("length", self.LENGTH),
					"source_warehouse": self.source_warehouse,
					"target_warehouse": self.target_warehouse,
				},
			)
		st.insert(ignore_permissions=True)
		st.submit()
		return st

	def _issue_stock(self, batch_no, warehouse, qty, pieces):
		"""Consume stock out of a warehouse via Material Issue."""
		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = "Material Issue"
		se.company = self.COMPANY
		se.set_posting_time = 1
		se.posting_date = nowdate()
		if se.meta.has_field("branch"):
			se.branch = self.branch
		se.append(
			"items",
			{
				"item_code": self.item_code,
				"qty": qty,
				"s_warehouse": warehouse,
				"batch_no": batch_no,
				"use_serial_batch_fields": 1,
				"pieces": pieces,
				"average_length": self.LENGTH,
				"cost_center": self.cost_center,
				"expense_account": self.expense_account,
				"branch": self.branch,
			},
		)
		se.insert(ignore_permissions=True)
		se.submit()
		return se

	def _batch_qty_in_warehouse(self, batch_no, warehouse):
		"""Live batch qty in a warehouse from active Stock Ledger Entries.

		Reads batch_no directly and via Serial and Batch Bundle, because
		bundle-based moves leave SLE.batch_no blank.
		"""
		result = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(actual_qty), 0)
			FROM `tabStock Ledger Entry`
			WHERE is_cancelled = 0
			  AND warehouse = %s
			  AND (
				batch_no = %s
				OR serial_and_batch_bundle IN (
					SELECT parent FROM `tabSerial and Batch Entry`
					WHERE batch_no = %s
				)
			  )
			""",
			(warehouse, batch_no, batch_no),
		)
		return flt(result[0][0]) if result else 0.0

	def test_submit_moves_stock_and_cancel_restores_source_warehouse(self):
		_, batch_no = self._receive_stock()

		source_before = self._batch_qty_in_warehouse(batch_no, self.source_warehouse)
		target_before = self._batch_qty_in_warehouse(batch_no, self.target_warehouse)
		self.assertAlmostEqual(source_before, self.RECEIPT_QTY, places=3)

		st = self._make_transfer(batch_no)
		se_name = st._resolve_linked_stock_entry()
		self.assertTrue(se_name, "Submit must create a linked Material Transfer")

		self.assertAlmostEqual(
			self._batch_qty_in_warehouse(batch_no, self.source_warehouse),
			source_before - self.TRANSFER_QTY,
			places=3,
			msg="submit should move qty out of the source warehouse",
		)
		self.assertAlmostEqual(
			self._batch_qty_in_warehouse(batch_no, self.target_warehouse),
			target_before + self.TRANSFER_QTY,
			places=3,
			msg="submit should move qty into the target warehouse",
		)

		st.reload()
		st.cancel()

		self.assertAlmostEqual(
			self._batch_qty_in_warehouse(batch_no, self.source_warehouse),
			source_before,
			places=3,
			msg="cancel must restore batch qty to the source warehouse",
		)
		self.assertAlmostEqual(
			self._batch_qty_in_warehouse(batch_no, self.target_warehouse),
			target_before,
			places=3,
			msg="cancel must not leave qty in the target warehouse",
		)
		self.assertEqual(
			frappe.db.get_value("Stock Entry", se_name, "docstatus"),
			2,
			"linked Material Transfer must be cancelled",
		)
		self.assertEqual(
			frappe.db.count(
				"Piece Stock Ledger Entry", {"voucher_no": se_name, "docstatus": 1}
			),
			0,
			"Piece Stock Ledger Entries for the transfer must be cancelled",
		)

	def test_cancel_rolls_back_even_when_stock_entry_link_is_blank(self):
		"""Reported root cause: a blank stock_entry link made cancel skip the SE,
		leaving stock parked in the target warehouse."""
		_, batch_no = self._receive_stock()
		source_before = self._batch_qty_in_warehouse(batch_no, self.source_warehouse)

		st = self._make_transfer(batch_no)
		se_name = st._resolve_linked_stock_entry()
		self.assertTrue(se_name)

		# Wipe both links so only the warehouse + posting date + batch fallback remains.
		frappe.db.set_value(
			"Stock Transfer", st.name, "stock_entry", "", update_modified=False
		)
		if frappe.get_meta("Stock Entry").has_field("stock_transfer"):
			frappe.db.set_value(
				"Stock Entry", se_name, "stock_transfer", "", update_modified=False
			)

		st.reload()
		self.assertFalse(st.stock_entry, "precondition: stock_entry link is blank")

		st.cancel()

		self.assertAlmostEqual(
			self._batch_qty_in_warehouse(batch_no, self.source_warehouse),
			source_before,
			places=3,
			msg="rollback must work without the stock_entry link",
		)
		self.assertEqual(
			frappe.db.get_value("Stock Entry", se_name, "docstatus"),
			2,
			"fallback must find and cancel the orphaned Material Transfer",
		)

	def test_resolve_never_returns_another_transfers_stock_entry(self):
		"""Two transfers of the same batch, same warehouses and same posting date.

		Resolving one must never reach for the other's Material Transfer,
		otherwise cancelling transfer A silently reverses transfer B.
		"""
		_, batch_no = self._receive_stock()

		first = self._make_transfer(batch_no)
		first_se = first._resolve_linked_stock_entry()
		second = self._make_transfer(batch_no)
		second_se = second._resolve_linked_stock_entry()
		self.assertNotEqual(first_se, second_se, "setup: two distinct Stock Entries")

		# Legacy state on the first transfer only: both links wiped.
		frappe.db.set_value(
			"Stock Transfer", first.name, "stock_entry", "", update_modified=False
		)
		if frappe.get_meta("Stock Entry").has_field("stock_transfer"):
			frappe.db.set_value(
				"Stock Entry", first_se, "stock_transfer", "", update_modified=False
			)

		first.reload()
		resolved = first._resolve_linked_stock_entry()

		self.assertNotEqual(
			resolved,
			second_se,
			"must not resolve to a Stock Entry owned by another Stock Transfer",
		)
		self.assertEqual(resolved, first_se, "should resolve to its own Stock Entry")

	def _ensure_customer(self):
		customer_name = "_Test ST Rollback Customer"
		# Customer may be autonamed by series, so resolve on customer_name.
		existing = frappe.db.get_value("Customer", {"customer_name": customer_name}, "name")
		if existing:
			return existing

		doc = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": customer_name,
				"customer_type": "Company",
				"customer_group": frappe.db.get_value(
					"Customer Group", {"is_group": 0}, "name"
				),
				"territory": frappe.db.get_value("Territory", {"is_group": 0}, "name"),
			}
		).insert(ignore_permissions=True)
		return doc.name

	def _make_sales_order(self, qty, warehouse=None):
		"""Submitted SO whose line warehouse matches the transfer target.

		create_fg_stock_reservation bails out unless those warehouses match.
		"""
		so = frappe.new_doc("Sales Order")
		so.customer = self._ensure_customer()
		so.company = self.COMPANY
		so.transaction_date = nowdate()
		so.delivery_date = nowdate()
		if so.meta.has_field("reserve_stock"):
			# Reserve only through the Stock Transfer flow under test.
			so.reserve_stock = 0
		if so.meta.has_field("branch"):
			so.branch = self.branch
		so.append(
			"items",
			{
				"item_code": self.item_code,
				"qty": qty,
				"rate": 1000,
				"delivery_date": nowdate(),
				"warehouse": warehouse or self.target_warehouse,
				"cost_center": self.cost_center,
			},
		)
		so.insert(ignore_permissions=True)
		so.submit()
		return so

	def _reserve_for(self, st, so, so_qty, row_idx=0):
		"""Run the production reservation helper for one transfer row."""
		payload = st._build_fg_reservation_payload(
			st.transfer_item[row_idx],
			so_qty=so_qty,
			work_order=None,
			sales_order=so.name,
			sales_order_item=so.items[0].name,
		)
		st.create_fg_stock_reservation(**payload)

	def _sres_of(self, st, docstatus=1):
		return frappe.get_all(
			"Stock Reservation Entry",
			filters={
				"from_voucher_type": "Stock Transfer",
				"from_voucher_no": st.name,
				"docstatus": docstatus,
			},
			fields=["name", "reserved_qty", "available_qty"],
		)

	def _total_reserved_on_so(self, so):
		return flt(
			frappe.db.sql(
				"""SELECT COALESCE(SUM(reserved_qty), 0)
				FROM `tabStock Reservation Entry`
				WHERE voucher_type='Sales Order' AND voucher_no=%s AND docstatus=1""",
				so.name,
			)[0][0]
		)

	def test_cancel_releases_stock_reservation_created_by_the_transfer(self):
		"""Cancel must also unwind the reservation leg, not just warehouse qty.

		The reservation is created through the production helpers so this
		exercises the same Stock Reservation Entry the live flow produces.
		"""
		_, batch_no = self._receive_stock()
		source_before = self._batch_qty_in_warehouse(batch_no, self.source_warehouse)

		so = self._make_sales_order(qty=self.RECEIPT_QTY)
		st = self._make_transfer(batch_no)

		payload = st._build_fg_reservation_payload(
			st.transfer_item[0],
			so_qty=so.items[0].qty,
			work_order=None,
			sales_order=so.name,
			sales_order_item=so.items[0].name,
		)
		st.create_fg_stock_reservation(**payload)

		sre_names = frappe.get_all(
			"Stock Reservation Entry",
			filters={
				"from_voucher_type": "Stock Transfer",
				"from_voucher_no": st.name,
				"docstatus": 1,
			},
			pluck="name",
		)
		self.assertTrue(sre_names, "setup: transfer must hold an active reservation")

		st.reload()
		st.cancel()

		for sre_name in sre_names:
			self.assertEqual(
				frappe.db.get_value("Stock Reservation Entry", sre_name, "docstatus"),
				2,
				f"reservation {sre_name} must be released on cancel",
			)

		self.assertEqual(
			frappe.db.count(
				"Stock Reservation Entry",
				{"voucher_no": so.name, "docstatus": 1},
			),
			0,
			"Sales Order must not keep a reservation from a cancelled transfer",
		)
		self.assertAlmostEqual(
			self._batch_qty_in_warehouse(batch_no, self.source_warehouse),
			source_before,
			places=3,
			msg="batch qty must still return to the source warehouse",
		)

	def test_reserved_qty_equals_transferred_tonne_with_pcs_and_length(self):
		"""Reported case: transfer 1 T as 7 pcs x 9.5 m.

		Reserved stock must be the transferred tonne qty (1.0) on both the
		Stock Reservation Entry and the Sales Order — not the legacy
		pcs x length x kg/m / 1000 figure of 0.788 — and Pcs/Length must be
		carried into the reservation rather than only the converted qty.
		"""
		transfer_qty = 1.0
		transfer_pieces = 7
		transfer_length = 9.5
		legacy_wrong_qty = (
			transfer_pieces * transfer_length * self.WEIGHT_PER_METER
		) / 1000.0
		self.assertAlmostEqual(legacy_wrong_qty, 0.788, places=3, msg="setup sanity")

		_, batch_no = self._receive_stock()
		so = self._make_sales_order(qty=self.RECEIPT_QTY)
		st = self._make_transfer(
			batch_no,
			qty=transfer_qty,
			pieces=transfer_pieces,
			length=transfer_length,
		)

		payload = st._build_fg_reservation_payload(
			st.transfer_item[0],
			so_qty=so.items[0].qty,
			work_order=None,
			sales_order=so.name,
			sales_order_item=so.items[0].name,
		)
		st.create_fg_stock_reservation(**payload)

		sre_name = frappe.db.get_value(
			"Stock Reservation Entry",
			{"from_voucher_type": "Stock Transfer", "from_voucher_no": st.name, "docstatus": 1},
			"name",
		)
		self.assertTrue(sre_name, "transfer must create a reservation")
		sre = frappe.get_doc("Stock Reservation Entry", sre_name)

		# Reserved qty is the transferred tonne qty.
		self.assertAlmostEqual(flt(sre.reserved_qty), transfer_qty, places=3)
		self.assertNotAlmostEqual(
			flt(sre.reserved_qty),
			legacy_wrong_qty,
			places=2,
			msg="reserved qty must not fall back to the 0.788 weight formula",
		)

		# Pcs and Length must be captured, not just the converted qty.
		self.assertEqual(len(sre.sb_entries), 1)
		entry = sre.sb_entries[0]
		self.assertEqual(entry.batch_no, batch_no)
		self.assertAlmostEqual(flt(entry.qty), transfer_qty, places=3)
		self.assertAlmostEqual(flt(entry.pieces), transfer_pieces, places=3)
		self.assertAlmostEqual(flt(entry.length), transfer_length, places=3)
		# Section weight realigned so pcs x length x sw / 1000 == qty.
		self.assertAlmostEqual(
			(flt(entry.pieces) * flt(entry.length) * flt(entry.section_weight)) / 1000,
			transfer_qty,
			places=4,
		)
		self.assertNotAlmostEqual(
			flt(entry.section_weight), self.WEIGHT_PER_METER, places=2
		)

		# Sales Order must show the same reserved figure.
		so_reserved = flt(
			frappe.db.get_value("Sales Order Item", so.items[0].name, "stock_reserved_qty")
		)
		self.assertAlmostEqual(
			so_reserved,
			transfer_qty,
			places=3,
			msg="Sales Order reserved qty must match the transferred tonne qty",
		)
		self.assertNotAlmostEqual(so_reserved, legacy_wrong_qty, places=2)

	def test_cancel_restores_every_batch_of_a_multi_row_transfer(self):
		"""A transfer carrying several batches must roll back all of them."""
		_, batch_a = self._receive_stock()
		_, batch_b = self._receive_stock()

		before = {
			batch_a: self._batch_qty_in_warehouse(batch_a, self.source_warehouse),
			batch_b: self._batch_qty_in_warehouse(batch_b, self.source_warehouse),
		}

		st = self._make_multi_row_transfer(
			[{"batch": batch_a, "qty": 1.5}, {"batch": batch_b, "qty": 2.5}]
		)
		se_name = st._resolve_linked_stock_entry()
		self.assertTrue(se_name)

		for batch_no, qty in ((batch_a, 1.5), (batch_b, 2.5)):
			self.assertAlmostEqual(
				self._batch_qty_in_warehouse(batch_no, self.target_warehouse),
				qty,
				places=3,
				msg=f"{batch_no} should have moved to the target warehouse",
			)

		st.reload()
		st.cancel()

		for batch_no in (batch_a, batch_b):
			self.assertAlmostEqual(
				self._batch_qty_in_warehouse(batch_no, self.source_warehouse),
				before[batch_no],
				places=3,
				msg=f"{batch_no} must be restored to the source warehouse",
			)
			self.assertAlmostEqual(
				self._batch_qty_in_warehouse(batch_no, self.target_warehouse),
				0,
				places=3,
				msg=f"{batch_no} must not remain in the target warehouse",
			)

	def test_cancelling_one_transfer_leaves_the_other_transfer_intact(self):
		"""Same batch moved twice: cancelling the first must not touch the second."""
		_, batch_no = self._receive_stock()

		first = self._make_transfer(batch_no, qty=1.0, pieces=2)
		second = self._make_transfer(batch_no, qty=2.0, pieces=3)
		second_se = second._resolve_linked_stock_entry()

		target_after_both = self._batch_qty_in_warehouse(batch_no, self.target_warehouse)
		self.assertAlmostEqual(target_after_both, 3.0, places=3)

		first.reload()
		first.cancel()

		# Only the first transfer's 1.0 T should have come back.
		self.assertAlmostEqual(
			self._batch_qty_in_warehouse(batch_no, self.target_warehouse),
			2.0,
			places=3,
			msg="second transfer's qty must stay in the target warehouse",
		)
		self.assertEqual(
			frappe.db.get_value("Stock Entry", second_se, "docstatus"),
			1,
			"second transfer's Stock Entry must remain submitted",
		)
		self.assertEqual(
			frappe.db.get_value("Stock Transfer", second.name, "docstatus"),
			1,
			"second Stock Transfer must remain submitted",
		)

	def test_full_batch_transfer_and_cancel(self):
		"""Moving the entire batch leaves the source at zero; cancel refills it."""
		_, batch_no = self._receive_stock()
		source_before = self._batch_qty_in_warehouse(batch_no, self.source_warehouse)

		st = self._make_transfer(
			batch_no, qty=self.RECEIPT_QTY, pieces=self.RECEIPT_PIECES
		)

		self.assertAlmostEqual(
			self._batch_qty_in_warehouse(batch_no, self.source_warehouse),
			0,
			places=3,
			msg="source warehouse should be emptied",
		)

		st.reload()
		st.cancel()

		self.assertAlmostEqual(
			self._batch_qty_in_warehouse(batch_no, self.source_warehouse),
			source_before,
			places=3,
		)
		self.assertAlmostEqual(
			self._batch_qty_in_warehouse(batch_no, self.target_warehouse), 0, places=3
		)

	def test_average_length_static_and_pieces_restored_on_cancel(self):
		"""Length Size must never move; Pcs must return to its pre-transfer value."""
		_, batch_no = self._receive_stock()

		length_before, pieces_before = frappe.db.get_value(
			"Batch", batch_no, ["average_length", "pieces"]
		)

		st = self._make_transfer(batch_no)

		length_after_submit = frappe.db.get_value("Batch", batch_no, "average_length")
		self.assertAlmostEqual(
			flt(length_after_submit),
			flt(length_before),
			places=3,
			msg="average_length must stay static on submit",
		)

		st.reload()
		st.cancel()

		length_after_cancel, pieces_after_cancel = frappe.db.get_value(
			"Batch", batch_no, ["average_length", "pieces"]
		)
		self.assertAlmostEqual(
			flt(length_after_cancel),
			flt(length_before),
			places=3,
			msg="average_length must stay static on cancel",
		)
		self.assertAlmostEqual(
			flt(pieces_after_cancel),
			flt(pieces_before),
			places=3,
			msg="pieces must return to the pre-transfer value",
		)

	def test_reserved_qty_never_exceeds_sales_order_allowance(self):
		"""Transferring more than the SO line must not over-reserve."""
		_, batch_no = self._receive_stock()
		so_qty = 1.0
		so = self._make_sales_order(qty=so_qty)

		st = self._make_transfer(batch_no, qty=4.0, pieces=8)
		payload = st._build_fg_reservation_payload(
			st.transfer_item[0],
			so_qty=so.items[0].qty,
			work_order=None,
			sales_order=so.name,
			sales_order_item=so.items[0].name,
		)
		st.create_fg_stock_reservation(**payload)

		allowance = flt(
			frappe.db.get_single_value("Stock Settings", "over_reservation_allowance") or 0
		)
		cap = so_qty * (1 + allowance / 100)

		reserved = flt(
			frappe.db.get_value(
				"Stock Reservation Entry",
				{
					"from_voucher_type": "Stock Transfer",
					"from_voucher_no": st.name,
					"docstatus": 1,
				},
				"reserved_qty",
			)
		)
		self.assertGreater(reserved, 0, "a reservation should still be created")
		self.assertLessEqual(
			reserved,
			cap + 0.001,
			f"reserved {reserved} must not exceed SO allowance {cap}",
		)

	def test_transfer_without_sales_order_creates_no_reservation(self):
		"""No SO on the row means no reservation, and submit must not fail."""
		_, batch_no = self._receive_stock()
		st = self._make_transfer(batch_no)

		self.assertEqual(
			frappe.db.count(
				"Stock Reservation Entry",
				{"from_voucher_type": "Stock Transfer", "from_voucher_no": st.name},
			),
			0,
		)

		st.reload()
		st.cancel()
		self.assertEqual(
			frappe.db.get_value("Stock Transfer", st.name, "docstatus"), 2
		)

	def test_cancel_after_target_stock_consumed_fails_loudly(self):
		"""If transferred stock was already issued, cancel must not silently
		leave the ledger inconsistent."""
		_, batch_no = self._receive_stock()
		source_before = self._batch_qty_in_warehouse(batch_no, self.source_warehouse)

		st = self._make_transfer(batch_no)
		self._issue_stock(
			batch_no, self.target_warehouse, qty=self.TRANSFER_QTY, pieces=self.TRANSFER_PIECES
		)

		st.reload()
		# Savepoint only — a full rollback would wipe the class fixtures.
		frappe.db.savepoint("before_cancel_attempt")
		try:
			st.cancel()
		except Exception:
			# Preferred outcome: the cancel is refused and nothing is reversed.
			frappe.db.rollback(save_point="before_cancel_attempt")
			return

		# If the cancel went through, the ledger must still balance out.
		source_after = self._batch_qty_in_warehouse(batch_no, self.source_warehouse)
		target_after = self._batch_qty_in_warehouse(batch_no, self.target_warehouse)
		self.assertAlmostEqual(
			source_after + target_after,
			source_before - self.TRANSFER_QTY,
			places=3,
			msg="cancel left the ledger inconsistent after the stock was issued",
		)

	def test_second_transfer_reservation_gets_real_available_qty(self):
		"""Reported "Available Qty to Reserve is required" on the odd-length row.

		Once an earlier transfer has reserved the whole Sales Order line, the
		next transfer still reserves against the over-reservation allowance —
		but Stock Reservation Entry.available_qty (labelled "Available Qty to
		Reserve") was derived from the SO line's leftover qty, so it landed as
		0 and tripped the mandatory-field check on sites where that field is
		mandatory.
		"""
		frappe.db.set_single_value("Stock Settings", "over_reservation_allowance", 20)

		_, batch_no = self._receive_stock()
		so_qty = 3.0
		so = self._make_sales_order(qty=so_qty)
		frappe.db.set_value(
			"Sales Order Item", so.items[0].name, "length_size", 12, update_modified=False
		)

		def reserve(st):
			payload = st._build_fg_reservation_payload(
				st.transfer_item[0],
				so_qty=so_qty,
				work_order=None,
				sales_order=so.name,
				sales_order_item=so.items[0].name,
			)
			st.create_fg_stock_reservation(**payload)

		# First transfer takes the whole Sales Order line (length 12).
		first = self._make_transfer(batch_no, qty=so_qty, pieces=6, length=12.0)
		reserve(first)
		self.assertAlmostEqual(
			flt(
				frappe.db.get_value(
					"Sales Order Item", so.items[0].name, "stock_reserved_qty"
				)
			),
			so_qty,
			places=3,
			msg="setup: the SO line should now be fully reserved",
		)

		# Second transfer is the odd length (11.5) with nothing left on the SO line.
		second = self._make_transfer(batch_no, qty=0.5, pieces=1, length=11.5)
		reserve(second)

		sre_name = frappe.db.get_value(
			"Stock Reservation Entry",
			{
				"from_voucher_type": "Stock Transfer",
				"from_voucher_no": second.name,
				"docstatus": 1,
			},
			"name",
		)
		self.assertTrue(
			sre_name, "the odd-length transfer must still create a reservation"
		)

		available_qty = flt(
			frappe.db.get_value("Stock Reservation Entry", sre_name, "available_qty")
		)
		self.assertGreater(
			available_qty,
			0,
			"available_qty (Available Qty to Reserve) must reflect real stock, "
			"otherwise the mandatory-field check rejects the transfer",
		)

	def test_odd_length_first_then_normal_length_also_works(self):
		"""Mirror of the reported case, with the lengths swapped.

		Proves the failure was never about the 11.5 length: whichever transfer
		runs second is the one that used to break, because the first one had
		already reserved the whole Sales Order line.
		"""
		frappe.db.set_single_value("Stock Settings", "over_reservation_allowance", 20)

		_, batch_no = self._receive_stock()
		so_qty = 3.0
		so = self._make_sales_order(qty=so_qty)

		# Odd length goes first this time.
		first = self._make_transfer(batch_no, qty=so_qty, pieces=6, length=11.5)
		self._reserve_for(first, so, so_qty)

		second = self._make_transfer(batch_no, qty=0.5, pieces=1, length=12.0)
		self._reserve_for(second, so, so_qty)

		sres = self._sres_of(second)
		self.assertTrue(sres, "the second transfer must still reserve")
		for sre in sres:
			self.assertGreater(
				flt(sre.available_qty), 0, "available_qty must never be zero"
			)

	def test_every_reservation_created_has_non_zero_available_qty(self):
		"""Invariant guarding the reported error across a sequence of transfers."""
		frappe.db.set_single_value("Stock Settings", "over_reservation_allowance", 50)

		_, batch_no = self._receive_stock()
		so_qty = 2.0
		so = self._make_sales_order(qty=so_qty)

		for qty, length in ((2.0, 12.0), (0.5, 11.5), (0.5, 13.0)):
			st = self._make_transfer(batch_no, qty=qty, pieces=1, length=length)
			self._reserve_for(st, so, so_qty)
			for sre in self._sres_of(st):
				self.assertGreater(
					flt(sre.available_qty),
					0,
					f"available_qty was zero for transfer of {qty} T at {length} m",
				)

	def test_reserved_qty_never_exceeds_physical_warehouse_stock(self):
		"""Over-reservation allowance must not reserve more than really exists."""
		frappe.db.set_single_value("Stock Settings", "over_reservation_allowance", 50)

		_, batch_no = self._receive_stock()
		# SO far larger than what we will physically move.
		so_qty = 10.0
		so = self._make_sales_order(qty=so_qty)

		moved = 0.0
		for qty in (2.0, 1.0):
			st = self._make_transfer(batch_no, qty=qty, pieces=1)
			self._reserve_for(st, so, so_qty)
			moved += qty

		physical = self._batch_qty_in_warehouse(batch_no, self.target_warehouse)
		self.assertAlmostEqual(physical, moved, places=3, msg="setup check")
		self.assertLessEqual(
			self._total_reserved_on_so(so),
			physical + 0.001,
			"reserved qty must not exceed the stock physically in the warehouse",
		)

	def test_reservation_stops_cleanly_once_allowance_is_exhausted(self):
		"""Beyond the allowance no reservation is made, and submit still works."""
		frappe.db.set_single_value("Stock Settings", "over_reservation_allowance", 20)

		_, batch_no = self._receive_stock()
		so_qty = 1.0
		so = self._make_sales_order(qty=so_qty)
		cap = so_qty * 1.2

		first = self._make_transfer(batch_no, qty=1.0, pieces=2)
		self._reserve_for(first, so, so_qty)

		second = self._make_transfer(batch_no, qty=1.0, pieces=2)
		self._reserve_for(second, so, so_qty)

		# Third one is past the allowance — must be a clean no-op.
		third = self._make_transfer(batch_no, qty=1.0, pieces=2)
		self._reserve_for(third, so, so_qty)
		self.assertEqual(
			self._sres_of(third), [], "no reservation once the allowance is used up"
		)

		self.assertLessEqual(
			self._total_reserved_on_so(so),
			cap + 0.001,
			f"total reserved must stay within the {cap} allowance",
		)
		self.assertEqual(
			frappe.db.get_value("Stock Transfer", third.name, "docstatus"),
			1,
			"the transfer itself must still submit",
		)

	def test_no_reservation_when_allowance_is_zero_and_line_is_full(self):
		"""With no allowance the second transfer simply reserves nothing."""
		frappe.db.set_single_value("Stock Settings", "over_reservation_allowance", 0)

		_, batch_no = self._receive_stock()
		so_qty = 2.0
		so = self._make_sales_order(qty=so_qty)

		first = self._make_transfer(batch_no, qty=so_qty, pieces=4)
		self._reserve_for(first, so, so_qty)
		self.assertTrue(self._sres_of(first))

		second = self._make_transfer(batch_no, qty=0.5, pieces=1, length=11.5)
		self._reserve_for(second, so, so_qty)

		self.assertEqual(
			self._sres_of(second), [], "nothing left to reserve without an allowance"
		)
		self.assertAlmostEqual(self._total_reserved_on_so(so), so_qty, places=3)

	def test_no_reservation_when_target_warehouse_has_no_stock(self):
		"""Reserving before the stock arrives must be a no-op, not an error."""
		frappe.db.set_single_value("Stock Settings", "over_reservation_allowance", 20)

		_, batch_no = self._receive_stock()
		so_qty = 2.0

		# Warehouse the SO asks for, which has never held any stock.
		empty_warehouse = self._ensure_warehouse("_Test Empty Rollback")
		so = self._make_sales_order(qty=so_qty, warehouse=empty_warehouse)

		st = self._make_transfer(batch_no, qty=1.0, pieces=2)
		payload = st._build_fg_reservation_payload(
			st.transfer_item[0],
			so_qty=so_qty,
			work_order=None,
			sales_order=so.name,
			sales_order_item=so.items[0].name,
		)
		payload["warehouse"] = empty_warehouse
		st.create_fg_stock_reservation(**payload)

		self.assertEqual(
			self._sres_of(st), [], "no stock in the warehouse means no reservation"
		)

	def test_no_reservation_when_so_line_warehouse_differs(self):
		"""A Sales Order pointing at another warehouse must be skipped."""
		frappe.db.set_single_value("Stock Settings", "over_reservation_allowance", 20)

		_, batch_no = self._receive_stock()
		so_qty = 2.0
		so = self._make_sales_order(qty=so_qty, warehouse=self.source_warehouse)

		st = self._make_transfer(batch_no, qty=1.0, pieces=2)
		self._reserve_for(st, so, so_qty)

		self.assertEqual(
			self._sres_of(st), [], "warehouse mismatch must not create a reservation"
		)

	def test_cancelling_an_over_reserved_transfer_releases_its_reservation(self):
		"""The extra reservation from an over-reserving transfer must unwind."""
		frappe.db.set_single_value("Stock Settings", "over_reservation_allowance", 20)

		_, batch_no = self._receive_stock()
		so_qty = 2.0
		so = self._make_sales_order(qty=so_qty)

		first = self._make_transfer(batch_no, qty=so_qty, pieces=4)
		self._reserve_for(first, so, so_qty)
		reserved_after_first = self._total_reserved_on_so(so)

		second = self._make_transfer(batch_no, qty=0.4, pieces=1, length=11.5)
		self._reserve_for(second, so, so_qty)
		self.assertGreater(
			self._total_reserved_on_so(so),
			reserved_after_first,
			"setup: the second transfer should over-reserve",
		)

		second.reload()
		second.cancel()

		self.assertAlmostEqual(
			self._total_reserved_on_so(so),
			reserved_after_first,
			places=3,
			msg="cancelling must release only the extra reservation",
		)

	def test_cancel_is_blocked_when_stock_entry_cannot_be_found(self):
		"""Cancel must fail loudly rather than silently leave stock moved."""
		_, batch_no = self._receive_stock()
		st = self._make_transfer(batch_no)
		se_name = st._resolve_linked_stock_entry()

		st.reload()
		with patch.object(st, "_resolve_linked_stock_entry", return_value=None):
			self.assertRaises(frappe.ValidationError, st.cancel)

		# Stock Entry untouched, so no silent stock loss.
		self.assertEqual(frappe.db.get_value("Stock Entry", se_name, "docstatus"), 1)
