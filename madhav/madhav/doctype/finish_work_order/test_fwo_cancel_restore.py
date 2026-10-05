# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

"""Unit tests: Work Order progress restored when a Finish Work Order is cancelled."""

from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from madhav.madhav.doctype.finish_work_order.finish_work_order import FinishWorkOrder


class FakeWorkOrder(SimpleNamespace):
	def db_set(self, field, value):
		setattr(self, field, value)


def _row(**kwargs):
	defaults = {
		"work_order": "MFG-WO-2026-03609",
		"ready_qty": 1.05,
		"calculated_qty": 0.0,
		"ready_pieces": 61,
		"deliver_as_qty": 1,
	}
	defaults.update(kwargs)
	return SimpleNamespace(**defaults)


def _wo(**kwargs):
	defaults = {
		"docstatus": 1,
		"qty": 32.009,
		"pieces": 61,
		"completed_pcs": 61,
		"pending_pcs": 0,
		"pending_qty": 30.959,
	}
	defaults.update(kwargs)
	return FakeWorkOrder(**defaults)


def _restore(row, wo, exists=True):
	with patch(
		"madhav.madhav.doctype.finish_work_order.finish_work_order.frappe"
	) as mock_frappe:
		mock_frappe.db.exists.return_value = exists
		mock_frappe.get_doc.return_value = wo
		FinishWorkOrder._restore_work_order_progress(SimpleNamespace(), row)
	return wo


class TestFwoCancelRestore(FrappeTestCase):
	def test_restores_pending_qty_and_pieces(self):
		wo = _restore(_row(), _wo())
		self.assertEqual(wo.pending_qty, 32.009)
		self.assertEqual(wo.completed_pcs, 0)
		self.assertEqual(wo.pending_pcs, 61)

	def test_never_exceeds_work_order_qty(self):
		wo = _restore(_row(), _wo(pending_qty=32.009, completed_pcs=0, pending_pcs=61))
		self.assertEqual(wo.pending_qty, 32.009)

	def test_uses_calculated_qty_when_not_deliver_as_qty(self):
		row = _row(deliver_as_qty=0, ready_qty=1.05, calculated_qty=2.5)
		wo = _restore(row, _wo(pending_qty=29.509))
		self.assertEqual(wo.pending_qty, 32.009)

	def test_completed_pieces_never_negative(self):
		wo = _restore(_row(ready_pieces=100), _wo(completed_pcs=61))
		self.assertEqual(wo.completed_pcs, 0)
		self.assertEqual(wo.pending_pcs, 61)

	def test_skips_draft_work_order(self):
		wo = _restore(_row(), _wo(docstatus=0))
		self.assertEqual(wo.pending_qty, 30.959)
		self.assertEqual(wo.completed_pcs, 61)

	def test_skips_missing_work_order(self):
		wo = _restore(_row(), _wo(), exists=False)
		self.assertEqual(wo.pending_qty, 30.959)

	def test_skips_row_without_work_order(self):
		wo = _restore(_row(work_order=None), _wo())
		self.assertEqual(wo.pending_qty, 30.959)
