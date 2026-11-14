# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

"""Team feedback 14 Nov 2026, item 2: Stock Transfer must not offer stock
reserved against a Sales Order until it is unreserved.

Rule: every open reservation in the source warehouse counts, whichever Sales
Order (or line) it is for. A Material Transfer takes the batch out of the
reservation's warehouse, which would orphan the reservation. Quality
Inspection → Finished Goods transfers are unaffected because finished goods
are not reserved in the Quality Inspection warehouse (Finish Work Order skips
the reservation for QI rows).
"""

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from madhav.madhav.doctype.stock_transfer import stock_transfer as st_module
from madhav.madhav.doctype.stock_transfer.stock_transfer import StockTransfer

MOD = "madhav.madhav.doctype.stock_transfer.stock_transfer"
WH = "Finished Goods RD - MUPL"


def _batch_row(batch_no, qty, pieces):
	return frappe._dict(
		batch_no=batch_no, item_code="FG000569", item_name="Angle", qty=qty, pieces=pieces,
		average_length=6.575, section_weight=16.58, reference_doctype="Stock Entry",
		reference_name="MAT-STE-1",
	)


def _sre_entry(batch_no, qty, delivered=0, pieces=0):
	return frappe._dict(batch_no=batch_no, qty=qty, delivered_qty=delivered, pieces=pieces)


class TestReservedStockSummary(FrappeTestCase):
	def test_open_qty_and_pieces_per_batch(self):
		out = st_module.summarize_reserved_batches([
			_sre_entry("B1", 1.0, 0, 10),
			_sre_entry("B1", 0.5, 0.25, 4),  # half delivered → 2 open pieces
			_sre_entry("B2", 2.0, 2.0, 20),  # fully delivered → ignored
		])
		self.assertAlmostEqual(out["B1"]["qty"], 1.25)
		self.assertAlmostEqual(out["B1"]["pieces"], 12)
		self.assertNotIn("B2", out)

	def test_free_limits(self):
		self.assertEqual(st_module.free_batch_limits(3.0, 30, {"qty": 1.25, "pieces": 12.4}), (1.75, 18))
		self.assertEqual(st_module.free_batch_limits(1.0, None, {"qty": 2.0}), (0, None))
		self.assertEqual(st_module.free_batch_limits(1.0, 10, None), (1.0, 10))


class TestFetchDetailsHidesReservedStock(FrappeTestCase):
	def _fetch(self, batch_rows, sre_rows):
		calls = []

		def sql(query, params=None, as_dict=False, **kw):
			calls.append(query)
			if "Stock Reservation Entry" in query:
				return sre_rows
			return batch_rows

		with patch.object(st_module.frappe.db, "sql", side_effect=sql):
			return st_module.get_batch_stock(source_warehouse=WH)

	def test_fully_reserved_batch_is_not_offered(self):
		rows = self._fetch([_batch_row("B1", 1.308, 12)], [_sre_entry("B1", 1.308, 0, 12)])
		self.assertEqual([r.batch_no for r in rows], [])

	def test_partly_reserved_batch_offers_only_the_free_part(self):
		rows = self._fetch(
			[_batch_row("B1", 3.0, 30), _batch_row("B2", 2.0, 20)],
			[_sre_entry("B1", 1.0, 0, 10)],
		)
		by = {r.batch_no: r for r in rows}
		self.assertAlmostEqual(by["B1"].qty, 2.0)
		self.assertEqual(by["B1"].pieces, 20)
		self.assertAlmostEqual(by["B1"].reserved_qty, 1.0)
		self.assertAlmostEqual(by["B2"].qty, 2.0)
		self.assertEqual(by["B2"].pieces, 20)

	def test_delivered_part_of_reservation_is_free_again(self):
		rows = self._fetch([_batch_row("B1", 1.0, 10)], [_sre_entry("B1", 1.0, 1.0, 10)])
		self.assertAlmostEqual(rows[0].qty, 1.0)


class TestSubmitBlocksReservedStock(FrappeTestCase):
	def _validate(self, qty, pieces, reserved):
		doc = StockTransfer.__new__(StockTransfer)
		doc.source_warehouse = WH
		doc.transfer_item = [SimpleNamespace(idx=1, item_code="FG000569", batch="B1",
			source_warehouse=WH, qty=qty, pieces=pieces)]
		with patch(f"{MOD}.get_live_batch_qty", return_value=3.0), \
			patch(f"{MOD}.get_live_batch_pieces", return_value=30), \
			patch(f"{MOD}.get_reserved_batch_map", return_value={"B1": reserved} if reserved else {}):
			doc.validate_transfer_item_limits()

	def test_qty_into_reserved_part_is_blocked(self):
		with self.assertRaises(frappe.ValidationError) as cm:
			self._validate(2.5, 10, {"qty": 1.0, "pieces": 10})
		self.assertIn("reserved against a Sales Order", str(cm.exception))

	def test_pieces_into_reserved_part_is_blocked(self):
		with self.assertRaises(frappe.ValidationError):
			self._validate(1.0, 25, {"qty": 1.0, "pieces": 10})

	def test_free_part_can_move(self):
		self._validate(2.0, 20, {"qty": 1.0, "pieces": 10})

	def test_unreserved_batch_unchanged(self):
		self._validate(3.0, 30, None)

	def test_transfer_batch_limits_api_returns_free_qty(self):
		with patch(f"{MOD}.get_live_batch_qty", return_value=3.0), \
			patch(f"{MOD}.get_live_batch_pieces", return_value=30), \
			patch(f"{MOD}.get_reserved_batch_map", return_value={"B1": {"qty": 1.0, "pieces": 10}}):
			out = st_module.get_transfer_batch_limits("FG000569", WH, "B1")
		self.assertEqual(out["qty"], 2.0)
		self.assertEqual(out["pieces"], 20)
