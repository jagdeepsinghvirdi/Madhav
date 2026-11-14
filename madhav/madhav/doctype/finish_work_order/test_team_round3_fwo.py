# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

"""Team feedback 14 Nov 2026, items 1 and 3 (Finish Work Order).

1. Length ± 2 against the Sales Order line is checked on every row tied to a
   Sales Order, also rows that go to Quality Inspection (PEN-WO-00521 let a
   6 m row through on a 12 m line).
3. Work Order Pending Qty = Qty - Produced Qty (MFG-WO-2026-04895: 6.5 qty,
   4.445 made showed 6.095 pending; must be 2.055).
"""

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from madhav.madhav.doctype.finish_work_order import finish_work_order as fwo_module
from madhav.madhav.doctype.finish_work_order.finish_work_order import FinishWorkOrder

MOD = "madhav.madhav.doctype.finish_work_order.finish_work_order"
BWRT = "madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool"


class FakeWorkOrder(SimpleNamespace):
	def db_set(self, field, value):
		setattr(self, field, value)


def _pwo(**kw):
	row = dict(
		idx=5,
		work_order="MFG-WO-2026-04895",
		sales_order="MU-SO26-00597",
		item="FG002248",
		qty=6.5,
		ready_qty=0.405,
		calculated_qty=0.4032,
		ready_pieces=4,
		length_size=6.0,
		quality_required=1,
		target_warehouse="Quality Inspection RD - MUPL",
		deliver_as_qty=1,
		make_it_unplanned=0,
		consumption=0.405,
		scrap_qty=0,
	)
	row.update(kw)
	return SimpleNamespace(**row)


def _fwo(rows):
	doc = FinishWorkOrder.__new__(FinishWorkOrder)
	doc.flags = frappe._dict()
	doc.pending_work_orders = rows
	doc.raw_materials = []
	return doc


def _readiness_errors(rows, so_window=(10.0, 14.0, 12.0)):
	"""Run validate_stock_readiness in Save (collect) mode; return the errors."""
	doc = _fwo(rows)
	doc._set_validation_mode(collect=True)

	def get_value(doctype, name, field=None, *a, **kw):
		if doctype == "Work Order" and field == "status":
			return "In Process"
		if doctype == "Work Order" and field == "sales_order_item":
			return "9dfbiunqvh"
		if doctype == "Sales Order Item" and field == "warehouse":
			return "Finished Goods RD - MUPL"
		return None

	with patch.object(FinishWorkOrder, "_build_rm_consume_pool", return_value=[{"item_code": "RM", "remaining_qty": 100}]), \
		patch.object(FinishWorkOrder, "_build_scrap_pool", return_value=[]), \
		patch.object(FinishWorkOrder, "validate_rm_yield", return_value=None), \
		patch.object(FinishWorkOrder, "_validate_raw_material_batch_stock", return_value=None), \
		patch.object(FinishWorkOrder, "_raise_collected_save_errors", return_value=None), \
		patch.object(fwo_module.frappe, "get_precision", return_value=3), \
		patch.object(fwo_module.frappe.db, "get_value", side_effect=get_value), \
		patch(f"{BWRT}.get_so_length_window", return_value=so_window):
		doc.validate_stock_readiness()
	return doc.flags.get("fwo_save_errors") or []


