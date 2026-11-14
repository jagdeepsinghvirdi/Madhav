# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

"""Item 7 (14 Nov 2026): Sales Order Item.work_order_qty must follow the line's
own Work Orders, also after Finish Work Order changes a Work Order's qty.

Production example: MU-SO26-00502 line 1me2cvj69u stored 1.8 while its only
Work Order MFG-WO-2026-04829 had been raised to 2.67 by Finish Work Order.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from frappe.tests.utils import FrappeTestCase

from madhav.madhav.override import work_order as wo_module
from madhav.madhav.doctype.finish_work_order import finish_work_order as fwo_module
from madhav.madhav.doctype.finish_work_order.finish_work_order import FinishWorkOrder

WO = "madhav.madhav.override.work_order"
FWO = "madhav.madhav.doctype.finish_work_order.finish_work_order"


class TestLineWorkOrderQty(FrappeTestCase):
	def _qty(self, sql_total, bundle=False, item="FG001441"):
		db = MagicMock()
		db.get_value.return_value = item
		db.exists.return_value = bundle
		db.sql.return_value = [[sql_total]]
		with patch.object(wo_module.frappe, "db", db):
			out = wo_module.line_work_order_qty("1me2cvj69u")
		return out, db

	def test_counts_only_this_line(self):
		out, db = self._qty(2.67)
		self.assertEqual(out, 2.67)
		query, params = db.sql.call_args[0]
		self.assertIn("sales_order_item = %s", query)
		self.assertNotIn("sales_order = %s", query)
		self.assertIn("production_plan_sub_assembly_item", query)
		self.assertIn("least(produced_qty, qty)", query)
		self.assertEqual(params, ("1me2cvj69u", "FG001441"))

	def test_bundle_line_is_left_alone(self):
		out, _db = self._qty(5, bundle=True)
		self.assertIsNone(out)

	def test_recount_writes_qty_and_pieces(self):
		with patch(f"{WO}.line_work_order_qty", return_value=2.67), \
			patch.object(wo_module.frappe.db, "set_value") as set_value, \
			patch("madhav.doc_events.work_order.recompute_work_order_pieces") as pieces:
			wo_module.recount_line_work_order_qty("1me2cvj69u")
		set_value.assert_called_once_with("Sales Order Item", "1me2cvj69u", "work_order_qty", 2.67, update_modified=False)
		pieces.assert_called_once_with("1me2cvj69u")


class TestFinishWorkOrderRecountsLine(FrappeTestCase):
	def test_raising_wo_qty_recounts_the_order_line(self):
		db = MagicMock()

		def get_value(doctype, name, fields, *a, **kw):
			if fields == "sales_order_item":
				return "1me2cvj69u"
			return SimpleNamespace(qty=1.8, produced_qty=0)

		db.get_value.side_effect = get_value
		with patch.object(fwo_module.frappe, "db", db), \
			patch(f"{WO}.recount_line_work_order_qty") as recount:
			fwo_module.ensure_work_order_covers_fg("MFG-WO-2026-04829", 2.67)
		db.set_value.assert_called_once_with("Work Order", "MFG-WO-2026-04829", "qty", 2.67, update_modified=False)
		recount.assert_called_once_with("1me2cvj69u")

	def test_no_raise_no_recount(self):
		db = MagicMock()
		db.get_value.side_effect = lambda d, n, f, *a, **k: "x" if f == "sales_order_item" else SimpleNamespace(qty=3.0, produced_qty=0)
		with patch.object(fwo_module.frappe, "db", db), \
			patch(f"{WO}.recount_line_work_order_qty") as recount:
			fwo_module.ensure_work_order_covers_fg("MFG-WO-1", 1.0)
		recount.assert_not_called()

	def test_cancel_restore_recounts_the_order_line(self):
		class FakeWO(SimpleNamespace):
			def db_set(self, f, v):
				setattr(self, f, v)

		wo = FakeWO(name="MFG-WO-2026-04829", docstatus=1, qty=0.218, produced_qty=0, pieces=1,
			completed_pcs=1, pending_pcs=0, pending_qty=0)
		row = SimpleNamespace(work_order=wo.name, deliver_as_qty=0, qty=0.2, ready_qty=0.2,
			calculated_qty=0.218, ready_pieces=1)
		with patch(f"{FWO}.frappe") as mock_frappe, \
			patch(f"{FWO}._recount_sales_order_line") as recount:
			mock_frappe.db.exists.return_value = True
			mock_frappe.get_doc.return_value = wo
			FinishWorkOrder._restore_work_order_progress(SimpleNamespace(), row)
		self.assertEqual(wo.qty, 0.2)
		recount.assert_called_once_with("MFG-WO-2026-04829")
