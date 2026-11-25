# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

"""Sales Order "Reserve Stock" (usage review, 26 Nov 2026).

_get_batch_constraints returned None for every Sales Order line with a length
since 10 Sep, so the reserve path crashed with "'NoneType' object has no
attribute 'get'" (demo Error Log, 25 Nov).
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from madhav.madhav.monkey_patch import stock_reservation_entry as sre_patch

MOD = "madhav.madhav.monkey_patch.stock_reservation_entry"


class TestBatchConstraints(FrappeTestCase):
	def _constraints(self, length, ranges=None):
		frappe.flags.stock_reservation_item_ranges = ranges or {}
		try:
			with patch.object(sre_patch.frappe.db, "has_column", return_value=True), \
				patch.object(sre_patch.frappe.db, "get_value", return_value=length):
				return sre_patch._get_batch_constraints("Sales Order", "LINE-1", "FG000873", "Sales Order")
		finally:
			frappe.flags.stock_reservation_item_ranges = None

	def test_line_with_a_length_gets_the_two_metre_window(self):
		c = self._constraints(12.0)
		self.assertIsNotNone(c)
		self.assertEqual((c.min_length, c.max_length), (10.0, 14.0))

	def test_range_typed_in_the_dialog_wins(self):
		c = self._constraints(12.0, {"LINE-1": {"min_length": 11.5, "max_length": 12.5}})
		self.assertEqual((c.min_length, c.max_length), (11.5, 12.5))

	def test_reversed_range_is_put_right(self):
		c = self._constraints(12.0, {"LINE-1": {"min_length": 13, "max_length": 11}})
		self.assertEqual((c.min_length, c.max_length), (11.0, 13.0))

	def test_line_without_length_has_no_window(self):
		c = self._constraints(0)
		self.assertEqual((c.min_length, c.max_length), (None, None))

	def test_reservation_tool_is_not_filtered(self):
		c = sre_patch._get_batch_constraints("Sales Order", "LINE-1", "FG000873", "Batch Wise Reservation Tool")
		self.assertEqual((c.min_length, c.max_length), (None, None))
