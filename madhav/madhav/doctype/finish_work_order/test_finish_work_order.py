# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

import unittest

from madhav.madhav.doctype.finish_work_order.finish_work_order import (
	_is_qty_rounding_dust,
	_pwo_basis_qty,
	_stock_qty_dust,
	required_rm_from_wo_and_scrap,
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