class TestFwoLengthCheckForQualityRows(FrappeTestCase):
	def test_qi_row_outside_window_is_blocked(self):
		errors = _readiness_errors([_pwo(length_size=6.0)])
		self.assertTrue(any("outside the allowed range" in e for e in errors), errors)

	def test_qi_row_longer_than_window_is_blocked(self):
		errors = _readiness_errors([_pwo(length_size=16.5)])
		self.assertTrue(any("outside the allowed range" in e for e in errors), errors)

	def test_row_to_so_warehouse_still_blocked(self):
		row = _pwo(length_size=6.0, quality_required=0, target_warehouse="Finished Goods RD - MUPL")
		errors = _readiness_errors([row])
		self.assertTrue(any("outside the allowed range" in e for e in errors), errors)

	def test_row_inside_window_passes(self):
		errors = _readiness_errors([_pwo(length_size=12.0), _pwo(idx=6, length_size=10.0)])
		self.assertFalse(any("allowed range" in e for e in errors), errors)

	def test_line_without_length_is_not_checked(self):
		errors = _readiness_errors([_pwo(length_size=6.0)], so_window=(None, None, 0))
		self.assertFalse(any("allowed range" in e for e in errors), errors)

	def test_row_without_sales_order_is_not_checked(self):
		errors = _readiness_errors([_pwo(sales_order=None, length_size=6.0)])
		self.assertFalse(any("allowed range" in e for e in errors), errors)

	def test_submit_mode_throws(self):
		doc = _fwo([_pwo(length_size=6.0)])
		doc._set_validation_mode(collect=False)
		with patch(f"{BWRT}.get_so_length_window", return_value=(10.0, 14.0, 12.0)):
			with self.assertRaises(frappe.ValidationError):
				doc._validate_row_length_against_so(doc.pending_work_orders[0], "9dfbiunqvh")


class TestWorkOrderPendingQty(FrappeTestCase):
	def test_pending_is_qty_minus_produced(self):
		self.assertEqual(fwo_module.work_order_pending_qty(6.5, 4.445), 2.055)

	def test_completed_work_order_has_zero_pending(self):
		# MFG-WO-2026-04896 showed 9.408 pending although fully produced.
		self.assertEqual(fwo_module.work_order_pending_qty(9.937, 9.937), 0)

	def test_never_negative(self):
		self.assertEqual(fwo_module.work_order_pending_qty(1.0, 1.2), 0)

	def test_cancel_of_one_row_restores_from_produced_qty(self):
		# Two rows made 4.04 + 0.405 on a 6.5 WO; cancelling the 0.405 row
		# leaves produced 4.04 → pending 2.46. Old code: min(6.095 + 0.405, 6.5) = 6.5.
		wo = FakeWorkOrder(docstatus=1, qty=6.5, produced_qty=4.04, pieces=33,
			completed_pcs=24, pending_pcs=9, pending_qty=6.095)
		row = SimpleNamespace(work_order="MFG-WO-2026-04895", ready_qty=0.405,
			calculated_qty=0.4032, ready_pieces=4, deliver_as_qty=1, qty=6.5)
		with patch(f"{MOD}.frappe") as mock_frappe:
			mock_frappe.db.exists.return_value = True
			mock_frappe.get_doc.return_value = wo
			FinishWorkOrder._restore_work_order_progress(SimpleNamespace(), row)
		self.assertEqual(wo.pending_qty, 2.46)
		self.assertEqual(wo.completed_pcs, 20)


class TestLengthCheckSkipsOtherItems(FrappeTestCase):
	def test_sub_assembly_work_order_is_not_checked(self):
		doc = _fwo([])
		with patch.object(fwo_module.frappe.db, "get_value", side_effect=lambda dt, n, f, *a, **k: "PPSA-1" if f == "production_plan_sub_assembly_item" else "FG002248"):
			self.assertFalse(doc._row_is_sales_order_item(_pwo(), "9dfbiunqvh"))

	def test_different_item_is_not_checked(self):
		doc = _fwo([])
		with patch.object(fwo_module.frappe.db, "get_value", side_effect=lambda dt, n, f, *a, **k: None if f == "production_plan_sub_assembly_item" else "OTHER-ITEM"):
			self.assertFalse(doc._row_is_sales_order_item(_pwo(), "9dfbiunqvh"))

	def test_ordered_item_is_checked(self):
		doc = _fwo([])
		with patch.object(fwo_module.frappe.db, "get_value", side_effect=lambda dt, n, f, *a, **k: None if f == "production_plan_sub_assembly_item" else "FG002248"):
			self.assertTrue(doc._row_is_sales_order_item(_pwo(), "9dfbiunqvh"))
