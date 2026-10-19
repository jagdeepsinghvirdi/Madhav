# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

import unittest

from madhav.madhav.doctype.finish_work_order.finish_work_order import _pwo_basis_qty
from madhav.madhav.utils.stock_piece_utils import absorb_rounding_dust


class TestFinishWorkOrderRmSplit(unittest.TestCase):
	"""RM is user-entered and split by ready/basis qty — no 0.975 yield rule."""

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
