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
