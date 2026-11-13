# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

import unittest
from unittest.mock import patch

from madhav.madhav.doctype.batch_wise_reservation_tool import batch_wise_reservation_tool as bwrt
from madhav.madhav.doctype.party_transfer.party_transfer import length_fits, proportional_pieces, suggested_pieces
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


class TestOtherLength(unittest.TestCase):
	"""Party Transfer takes any length of the same item and marks the others."""

	def fits(self, so_length, length):
		window = (so_length - 2, so_length + 2, so_length) if so_length else (None, None, 0)
		with patch.object(bwrt, "get_so_length_window", return_value=window):
			return length_fits("SOI-1", length)

	def test_inside_window(self):
		# MU-SO26-00594 line FG001394 length 8.8: 8.5, 8.8, 9.0 fit.
		self.assertTrue(self.fits(8.8, 8.5))
		self.assertTrue(self.fits(8.8, 10.8))

	def test_outside_window_is_other_length(self):
		# 6.4 and 11.7 were hidden before; now listed as other length.
		self.assertFalse(self.fits(8.8, 6.4))
		self.assertFalse(self.fits(8.8, 11.7))

	def test_line_without_length_takes_anything(self):
		self.assertTrue(self.fits(0, 6.4))
