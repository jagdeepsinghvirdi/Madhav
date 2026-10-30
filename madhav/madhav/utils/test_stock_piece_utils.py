import unittest

from madhav.madhav.utils.stock_piece_utils import (
	_entry_avail_qty,
	absorb_rounding_dust,
	cap_pieces_to_physical_batch,
	cap_pieces_using_sre_batches,
	distribute_integer_pieces,
	dn_pieces_for_invoice_change,
	int_pieces_from_qty,
	pieces_for_weight_reconciliation,
	invoice_qty_tolerance_result,
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
		# Stored pieces are for full reserved qty and are not reduced when
		# delivered_qty moves. Remaining 0.3 of 0.5 → round(10 * 0.3/0.5) = 6.
		row = {
			"qty": 0.5,
			"delivered_qty": 0.2,
			"pieces": 10,
			"length": 6.0,
			"section_weight": 9.0,
		}
		self.assertEqual(resolve_entry_pieces(row, 0.3, 6.0, 9.0), 6)

		row2 = {
			"qty": 0.5,
			"delivered_qty": 0.0,
			"pieces": 10,
			"length": 6.0,
			"section_weight": 9.0,
		}
		self.assertEqual(resolve_entry_pieces(row2, 0.25, 6.0, 9.0), 5)

	def test_resolve_entry_pieces_after_partial_sre_delivery(self):
		# MAT-SRE style: 74 PC reserved, 10.825 delivered → remaining 5.519
		# must become 25 PC on the next DN, not the original 74.
		length, section_weight = 11.2, 19.72
		row = {
			"qty": 16.344,
			"delivered_qty": 10.825,
			"pieces": 74,
			"length": length,
			"section_weight": section_weight,
		}
		self.assertEqual(
			resolve_entry_pieces(row, 5.519, length, section_weight),
			25,
		)
		self.assertEqual(
			sum_undelivered_pieces_from_sre_rows([row]),
			25,
		)

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

	def test_issue_2i_qty_increase_of_one_piece_keeps_entered_pc(self):
		# Light section: one piece is 0.25. Qty 1.00 → 4 PC implied. Raising
		# qty by a full piece to 1.25 used to ceiling PC to 5. Entered 4 stays.
		length, section_weight = 10.0, 25.0
		piece_weight = length * section_weight / 1000
		self.assertAlmostEqual(piece_weight, 0.25)
		qty_before = 1.0
		qty_after = 1.25
		self.assertEqual(int_pieces_from_qty(qty_after, length, section_weight), 5)
		self.assertEqual(
			pieces_for_direct_batch_delivery(4, qty_after, length, section_weight),
			4,
		)
		self.assertEqual(
			dn_pieces_for_invoice_change(
				4, 4, qty_before, qty_after, piece_weight=piece_weight
			),
			4,
		)

	def test_missing_pieces_still_derive_from_weight(self):
		length, section_weight = 6.0, 10.0
		qty = 32.0004 * length * section_weight / 1000
		self.assertEqual(
			pieces_for_direct_batch_delivery(0, qty, length, section_weight),
			33,
		)


class TestDeliveryPieceAndToleranceRules(unittest.TestCase):
	def test_weight_reconciliation_does_not_add_pieces(self):
		# MUBIT-13721: 10.250 → 10.340 at 9.350 m × 9.60 kg/m is 1.003 pieces
		# of weight. That used to post +1 and leave a bar on an empty batch.
		raw = (10.340 - 10.250) * 1000 / (9.350 * 9.60)
		self.assertGreater(raw, 1)
		self.assertEqual(
			pieces_for_weight_reconciliation(10.250, 10.340, 9.350, 9.60),
			0,
		)
		# A gap smaller than one piece, and a gap of several pieces of
		# weight, are also not new bars.
		self.assertEqual(
			pieces_for_weight_reconciliation(6.000, 6.010, 6.0, 10.0),
			0,
		)
		self.assertEqual(
			pieces_for_weight_reconciliation(1.0, 2.5, 10.0, 25.0),
			0,
		)

	def test_entered_pieces_do_not_increase_when_invoice_qty_rises(self):
		# Qty 2, Invoice Qty 2.5, PC 4. One piece is 0.5, so the old scale
		# became round(4 * 2.5 / 2) = 5. The entered 4 stays 4.
		self.assertEqual(
			dn_pieces_for_invoice_change(4, 4, 2, 2.5, piece_weight=0.5),
			4,
		)

	def test_partial_count_is_not_ceiled_or_scaled_down(self):
		# Batch 5 qty / 10 PC, delivery 4.12 qty / 8 PC. 4.12 / 0.5 = 8.24,
		# which used to ceiling to 9. The typed 8 stays 8.
		self.assertEqual(
			dn_pieces_for_invoice_change(8, 10, 5, 4.12, piece_weight=0.5),
			8,
		)

	def test_inside_tolerance_keeps_entered_pieces(self):
		self.assertEqual(
			dn_pieces_for_invoice_change(
				168, 168, 31.103, 30.735,
				piece_weight=31.103 / 168, tolerance_percent=3,
			),
			168,
		)
		self.assertEqual(
			dn_pieces_for_invoice_change(
				100, 100, 18.480, 18.0,
				piece_weight=18.480 / 100, tolerance_percent=3,
			),
			100,
		)

	def test_outside_tolerance_still_scales_down(self):
		self.assertEqual(
			dn_pieces_for_invoice_change(
				30, 30, 6, 3, piece_weight=0.2, tolerance_percent=3,
			),
			15,
		)

	def test_without_tolerance_argument_behaviour_is_unchanged(self):
		self.assertEqual(
			dn_pieces_for_invoice_change(
				168, 168, 31.103, 30.735, piece_weight=31.103 / 168,
			),
			166,
		)

	def test_full_bundle_still_scales_down_for_a_real_partial(self):
		self.assertEqual(
			dn_pieces_for_invoice_change(30, 30, 6, 3, piece_weight=0.2),
			15,
		)

	def test_small_weight_gap_keeps_five_pieces(self):
		self.assertEqual(
			dn_pieces_for_invoice_change(5, 5, 1.189, 1.190, piece_weight=0.2378),
			5,
		)

	def test_missing_piece_weight_keeps_entered_pieces(self):
		self.assertEqual(dn_pieces_for_invoice_change(8, 8, 4, 5, piece_weight=0), 8)

	def test_tolerance_examples(self):
		# Classic 2(ii): 0.084% inside 3% → allow; PC must stay 5 (piece path).
		inside = invoice_qty_tolerance_result(1.189, 1.190, 3, precision=3)
		self.assertTrue(inside["within"])
		self.assertLess(inside["percent"], 3)
		self.assertAlmostEqual(inside["percent"], (0.001 / 1.189) * 100, places=4)

		# At 2 decimals both become 1.19 → no shortfall / 0%.
		equal_after_two_decimals = invoice_qty_tolerance_result(
			1.189, 1.190, 3, precision=2
		)
		self.assertTrue(equal_after_two_decimals["within"])
		self.assertEqual(equal_after_two_decimals["percent"], 0)

		# Classic outside: 7.150 → 7.650 ≈ 6.99% must fail at 3%.
		large_gap = invoice_qty_tolerance_result(7.150, 7.650, 3, precision=3)
		self.assertFalse(large_gap["within"])

		outside = invoice_qty_tolerance_result(2, 2.5, 3, precision=3)
		self.assertFalse(outside["within"])
		self.assertAlmostEqual(outside["percent"], 25, places=2)

		exact = invoice_qty_tolerance_result(100, 103, 3, precision=3)
		self.assertTrue(exact["within"])

		blank = invoice_qty_tolerance_result(2, None, 3, precision=3)
		self.assertTrue(blank["skip"])

		zero_qty = invoice_qty_tolerance_result(0, 1.5, 3, precision=3)
		self.assertTrue(zero_qty["zero_qty"])

	def test_rounding_dust_lands_on_the_last_row(self):
		# 1.000 split into three shares rounds to 0.333 and loses 0.001.
		self.assertEqual(
			absorb_rounding_dust([0.333, 0.333, 0.333], 1, precision=3),
			[0.333, 0.333, 0.334],
		)

	def test_rounding_dust_does_not_hide_a_real_gap(self):
		self.assertEqual(
			absorb_rounding_dust([1, 1, 1], 10, precision=3),
			[1, 1, 1],
		)


class TestCapPiecesToPhysicalBatch(unittest.TestCase):
	def test_ceiled_pieces_capped_to_batch(self):
		# FWO produced 21; SO reserve ceiled reserved weight to 22.
		self.assertEqual(cap_pieces_to_physical_batch(22, 21), 21)

	def test_does_not_raise_when_under_batch(self):
		self.assertEqual(cap_pieces_to_physical_batch(10, 21), 10)

	def test_equal_count_unchanged(self):
		self.assertEqual(cap_pieces_to_physical_batch(21, 21), 21)

	def test_zero_physical_does_not_cap(self):
		# Batch not stamped yet — keep the calculated count.
		self.assertEqual(cap_pieces_to_physical_batch(22, 0), 22)

	def test_sre_batches_cap_uses_sum_of_unique_batches(self):
		rows = [{"batch_no": "B1"}, {"batch_no": "B1"}, {"batch_no": "B2"}]
		lookup = {"B1": 10, "B2": 11}.get
		self.assertEqual(
			cap_pieces_using_sre_batches(22, rows, physical_lookup=lookup),
			21,
		)

	def test_sre_batches_no_rows_leaves_pieces(self):
		self.assertEqual(cap_pieces_using_sre_batches(22, [], physical_lookup=lambda _: 21), 22)


class TestSreSourceLabel(unittest.TestCase):
	def test_from_voucher_type_wins(self):
		from madhav.doc_events.stock_reservation_entry import sre_source_label
		from types import SimpleNamespace

		self.assertEqual(
			sre_source_label(
				SimpleNamespace(
					from_voucher_type="Finish Work Order",
					from_voucher_no="FWO-0001",
					voucher_type="Sales Order",
					voucher_no="SAL-ORD-0001",
				)
			),
			"Finish Work Order FWO-0001",
		)

	def test_sales_order_when_from_voucher_blank(self):
		from madhav.doc_events.stock_reservation_entry import sre_source_label
		from types import SimpleNamespace

		self.assertEqual(
			sre_source_label(
				SimpleNamespace(
					from_voucher_type="",
					from_voucher_no="",
					voucher_type="Sales Order",
					voucher_no="SAL-ORD-0001",
				)
			),
			"Sales Order SAL-ORD-0001",
		)

	def test_manual_when_no_voucher(self):
		from madhav.doc_events.stock_reservation_entry import sre_source_label
		from types import SimpleNamespace

		self.assertEqual(
			sre_source_label(
				SimpleNamespace(
					from_voucher_type="",
					from_voucher_no="",
					voucher_type="",
					voucher_no="",
				)
			),
			"Manual",
		)


class _FakeSre:
	def __init__(self, reserved_qty, before_qty=None, docstatus=1, action="update_after_submit", sb=None, before_sb=None):
		self.docstatus = docstatus
		self._action = action
		self.reserved_qty = reserved_qty
		self.sb_entries = sb or []
		self.flags = type("Flags", (), {"get": lambda _s, k, d=None: None})()
		self._before = None
		if before_qty is not None or before_sb is not None:
			self._before = _FakeSre(
				reserved_qty if before_qty is None else before_qty,
				docstatus=1,
				action="save",
				sb=before_sb if before_sb is not None else sb,
			)
			self._before._before = None

	def has_value_changed(self, field):
		if field != "reserved_qty" or self._before is None:
			return False
		return self.reserved_qty != self._before.reserved_qty

	def get_doc_before_save(self):
		return self._before


class TestSreMeaningfulEdit(unittest.TestCase):
	def test_qty_change_on_submitted_sre_is_edit(self):
		from madhav.doc_events.stock_reservation_entry import _is_meaningful_edit

		self.assertTrue(_is_meaningful_edit(_FakeSre(10, before_qty=9)))

	def test_submit_is_not_edit(self):
		from madhav.doc_events.stock_reservation_entry import _is_meaningful_edit

		doc = _FakeSre(10, before_qty=0, action="submit")
		self.assertFalse(_is_meaningful_edit(doc))

	def test_batch_row_change_is_edit(self):
		from madhav.doc_events.stock_reservation_entry import _is_meaningful_edit
		from types import SimpleNamespace

		old = [SimpleNamespace(batch_no="B1", qty=10, serial_no="", warehouse="WH")]
		new = [SimpleNamespace(batch_no="B2", qty=10, serial_no="", warehouse="WH")]
		self.assertTrue(_is_meaningful_edit(_FakeSre(10, before_qty=10, sb=new, before_sb=old)))

	def test_no_change_is_not_edit(self):
		from madhav.doc_events.stock_reservation_entry import _is_meaningful_edit
		from types import SimpleNamespace

		rows = [SimpleNamespace(batch_no="B1", qty=10, serial_no="", warehouse="WH")]
		self.assertFalse(_is_meaningful_edit(_FakeSre(10, before_qty=10, sb=rows, before_sb=rows)))


if __name__ == "__main__":
	unittest.main()
