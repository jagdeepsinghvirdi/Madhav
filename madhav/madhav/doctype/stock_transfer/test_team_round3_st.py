# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

"""Team feedback 14 Nov 2026, item 2 (rule as decided by Harsh, 14 Nov):
Stock Transfer does not offer stock reserved for ANOTHER Sales Order line.
Stock reserved for the batch's own line stays available; its reservation is
released on submit, reserved again in the target warehouse for the same line,
and given back if the transfer is cancelled. Quality Inspection → Finished
Goods transfers are unaffected (nothing is reserved in QI warehouses).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
			source_warehouse=WH, qty=qty, pieces=pieces, source_document_type=None, source_document_name=None)]
		with patch(f"{MOD}.get_live_batch_qty", return_value=3.0), \
			patch(f"{MOD}.get_live_batch_pieces", return_value=30), \
			patch(f"{MOD}.get_reserved_batch_map", return_value={"B1": reserved} if reserved else {}):
			doc.validate_transfer_item_limits()

	def test_qty_into_reserved_part_is_blocked(self):
		with self.assertRaises(frappe.ValidationError) as cm:
			self._validate(2.5, 10, {"qty": 1.0, "pieces": 10})
		self.assertIn("reserved against another Sales Order line", str(cm.exception))

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


# --- Harsh, 14 Nov 2026: stock reserved for the SAME order line stays available ---
class TestOwnLineReservationIsAvailable(FrappeTestCase):
	def _map(self, own_lines):
		rows = [
			frappe._dict(batch_no="B1", voucher_detail_no="LINE-A", qty=1.0, delivered_qty=0, pieces=10),
			frappe._dict(batch_no="B1", voucher_detail_no="LINE-B", qty=0.5, delivered_qty=0, pieces=5),
		]
		with patch.object(st_module.frappe.db, "sql", return_value=rows):
			return st_module.get_reserved_batch_map(WH, ["B1"], own_lines)

	def test_other_lines_still_count(self):
		self.assertAlmostEqual(self._map({"B1": "LINE-A"})["B1"]["qty"], 0.5)

	def test_without_own_line_everything_counts(self):
		self.assertAlmostEqual(self._map(None)["B1"]["qty"], 1.5)

	def test_save_allows_moving_own_line_reserved_stock(self):
		doc = StockTransfer.__new__(StockTransfer)
		doc.source_warehouse = WH
		doc.transfer_item = [SimpleNamespace(idx=1, item_code="FG000569", batch="B1", source_warehouse=WH,
			qty=1.0, pieces=10, source_document_type="Stock Entry", source_document_name="MUST-1")]
		seen = {}

		def reserved_map(warehouse, batches, own_lines=None):
			seen["own"] = own_lines
			return {} if own_lines and own_lines.get("B1") == "LINE-A" else {"B1": {"qty": 1.0, "pieces": 10}}

		with patch(f"{MOD}.get_live_batch_qty", return_value=1.0), \
			patch(f"{MOD}.get_live_batch_pieces", return_value=10), \
			patch(f"{MOD}.source_sales_order_line", return_value="LINE-A"), \
			patch(f"{MOD}.get_reserved_batch_map", side_effect=reserved_map):
			doc.validate_transfer_item_limits()
		self.assertEqual(seen["own"], {"B1": "LINE-A"})


class FakeSRE(SimpleNamespace):
	def cancel(self):
		self.docstatus = 2


class TestReleaseAndRestore(FrappeTestCase):
	def _doc(self, qty):
		doc = StockTransfer.__new__(StockTransfer)
		doc.doctype, doc.name, doc.source_warehouse = "Stock Transfer", "STE-TEST", WH
		doc.transfer_item = [SimpleNamespace(name="row1", idx=1, item_code="FG000569", batch="B1", source_warehouse=WH,
			qty=qty, pieces=10, source_document_type="Stock Entry", source_document_name="MUST-1")]
		return doc

	def _release(self, qty, sre):
		entry = frappe._dict(name=sre.name, qty=1.0, delivered_qty=0, pieces=10, length=6.5, section_weight=16)
		comments = []
		with patch(f"{MOD}.source_sales_order_line", return_value="LINE-A"), \
			patch.object(st_module.frappe.db, "sql", return_value=[entry]), \
			patch.object(st_module.frappe, "get_doc", side_effect=lambda *a, **k: comments.append(a[0]) or MagicMock() if isinstance(a[0], dict) else sre), \
			patch.object(st_module.frappe, "msgprint"), \
			patch("madhav.madhav.doctype.party_transfer.party_transfer.trim_reservation") as trim:
			released = self._doc(qty).release_own_line_reservations()
		return released, trim, comments

	def test_full_move_cancels_the_reservation(self):
		sre = FakeSRE(name="SRE-1", reserved_qty=1.0, delivered_qty=0, docstatus=1, flags=frappe._dict())
		released, trim, comments = self._release(1.0, sre)
		self.assertEqual(sre.docstatus, 2)
		trim.assert_not_called()
		self.assertEqual(released[0]["qty"], 1.0)
		self.assertTrue(comments and comments[0]["content"].startswith(st_module.RELEASED_SRE_PREFIX))

	def test_part_move_trims_the_reservation(self):
		sre = FakeSRE(name="SRE-1", reserved_qty=1.0, delivered_qty=0, docstatus=1, flags=frappe._dict())
		released, trim, _c = self._release(0.4, sre)
		self.assertEqual(sre.docstatus, 1)
		trim.assert_called_once()
		self.assertEqual(trim.call_args[0][1:3], ("B1", 0.4))
		self.assertEqual(released[0]["pieces"], 4)

	def test_cancel_gives_back_trimmed_and_cancelled_reservations(self):
		import json as _json
		recs = [
			{"sre": "SRE-1", "batch_no": "B1", "qty": 0.4, "pieces": 4, "warehouse": WH, "length": 6.5, "section_weight": 16, "cancelled": 0},
			{"sre": "SRE-2", "batch_no": "B2", "qty": 1.0, "pieces": 10, "warehouse": WH, "length": 6.5, "section_weight": 16, "cancelled": 1},
		]
		docs = {"SRE-1": FakeSRE(name="SRE-1", docstatus=1), "SRE-2": FakeSRE(name="SRE-2", docstatus=2)}
		with patch.object(st_module.frappe.db, "get_value", return_value=st_module.RELEASED_SRE_PREFIX + _json.dumps(recs)), \
			patch.object(st_module.frappe, "get_doc", side_effect=lambda dt, n: docs[n]), \
			patch("madhav.madhav.doctype.party_transfer.party_transfer.trim_reservation") as trim, \
			patch("madhav.madhav.doctype.party_transfer.party_transfer.recreate_reservation") as recreate:
			errors = self._doc(1.0).restore_released_reservations()
		self.assertEqual(errors, [])
		trim.assert_called_once_with(docs["SRE-1"], "B1", -0.4, -4)
		self.assertEqual(recreate.call_args[0][1].transfer_qty, 1.0)


class FakeNewSRE(SimpleNamespace):
	def set(self, f, v):
		setattr(self, f, list(v))

	def append(self, f, v):
		getattr(self, f).append(v)

	def insert(self):
		self.inserted = True

	def submit(self):
		self.submitted = True


class TestReservationMovesToTarget(FrappeTestCase):
	def _run(self, reserved_rows):
		doc = StockTransfer.__new__(StockTransfer)
		doc.doctype, doc.name, doc.target_warehouse = "Stock Transfer", "STE-TEST", "For Mill (EXTRA) - MUPL"
		doc.transfer_item = [SimpleNamespace(name="row1", target_warehouse=None)]
		doc._fg_reservation_data = [{"from_voucher_detail_no": r} for r in reserved_rows]
		doc._released_reservations = [{"sre": "SRE-1", "row": "row1", "batch_no": "B1", "qty": 1.308, "pieces": 12, "length": 6.575, "section_weight": 16.58}]
		new = FakeNewSRE(item_code="FG000569", reservation_based_on="Serial and Batch", sb_entries=[], flags=frappe._dict())
		with patch.object(st_module.frappe, "get_doc", return_value=SimpleNamespace(name="SRE-1")), \
			patch.object(st_module.frappe, "copy_doc", return_value=new), \
			patch(f"{MOD}.get_available_qty_to_reserve", return_value=5):
			doc.carry_released_reservations()
		return new

	def test_reserved_again_in_target_for_same_line(self):
		new = self._run([])
		self.assertTrue(getattr(new, "submitted", False))
		self.assertEqual(new.warehouse, "For Mill (EXTRA) - MUPL")
		self.assertEqual((new.from_voucher_type, new.from_voucher_no), ("Stock Transfer", "STE-TEST"))
		self.assertEqual(new.sb_entries[0]["qty"], 1.308)
		self.assertEqual(new.sb_entries[0]["warehouse"], "For Mill (EXTRA) - MUPL")

	def test_not_twice_when_sales_order_warehouse_rule_reserved_it(self):
		new = self._run(["row1"])
		self.assertFalse(getattr(new, "submitted", False))
