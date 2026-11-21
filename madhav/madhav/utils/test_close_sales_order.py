"""Team feedback 3 (21 Nov 2026), item 5: closing a Sales Order with reserved stock.

"On clicking Close, the remaining reserved pending quantity should
automatically unreserve and move to the For Mill (EXTRA) warehouse. The Sales
Order status should change to Closed. If the Sales Order is reopened, it
should work normally as before."
"""
import unittest
from unittest.mock import MagicMock, patch

import frappe

from madhav.madhav.utils import close_sales_order as cso

MOD = "madhav.madhav.utils.close_sales_order"
FG, MILL = "Finished Goods RD - MUPL", "For Mill (EXTRA) - MUPL"


def _sre(name, reserved, delivered=0, based_on="Serial and Batch", warehouse=FG, item="FG000873"):
	return frappe._dict(name=name, item_code=item, warehouse=warehouse, reserved_qty=reserved,
		delivered_qty=delivered, reservation_based_on=based_on)


def _entry(batch, qty, pieces, delivered=0, warehouse=FG):
	return frappe._dict(batch_no=batch, qty=qty, delivered_qty=delivered, pieces=pieces, length=8.43,
		section_weight=19.72, warehouse=warehouse)


SO = frappe._dict(name="MU-SO26-00999", company="MADHAV UDYOG PRIVATE LIMITED", branch="Rolling Division",
	cost_center="Rolling Division - MUPL")


class TestPlan(unittest.TestCase):
	def _plan(self, sres, entries, live=99.0):
		with patch(f"{MOD}.for_mill_warehouse", return_value=MILL), \
			patch(f"{MOD}._open_reservations", return_value=sres), \
			patch.object(cso.frappe, "get_all", side_effect=lambda dt, **kw: entries.get(kw["filters"]["parent"], [])), \
			patch(f"{MOD}._live_batch_qty", side_effect=lambda i, w, b: live):
			return cso.plan_release(SO)

	def test_open_batches_move_with_their_open_pieces(self):
		plan = self._plan([_sre("SRE-1", 3.655, delivered=1.0)], {"SRE-1": [_entry("B-1", 3.655, 22, delivered=1.0)]})
		self.assertEqual(len(plan.moves), 1)
		m = plan.moves[0]
		self.assertEqual((m.batch_no, m.warehouse, m.qty, m.pieces), ("B-1", FG, 2.655, 16))
		self.assertEqual(plan.reservations[0].open_qty, 2.655)

	def test_stock_already_in_for_mill_is_only_unreserved(self):
		plan = self._plan([_sre("SRE-1", 2.0, warehouse=MILL)], {"SRE-1": [_entry("B-1", 2.0, 10, warehouse=MILL)]})
		self.assertEqual(plan.moves, [])
		self.assertEqual(len(plan.reservations), 1)

	def test_never_moves_more_than_the_batch_holds(self):
		plan = self._plan([_sre("SRE-1", 2.0)], {"SRE-1": [_entry("B-1", 2.0, 10)]}, live=1.5)
		self.assertEqual((plan.moves[0].qty, plan.moves[0].pieces), (1.5, 8))

	def test_qty_reservation_is_unreserved_not_moved(self):
		plan = self._plan([_sre("SRE-1", 2.0, based_on="Qty")], {})
		self.assertEqual(plan.moves, [])
		self.assertEqual(plan.unreserve_only[0].qty, 2.0)

	def test_delivered_reservation_is_left_alone(self):
		plan = self._plan([_sre("SRE-1", 2.0, delivered=2.0)], {"SRE-1": [_entry("B-1", 2.0, 10, delivered=2.0)]})
		self.assertEqual((plan.reservations, plan.moves), ([], []))


class TestClose(unittest.TestCase):
	def _close(self, plan):
		sre_docs = {}

		def get_doc(*a, **k):
			if a and a[0] == "Stock Reservation Entry":
				return sre_docs.setdefault(a[1], MagicMock(flags=frappe._dict()))
			return MagicMock()

		with patch(f"{MOD}.plan_release", return_value=plan), \
			patch.object(cso.frappe, "get_doc", side_effect=get_doc), \
			patch("madhav.doc_events.delivery_note._release_leftover_on_sre") as leftover, \
			patch(f"{MOD}._move_to_for_mill", return_value="MUST26-1") as move, \
			patch.object(cso.frappe, "msgprint"):
			se = cso.release_and_move_on_close(SO)
		return se, sre_docs, leftover, move

	def test_undelivered_is_cancelled_partly_delivered_is_closed_at_delivered(self):
		plan = frappe._dict(target=MILL, unreserve_only=[],
			reservations=[frappe._dict(_sre("SRE-1", 2.0), open_qty=2.0), frappe._dict(_sre("SRE-2", 3.0, 1.0), open_qty=2.0)],
			moves=[frappe._dict(item_code="FG000873", warehouse=FG, batch_no="B-1", qty=4.0, pieces=20, length=8.43, section_weight=19.72)])
		se, docs, leftover, move = self._close(plan)
		self.assertEqual(se, "MUST26-1")
		docs["SRE-1"].cancel.assert_called_once()
		self.assertNotIn("SRE-2", docs)
		leftover.assert_called_once_with("SRE-2")
		move.assert_called_once()

	def test_nothing_reserved_closes_as_usual(self):
		se, docs, leftover, move = self._close(frappe._dict(target=MILL, reservations=[], moves=[], unreserve_only=[]))
		self.assertIsNone(se)
		move.assert_not_called()

	def test_close_no_longer_refuses(self):
		"""ERPNext's own check threw "Unreserve it before closing"."""
		import madhav.hooks  # noqa: F401  (applies the patch, as the web workers do)
		from erpnext.selling.doctype.sales_order.sales_order import SalesOrder

		self.assertIs(SalesOrder.validate_no_reserved_stock, cso.validate_no_reserved_stock)


class TestClosedOrderGetsNoNewReservation(unittest.TestCase):
	def test_closed(self):
		with patch.object(cso.frappe.db, "get_value", return_value="Closed"), patch.object(cso.frappe, "msgprint") as msg:
			self.assertTrue(cso.skip_reservation_for_closed_order("SO-1", "FG000873"))
		msg.assert_called_once()

	def test_open_or_reopened(self):
		with patch.object(cso.frappe.db, "get_value", return_value="To Deliver and Bill"):
			self.assertFalse(cso.skip_reservation_for_closed_order("SO-1", "FG000873"))
