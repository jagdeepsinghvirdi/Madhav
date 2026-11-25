# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

"""Team feedback 4 (26 Nov 2026): Delivery Note, Production Plan, reservation tool.

1. DCA/26-27/1583: Invoice Qty 0.520 on a 0.510 reservation. The batch's other
   stock is reserved for other orders, so the extra 0.010 is a shortfall the
   Stock Reconciliation tops up; it must not come from those orders.
2. DCA/26-27/1588: typing Qty 11.642 turned 63 pieces into 58. A typed Qty is
   the weight of the same bars.
5. SO MU-SO26-00626 / DCA/26-27/1592: all 3 bars of a reservation delivered at
   0.98 of 1.000 t left 0.02 t reserved with no bar; the Production Plan then
   offered 18.94 pieces and the reservation tool showed 3 reserved pieces.
"""

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from madhav.doc_events import delivery_note as dn



def _only(marker, rows_for):
	"""db.sql side effect: fake rows for queries containing ``marker``, the real database otherwise.

	Mocking every query lets unrelated lookups (site defaults while a message is
	built) read the fake rows and cache them.
	"""
	real = frappe.db.sql

	def side_effect(query, *a, **k):
		if marker in str(query):
			return rows_for(query)
		return real(query, *a, **k)

	return side_effect
from madhav.doc_events.test_round4_dn import _bundle, _eight_metre_row, _rebuild

MOD = "madhav.doc_events.delivery_note"
FG = "Finished Goods RD - MUPL"


class TestInvoiceQtyAboveReservedOnSharedBatch(FrappeTestCase):
	def _available(self, reserved, live=3.425, own_line="LINE-A", claimed=None, note_lines=None):
		"""``reserved``: {Sales Order line: open qty reserved on the batch}."""
		row = frappe._dict(item_code="FG003901", warehouse=FG, batch_no="MUBT-15060",
			serial_and_batch_bundle=None, so_detail=own_line, against_sales_order="SO-A")
		rows = [frappe._dict(batch_no="MUBT-15060", voucher_detail_no=l, qty=q) for l, q in reserved.items()]
		with patch(f"{MOD}._has_live_batch_ledger", return_value=True), \
			patch(f"{MOD}.get_batch_qty_from_sle", return_value=live), \
			patch.object(dn.frappe.db, "sql", side_effect=_only("tabStock Reservation Entry", lambda q: rows)):
			return dn.get_available_qty_for_item(row, already_claimed=claimed, note_lines=note_lines)

	def test_stock_reserved_for_other_orders_is_not_available(self):
		# Team's batch: 3.425 t, 2.915 t reserved for other orders, 0.510 for this line.
		out = self._available({"LINE-A": 0.51, "LINE-X": 2.0, "LINE-Y": 0.915})
		self.assertAlmostEqual(out, 0.51, places=6)

	def test_invoice_qty_above_it_is_a_shortfall(self):
		out = self._available({"LINE-A": 0.51, "LINE-X": 2.915})
		self.assertAlmostEqual(round(0.52 - out, 3), 0.01)

	def test_batch_with_no_other_reservation_is_unchanged(self):
		self.assertAlmostEqual(self._available({"LINE-A": 0.51}), 3.425)

	def test_two_lines_on_one_note_do_not_count_each_other(self):
		# Batch 2.0: line A and line B reserve 1.0 each; both are on this note.
		# Row B after row A took 1.0 has 1.0 left - not 0 (which would top up a
		# whole tonne that is not missing).
		out = self._available({"LINE-A": 1.0, "LINE-B": 1.0}, live=2.0, own_line="LINE-B",
			claimed={("FG003901", FG, "MUBT-15060"): 1.0}, note_lines={"LINE-A", "LINE-B"})
		self.assertAlmostEqual(out, 1.0)


class TestTopUpOnBatchReservedForOthers(FrappeTestCase):
	KEY = ("FG003901", FG, "MUBT-15060")

	def _sr(self, dn_ref="DCA/26-27/1583", qty=3.435, current=3.425):
		from madhav.madhav.override.stock_reconciliation import StockReconciliation

		sr = StockReconciliation.__new__(StockReconciliation)
		sr.flags = frappe._dict()
		sr._action = "submit"
		sr.items = [frappe._dict(item_code=self.KEY[0], warehouse=self.KEY[1], batch_no=self.KEY[2],
			qty=qty, current_qty=current, delivery_note_ref=dn_ref)]
		return sr

	def _run(self, sr, reserved=2.915):
		rows = [frappe._dict(item_code=self.KEY[0], warehouse=self.KEY[1], batch_no=self.KEY[2],
			sales_orders="MU-SO26-00534", reserved_qty=reserved)]
		with patch.object(frappe.db, "sql", side_effect=_only("tabStock Reservation Entry", lambda q: rows)):
			sr.validate_reserved_stock()

	def test_delivery_note_top_up_passes(self):
		self._run(self._sr())

	def test_manual_reconciliation_keeps_strict_rule(self):
		with self.assertRaises(frappe.ValidationError):
			self._run(self._sr(dn_ref=None))

	def test_reconciliation_that_lowers_the_batch_is_still_blocked(self):
		with self.assertRaises(frappe.ValidationError):
			self._run(self._sr(qty=3.0, current=3.425))


