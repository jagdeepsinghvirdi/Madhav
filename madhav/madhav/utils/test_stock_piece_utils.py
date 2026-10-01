import unittest

from madhav.madhav.utils.stock_piece_utils import (
	_entry_avail_qty,
	distribute_integer_pieces,
	int_pieces_from_qty,
	pieces_for_direct_batch_delivery,
	preserve_entry_pieces,
	resolve_entry_pieces,
	resolve_entry_section_weight,
	resolve_weighted_length_from_entries,
	should_preserve_pieces_for_qty_change,
	stored_entry_pieces,
	sum_undelivered_pieces_from_sre_rows,
)


class TestDistributeIntegerPieces(unittest.TestCase):
	def test_exact_total_two_batches(self):
		# 30 pieces, 40/60 split -> 12 + 18 (not 12+18=30 with round drift)
		result = distribute_integer_pieces(30, [4.0, 6.0])
		self.assertEqual(sum(result), 30)
		self.assertEqual(result, [12, 18])

	def test_exact_total_three_batches(self):
		result = distribute_integer_pieces(26, [0.835, 5.43])
		self.assertEqual(sum(result), 26)
		self.assertTrue(all(p >= 0 for p in result))

	def test_zero_total(self):
		self.assertEqual(distribute_integer_pieces(0, [1, 2]), [0, 0])

	def test_never_exceeds_dn_row_total(self):
		total = 26
		weights = [1.0, 2.0, 3.0]
		result = distribute_integer_pieces(total, weights)
		self.assertEqual(sum(result), total)
		self.assertLessEqual(sum(result), total)


class TestPreserveEntryPieces(unittest.TestCase):
	def test_prefers_stored_row_pieces(self):
		entry = {"pieces": 4}
		self.assertEqual(preserve_entry_pieces(entry, item_pieces=26, qty_ratio=0.2), 4)

	def test_allocates_share_when_row_empty(self):
		entry = {"pieces": 0}
		self.assertEqual(preserve_entry_pieces(entry, item_pieces=26, qty_ratio=0.5), 13)

	def test_stored_entry_pieces_helper(self):
		self.assertEqual(stored_entry_pieces({"pieces": 7.9}), 7)
		self.assertEqual(stored_entry_pieces({"pieces": 0}), 0)


class TestResolveEntrySectionWeight(unittest.TestCase):
	def test_derives_from_qty_pieces_length_before_item_master(self):
		entry = {
			"section_weight": 0,
			"qty": 0.835,
			"pieces": 4,
			"length": 11,
		}
		# (0.835 * 1000) / (4 * 11) ≈ 18.977
		derived = resolve_entry_section_weight(entry, "FG000693", 11, batch_no=None)
		self.assertAlmostEqual(derived, 18.977, places=2)

	def test_stored_section_weight_wins(self):
		entry = {"section_weight": 20.5, "qty": 1, "pieces": 4, "length": 11}
		self.assertEqual(resolve_entry_section_weight(entry, "FG000693", 11), 20.5)


