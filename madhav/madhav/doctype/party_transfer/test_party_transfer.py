# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

from frappe.tests.utils import FrappeTestCase

from madhav.madhav.doctype.party_transfer.party_transfer import proportional_pieces
from madhav.madhav.utils.production_demand import is_free_stock_source


class TestPartyTransferHelpers(FrappeTestCase):
	def test_pieces_follow_qty(self):
		self.assertEqual(proportional_pieces(2, 10, 100), 20)

	def test_full_qty_moves_all_pieces(self):
		self.assertEqual(proportional_pieces(10, 10, 99), 99)
		self.assertEqual(proportional_pieces(9.9998, 10, 99), 99)

	def test_pieces_never_exceed_reserved(self):
		self.assertEqual(proportional_pieces(9.99, 10, 3), 3)

	def test_no_pieces_on_source(self):
		self.assertEqual(proportional_pieces(5, 10, 0), 0)
		self.assertEqual(proportional_pieces(5, 0, 10), 0)

	def test_rounding_is_to_nearest(self):
		self.assertEqual(proportional_pieces(1.04, 10, 100), 10)
		self.assertEqual(proportional_pieces(1.06, 10, 100), 11)

	def test_source_types(self):
		self.assertTrue(is_free_stock_source("Batch Wise Reservation Tool"))
		self.assertTrue(is_free_stock_source("Party Transfer"))
		self.assertFalse(is_free_stock_source("Finish Work Order"))
		self.assertFalse(is_free_stock_source("Stock Transfer"))
		self.assertFalse(is_free_stock_source(None))