class TestTypedQtyKeepsBars(FrappeTestCase):
	def test_team_example_63_pieces_stay_63(self):
		bundle = _bundle([("MUBT-13577", 63, 12.839)])
		item = _eight_metre_row(qty=11.642, stock_qty=11.642, pieces=63,
			custom_reserved_batch_pool='[{"batch_no": "MUBT-13577"}]')
		pool = [frappe._dict(batch_no="MUBT-13577", qty=12.839, pieces=63, length=11, section_weight=16.8,
			warehouse=FG, batch_date="2026-11-01", creation="2026-11-01")]
		_rebuild(item, bundle, pool=pool)
		self.assertEqual([(e.batch_no, e.pieces, -e.qty) for e in bundle.entries], [("MUBT-13577", 63, 11.642)])
		self.assertEqual(item.pieces, 63)
		self.assertAlmostEqual(item.qty, 11.642)

	def test_more_weight_than_reserved_needs_free_stock(self):
		bundle = _bundle([("B-001", 10, 2.0), ("B-002", 5, 1.0)])
		item = _eight_metre_row(qty=3.6, stock_qty=3.6)
		with self.assertRaises(frappe.ValidationError):
			_rebuild(item, bundle, cap=1.0)  # B-002 would need 1.2 with only 1.0 reserved, no free stock

	def test_changed_pieces_still_retake_bars(self):
		bundle = _bundle([("B-001", 10, 2.0), ("B-002", 5, 1.0)])
		item = _eight_metre_row(pieces=12)
		_rebuild(item, bundle)
		self.assertEqual([(e.batch_no, e.pieces) for e in bundle.entries], [("B-001", 10), ("B-002", 2)])


class TestLeftoverReleasedWhenAllBarsDelivered(FrappeTestCase):
	def _names(self, sres, delivered):
		def rows_for(query):
			return delivered if "tabDelivery Note Item" in query else sres

		with patch.object(dn.frappe.db, "sql", side_effect=_only("tabSerial and Batch Entry", rows_for)):
			return dn._sres_with_all_bars_delivered("46lp1u9n97")

	def _team_sres(self):
		# MU-SO26-00626: SRE-04080 delivered, SRE-04081 3 bars, 0.98 of 1.000 delivered.
		return [
			frappe._dict(name="SRE-04080", warehouse=FG, batch_no="MUBT-12471", qty=1.0, delivered_qty=1.0,
				pieces=3, status="Delivered"),
			frappe._dict(name="SRE-04081", warehouse="Finished Goods - MUPL", batch_no="MUBT-14648", qty=1.0,
				delivered_qty=0.98, pieces=3, status="Partially Delivered"),
		]

	def test_team_case_leftover_is_released(self):
		delivered = [frappe._dict(warehouse=FG, batch_no="MUBT-12471", pieces=3),
			frappe._dict(warehouse="Finished Goods - MUPL", batch_no="MUBT-14648", pieces=3)]
		self.assertEqual(self._names(self._team_sres(), delivered), ["SRE-04081"])

	def test_bars_still_to_deliver_keep_the_reservation(self):
		delivered = [frappe._dict(warehouse="Finished Goods - MUPL", batch_no="MUBT-14648", pieces=2)]
		self.assertEqual(self._names(self._team_sres(), delivered), [])

	def test_submit_releases_it_and_records_it_for_cancel(self):
		doc = SimpleNamespace(name="DCA/26-27/1592", is_return=0, items=[frappe._dict(so_detail="46lp1u9n97")])
		with patch(f"{MOD}._stuck_sres_for_so_line", return_value=[]), \
			patch(f"{MOD}._sres_with_all_bars_delivered", return_value=["SRE-04081"]), \
			patch(f"{MOD}._release_leftover_on_sre", return_value={"sre": "SRE-04081", "reserved_qty": 1.0, "entries": []}) as rel, \
			patch.object(dn.frappe, "get_doc") as get_doc:
			dn.release_fully_delivered_so_reservations(doc)
		rel.assert_called_once_with("SRE-04081")
		self.assertIn("SRE-04081", get_doc.call_args.args[0]["content"])


class TestWholeBars(FrappeTestCase):
	def test_production_plan_offers_whole_bars(self):
		from madhav.madhav.override import production_plan as pp

		with patch.object(pp, "get_effective_planned", return_value=frappe._dict(pieces=0)), \
			patch.object(pp, "get_delivered_pieces", return_value=6), \
			patch.object(pp, "get_already_reserved_pieces", return_value=0.06), \
			patch.object(pp, "get_bwrt_delivered_pieces", return_value=0), \
			patch.object(pp, "production_open_pieces", return_value=0):
			self.assertEqual(pp.get_remaining_pieces_for_new_production_plan("46lp1u9n97", 25, 0), 19)

	def test_reservation_tool_counts_undelivered_bars(self):
		from madhav.madhav.doctype.batch_wise_reservation_tool import batch_wise_reservation_tool as bwrt

		seen = []

		def rows_for(query):
			seen.append(query)
			return [frappe._dict(sales_order_item="L1", pieces=2.6)]

		with patch.object(bwrt.frappe.db, "sql", side_effect=_only("tabStock Reservation Entry", rows_for)):
			self.assertEqual(bwrt.get_reserved_pieces_map(["L1"]), {"L1": 3})
		self.assertIn("delivered_qty", seen[0])
