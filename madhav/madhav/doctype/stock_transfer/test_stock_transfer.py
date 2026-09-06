# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

"""Stock Transfer cancel rollback tests.

Run via:
  bench --site madhav.localhost run-tests --app madhav --module madhav.madhav.doctype.stock_transfer.test_stock_transfer
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

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
		"""STE-26-0229 submitted with blank stock_entry; SE MAT-STE-00009 exists."""
		if not frappe.db.exists("Stock Transfer", "STE-26-0229"):
			self.skipTest("STE-26-0229 not on this site")

		doc = frappe.get_doc("Stock Transfer", "STE-26-0229")
		if doc.stock_entry:
			self.skipTest("STE-26-0229 already has stock_entry linked")

		se_name = doc._resolve_linked_stock_entry()
		self.assertTrue(se_name, "Should find Material Transfer via batch fallback")
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
