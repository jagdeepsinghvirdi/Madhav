# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

from frappe.tests.utils import FrappeTestCase

from madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool import (
	remaining_qty_within_production_plan_bucket,
)
from madhav.madhav.override.production_plan import remaining_qty_for_new_production_plan


class TestProductionPlanQtyBuckets(FrappeTestCase):
	"""SO 140 / PP 60 / reserved 39.120 — plan vs reservation buckets."""

	def test_next_production_plan_shows_unplanned_only(self):
		# 140 − 60 already planned → 80 for PP-2 (not 140 − 39.120).
		self.assertEqual(
			remaining_qty_for_new_production_plan(140, 60),
			80,
		)
		self.assertEqual(
			remaining_qty_for_new_production_plan(140, 0),
			140,
		)
		self.assertEqual(
			remaining_qty_for_new_production_plan(140, 140),
			0,
		)

	def test_client_scenario_1_plan_only(self):
		self.assertEqual(
			remaining_qty_for_new_production_plan(10, 5, 0),
			5,
		)

	def test_client_scenario_2_reservation_only(self):
		self.assertEqual(
			remaining_qty_for_new_production_plan(10, 0, 2),
			8,
		)

	def test_client_scenario_3_plan_and_reservation(self):
		self.assertEqual(
			remaining_qty_for_new_production_plan(10, 5, 2),
			3,
		)

	def test_commitments_beyond_order_floor_at_zero(self):
		self.assertEqual(
			remaining_qty_for_new_production_plan(10, 5, 8),
			0,
		)
		self.assertEqual(
			remaining_qty_for_new_production_plan(100, 0, 140),
			0,
		)

	def test_fractional_reservation(self):
		self.assertAlmostEqual(
			remaining_qty_for_new_production_plan(140, 60, 39.120),
			40.88,
			places=3,
		)

	def test_bwrt_fetches_leftover_inside_planned_bucket(self):
		# PP-1 bucket 60, reserved 39.120 → next BWRT for that plan gets 20.880.
		self.assertAlmostEqual(
			remaining_qty_within_production_plan_bucket(140, 60, 39.120),
			20.88,
			places=3,
		)

	def test_bwrt_without_production_plan_uses_full_line(self):
		self.assertAlmostEqual(
			remaining_qty_within_production_plan_bucket(140, 0, 39.120),
			100.88,
			places=3,
		)

	def test_bwrt_after_full_plan_reserved(self):
		self.assertEqual(
			remaining_qty_within_production_plan_bucket(140, 60, 60),
			0,
		)


class TestPendingForNewProductionPlan(FrappeTestCase):
	"""Delivery is subtracted once, not on top of what was already planned."""

	def _pending(self, *args, **kwargs):
		from madhav.madhav.override.production_plan import pending_for_new_production_plan

		return pending_for_new_production_plan(*args, **kwargs)

	def test_delivered_from_planned_production_not_double_counted(self):
		self.assertEqual(self._pending(100, 60, 60), 40)

	def test_planned_nothing_delivered(self):
		self.assertEqual(self._pending(100, 60, 0), 40)

	def test_bwrt_delivered_and_rest_planned(self):
		self.assertEqual(self._pending(100, 70, 30, bwrt_open=0, bwrt_delivered=30), 0)

	def test_bwrt_open_reservation(self):
		self.assertEqual(self._pending(10, 5, 0, bwrt_open=2), 3)

	def test_delivered_from_stock_without_plan(self):
		self.assertEqual(self._pending(100, 0, 20), 80)

	def test_fully_delivered_line(self):
		self.assertEqual(self._pending(100, 0, 100), 0)
		self.assertEqual(self._pending(100, 100, 100), 0)

	def test_delivered_beyond_plan(self):
		self.assertEqual(self._pending(100, 60, 80), 20)

	def test_over_delivery_floors_at_zero(self):
		self.assertEqual(self._pending(100, 0, 103), 0)

	def test_production_beyond_plan_is_covered(self):
		# MU-SO26-00596 / FG001394 on demo: SO 10, planned 5.98, Finish Work
		# Order made 5.315 at length 9 plus 1.12 at length 8.5 (6.435 reserved),
		# 0.42 spare stock reserved. Only 3.145 is left, not 3.6.
		self.assertAlmostEqual(self._pending(10, 5.98, 0, bwrt_open=0.42, production_open=6.435), 3.145, places=6)

	def test_production_below_plan_keeps_plan(self):
		self.assertEqual(self._pending(100, 60, 0, production_open=30), 40)

	def test_production_delivered_and_held_counted_once(self):
		# 6.435 made: 2 delivered, 4.435 still reserved.
		self.assertAlmostEqual(self._pending(10, 5.98, 2, production_open=4.435), 3.565, places=6)

	def test_unplanned_production_is_covered(self):
		# Made without a Production Plan (Finish Work Order "Make it Unplanned").
		self.assertEqual(self._pending(10, 0, 0, production_open=4), 6)
