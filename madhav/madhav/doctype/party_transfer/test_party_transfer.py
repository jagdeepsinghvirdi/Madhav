# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

import unittest

from madhav.madhav.doctype.party_transfer.party_transfer import proportional_pieces, suggested_pieces
from madhav.madhav.utils.production_demand import is_free_stock_source


class TestPartyTransferHelpers(unittest.TestCase):
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

	def test_source_type_classes(self):
		from madhav.madhav.utils.production_demand import source_type_for

		self.assertEqual(source_type_for("Finish Work Order"), "Production")
		self.assertEqual(source_type_for("Stock Transfer"), "Production")
		self.assertEqual(source_type_for("Batch Wise Reservation Tool"), "Spare")
		self.assertEqual(source_type_for("Party Transfer"), "Spare")
		self.assertEqual(source_type_for(None), "Other")
		self.assertEqual(source_type_for("Pick List"), "Other")

	def test_source_types(self):
		self.assertTrue(is_free_stock_source("Batch Wise Reservation Tool"))
		self.assertTrue(is_free_stock_source("Party Transfer"))
		self.assertFalse(is_free_stock_source("Finish Work Order"))
		self.assertFalse(is_free_stock_source("Stock Transfer"))
		self.assertFalse(is_free_stock_source(None))

	def test_suggested_pieces_rules(self):
		self.assertEqual(suggested_pieces(10, 10, 99), 99)  # all qty, all pieces
		self.assertEqual(suggested_pieces(9.5, 10, 3), 2)  # part qty leaves a piece
		self.assertEqual(suggested_pieces(0.001, 10, 50), 1)  # at least one piece
		self.assertEqual(suggested_pieces(2, 10, 100), 20)
		self.assertEqual(suggested_pieces(5, 10, 0), 0)  # source has no pieces
		self.assertEqual(suggested_pieces(5, 10, 1), 1)  # a single piece can't be split
