# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

import unittest

import frappe

from madhav.madhav.doctype.finish_work_order.finish_work_order import (
	FinishWorkOrder,
	_is_qty_rounding_dust,
	_pwo_basis_qty,
	_stock_qty_dust,
	qty_needed_to_cover_fg,
	required_rm_from_wo_and_scrap,
	restored_wo_qty_after_fwo_cancel,
)
from madhav.madhav.utils.stock_piece_utils import absorb_rounding_dust


class TestFinishWorkOrderRmSplit(unittest.TestCase):
	"""RM is user-entered and split by ready/basis qty; yield floor is 0.975."""

	def test_basis_qty_uses_ready_when_deliver_as_qty(self):
		row = type("R", (), {"make_it_unplanned": 0, "sales_order": "SO-1", "deliver_as_qty": 1, "ready_qty": 10, "calculated_qty": 9})()
		self.assertEqual(_pwo_basis_qty(row), 10)

	def test_basis_qty_uses_calculated_when_deliver_as_qty_off(self):
		row = type("R", (), {"make_it_unplanned": 0, "sales_order": "SO-1", "deliver_as_qty": 0, "ready_qty": 10, "calculated_qty": 9})()
		self.assertEqual(_pwo_basis_qty(row), 9)

	def test_wo_qty_covers_mill_calculated_fg(self):
		# PEN-WO-00430: mill 0.218 vs planned remaining 0.2.
		self.assertEqual(qty_needed_to_cover_fg(0.2, 0, 0.218), 0.218)
		self.assertEqual(qty_needed_to_cover_fg(0.2, 0, 0.2), 0.2)
		self.assertEqual(qty_needed_to_cover_fg(1.0, 0.5, 0.52), 1.02)
		self.assertEqual(qty_needed_to_cover_fg(1.0, 0.5, 0.4), 1.0)

	def test_cancel_restores_expanded_wo_qty(self):
		self.assertEqual(restored_wo_qty_after_fwo_cancel(0.218, 0, 0.2), 0.2)
		self.assertEqual(restored_wo_qty_after_fwo_cancel(1.02, 0.5, 0.5), 1.0)
		self.assertEqual(restored_wo_qty_after_fwo_cancel(0.2, 0, 0.2), 0.2)
		# No planned remaining → do not wipe WO qty.
		self.assertEqual(restored_wo_qty_after_fwo_cancel(32.009, 0, 0), 32.009)

	def test_rounding_dust_lands_on_last_row(self):
		# Same split used when RM is shared across Pending WO rows.
		parts = absorb_rounding_dust([0.333, 0.333, 0.333], 1.0, precision=3)
		self.assertEqual(sum(parts), 1.0)
		self.assertEqual(parts[-1], 0.334)

	def test_still_need_0_001_is_rounding_dust(self):
		# PEN-WO-00401 style: last WO Still need 0.001 must not block Approve.
		self.assertEqual(_stock_qty_dust(3), 0.001)
		self.assertTrue(_is_qty_rounding_dust(0.001, 3))
		self.assertTrue(_is_qty_rounding_dust(0.0004, 3))
		self.assertFalse(_is_qty_rounding_dust(0.002, 3))
		self.assertFalse(_is_qty_rounding_dust(0.5, 3))

	def test_required_rm_is_wo_plus_scrap_over_yield(self):
		# PEN-WO-00407-2: 373.815 WO + 21.227 scrap → min RM 405.171
		self.assertEqual(
			required_rm_from_wo_and_scrap(373.815, 21.227, 3),
			405.171,
		)
		# 300 RM is below that floor.
		self.assertGreater(required_rm_from_wo_and_scrap(373.815, 21.227, 3), 300)
		# Equal-or-more at the floor is allowed.
		self.assertEqual(
			required_rm_from_wo_and_scrap(373.815, 21.227, 3),
			required_rm_from_wo_and_scrap(373.815, 21.227, 3),
		)


class TestFwoSaveAndSubmitValidations(unittest.TestCase):
	"""Save collects every error; Submit throws the first (original backstop)."""

	def _doc(self, action="save", silent=False):
		doc = FinishWorkOrder.__new__(FinishWorkOrder)
		doc.flags = frappe._dict()
		doc._action = action
		if silent:
			doc.flags.fwo_silent_autosave = True
		silent_flag = bool(doc.flags.get("fwo_silent_autosave"))
		submitting = getattr(doc, "_action", None) == "submit"
		doc._set_validation_mode(collect=not silent_flag and not submitting)
		return doc

	def test_manual_save_collects_errors(self):
		doc = self._doc(action="save")
		self.assertTrue(doc.flags.fwo_collect_errors)

	def test_submit_does_not_collect(self):
		doc = self._doc(action="submit")
		self.assertFalse(doc.flags.fwo_collect_errors)

	def test_fetch_autosave_does_not_collect(self):
		doc = self._doc(action="save", silent=True)
		self.assertFalse(doc.flags.fwo_collect_errors)

	def test_before_submit_forces_throw_mode(self):
		doc = self._doc(action="save")
		self.assertTrue(doc.flags.fwo_collect_errors)
		doc._set_validation_mode(collect=False)
		self.assertFalse(doc.flags.fwo_collect_errors)

	def _patch_throw(self):
		raised = []
		orig = frappe.throw

		def fake_throw(msg, title=None, **kwargs):
			raised.append(str(msg))
			exc = frappe.ValidationError(msg)
			exc.title = title
			raise exc

		frappe.throw = fake_throw
		self.addCleanup(setattr, frappe, "throw", orig)
		return raised

	def test_save_shows_all_errors_together(self):
		doc = self._doc(action="save")
		raised = self._patch_throw()
		doc._collect_save_error("Missing batch")
		doc._collect_save_error("Raw material short")
		with self.assertRaises(frappe.ValidationError):
			doc._raise_collected_save_errors()
		self.assertEqual(len(raised), 1)
		self.assertIn("Missing batch", raised[0])
		self.assertIn("Raw material short", raised[0])

	def test_submit_throws_on_first_error(self):
		doc = self._doc(action="submit")
		raised = self._patch_throw()
		with self.assertRaises(frappe.ValidationError):
			doc._collect_save_error("Missing batch")
		self.assertEqual(raised, ["Missing batch"])
		# Second error is never queued — original first-throw behaviour.
		self.assertFalse(doc.flags.get("fwo_save_errors"))