class TestStockPieceUtilsPartialDelivery(unittest.TestCase):
	def test_entry_avail_qty_excludes_fully_delivered_sre_row(self):
		row = {"qty": 0.5, "delivered_qty": 0.5}
		self.assertEqual(_entry_avail_qty(row), 0)

	def test_entry_avail_qty_uses_undelivered_share(self):
		row = {"qty": 0.5, "delivered_qty": 0.2}
		self.assertAlmostEqual(_entry_avail_qty(row), 0.3)

	def test_entry_avail_qty_for_outward_bundle_row(self):
		row = {"qty": -0.445, "delivered_qty": 0}
		self.assertAlmostEqual(_entry_avail_qty(row), 0.445)

	def test_weighted_length_skips_fully_delivered_batch(self):
		entries = [
			{"qty": 0.4, "delivered_qty": 0.4, "length": 6.0, "batch_no": "B1"},
			{"qty": 0.3, "delivered_qty": 0.0, "length": 8.5, "batch_no": "B2"},
		]
		self.assertAlmostEqual(resolve_weighted_length_from_entries(entries), 8.5)

	def test_weighted_length_multi_batch_undelivered_only(self):
		entries = [
			{"qty": 0.4, "delivered_qty": 0.1, "length": 6.0, "batch_no": "B1"},
			{"qty": 0.3, "delivered_qty": 0.0, "length": 8.5, "batch_no": "B2"},
		]
		# (0.3 * 6 + 0.3 * 8.5) / 0.6 = 7.25
		self.assertAlmostEqual(resolve_weighted_length_from_entries(entries), 7.25)

	def test_resolve_entry_pieces_scales_stored_pieces_for_partial_delivery(self):
		row = {
			"qty": 0.5,
			"delivered_qty": 0.2,
			"pieces": 10,
			"length": 6.0,
			"section_weight": 9.0,
		}
		avail = 0.3
		# proportional: round(10 * 0.3 / 0.3) = 10, but orig avail is 0.3 from 0.5 total
		# stored 10 for 0.5 qty, avail 0.3 -> round(10 * 0.3/0.3) wait orig_avail = 0.5-0.2=0.3, avail=0.3
		# full stored pieces since all undelivered
		self.assertEqual(resolve_entry_pieces(row, avail, 6.0, 9.0), 10)

		row2 = {
			"qty": 0.5,
			"delivered_qty": 0.0,
			"pieces": 10,
			"length": 6.0,
			"section_weight": 9.0,
		}
		self.assertEqual(resolve_entry_pieces(row2, 0.25, 6.0, 9.0), 5)

	def test_small_weight_change_does_not_drop_a_piece(self):
		# 40 PC at 12m x 8 kg/m. One piece weighs 0.096. A 0.05 cut used to
		# round 40 down to 39 and leave a piece pending on the batch.
		length, section_weight = 12.0, 8.0
		full_qty = 40 * length * section_weight / 1000
		row = {
			"qty": full_qty,
			"delivered_qty": 0,
			"pieces": 40,
			"length": length,
			"section_weight": section_weight,
		}
		self.assertEqual(
			resolve_entry_pieces(row, full_qty - 0.05, length, section_weight),
			40,
		)
		self.assertTrue(
			should_preserve_pieces_for_qty_change(
				full_qty, full_qty - 0.05, length, section_weight
			)
		)
		# Half the weight is a real partial delivery and still scales.
		self.assertFalse(
			should_preserve_pieces_for_qty_change(
				full_qty, full_qty / 2, length, section_weight
			)
		)

	def test_sum_undelivered_pieces_skips_fully_delivered_batch(self):
		rows = [
			{
				"batch_no": "B1",
				"qty": 0.4,
				"delivered_qty": 0.4,
				"pieces": 8,
				"length": 6.0,
				"section_weight": 9.0,
			},
			{
				"batch_no": "B2",
				"qty": 0.3,
				"delivered_qty": 0.0,
				"pieces": 6,
				"length": 8.5,
				"section_weight": 9.0,
			},
		]
		self.assertEqual(sum_undelivered_pieces_from_sre_rows(rows), 6)


class TestDirectBatchPiecesStayPhysical(unittest.TestCase):
	def test_entered_pieces_are_not_ceiled_up(self):
		# raw = 32.0004 ceils to 33. The entered 32 PC must stay 32.
		length, section_weight = 6.0, 10.0
		qty = 32.0004 * length * section_weight / 1000
		self.assertEqual(int_pieces_from_qty(qty, length, section_weight), 33)
		self.assertEqual(
			pieces_for_direct_batch_delivery(32, qty, length, section_weight),
			32,
		)

	def test_missing_pieces_still_derive_from_weight(self):
		length, section_weight = 6.0, 10.0
		qty = 32.0004 * length * section_weight / 1000
		self.assertEqual(
			pieces_for_direct_batch_delivery(0, qty, length, section_weight),
			33,
		)


if __name__ == "__main__":
	unittest.main()
