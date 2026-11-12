# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt
"""Effective planned quantity: Work Order releases and Party Transfers.

The database reads are patched, so these tests check the arithmetic and
the rules (closed vs cancelled, re-ordered plan rows, production vs spare
stock) without needing Sales Orders or Work Orders.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from madhav.madhav.override.production_plan import pending_for_new_production_plan
from madhav.madhav.utils import production_demand as pd


class FakeDB:
	"""Answers the three queries get_work_order_release makes."""

	def __init__(self, closed=None, cancelled=None, plan_rows=None, active_pieces=0):
		self.closed = closed or []
		self.cancelled = cancelled or []
		self.plan_rows = plan_rows or {}
		self.active_pieces = active_pieces

	def sql(self, query, *args, **kwargs):
		if "wo.status = 'Closed'" in query:
			return [frappe._dict(r) for r in self.closed]
		if "wo.docstatus = 2" in query:
			return [frappe._dict(r) for r in self.cancelled]
		if "sum(ifnull(pieces, 0))" in query:
			return [[self.active_pieces]]
		raise AssertionError(query)

	def get_value(self, doctype, name, fields, as_dict=False):
		row = self.plan_rows.get(name)
		return frappe._dict(row) if row else None


def release(**kwargs):
	fake = FakeDB(**kwargs)
	with patch.object(frappe, "db", fake):
		return pd.get_work_order_release("SOI-1")


class TestWorkOrderRelease(FrappeTestCase):
	def test_no_work_orders(self):
		r = release()
		self.assertEqual(r.qty, 0)
		self.assertEqual(r.pieces, 0)

	def test_closed_releases_only_unproduced(self):
		# Plan 60, Work Order 60, produced 25, then closed -> 35 back.
		r = release(closed=[{"qty": 60, "produced_qty": 25, "pieces": 600, "completed_pcs": 250}])
		self.assertAlmostEqual(r.qty, 35)
		self.assertAlmostEqual(r.pieces, 350)

	def test_closed_after_over_production_releases_nothing(self):
		r = release(closed=[{"qty": 10, "produced_qty": 10.4, "pieces": 0, "completed_pcs": 0}])
		self.assertEqual(r.qty, 0)

	def test_cancelled_releases_its_qty(self):
		r = release(
			cancelled=[{"ppi": "PPI-1", "qty": 40, "pieces": 400}],
			plan_rows={"PPI-1": {"planned_qty": 100, "ordered_qty": 60, "pieces": 1000}},
			active_pieces=600,
		)
		self.assertAlmostEqual(r.qty, 40)
		self.assertAlmostEqual(r.pieces, 400)

	def test_cancelled_then_remade_from_same_plan_releases_nothing(self):
		# A replacement Work Order from the old plan row brought ordered_qty back.
		r = release(
			cancelled=[{"ppi": "PPI-1", "qty": 40, "pieces": 400}],
			plan_rows={"PPI-1": {"planned_qty": 100, "ordered_qty": 100, "pieces": 1000}},
			active_pieces=1000,
		)
		self.assertEqual(r.qty, 0)
		self.assertEqual(r.pieces, 0)

	def test_cancelled_partly_remade(self):
		# Cancelled 40, remade 30 from the old plan -> 10 released.
		r = release(
			cancelled=[{"ppi": "PPI-1", "qty": 40, "pieces": 400}],
			plan_rows={"PPI-1": {"planned_qty": 100, "ordered_qty": 90, "pieces": 1000}},
			active_pieces=900,
		)
		self.assertAlmostEqual(r.qty, 10)
		self.assertAlmostEqual(r.pieces, 100)

	def test_closed_and_cancelled_add_up(self):
		r = release(
			closed=[{"qty": 60, "produced_qty": 25, "pieces": 0, "completed_pcs": 0}],
			cancelled=[{"ppi": "PPI-2", "qty": 20, "pieces": 0}],
			plan_rows={"PPI-2": {"planned_qty": 20, "ordered_qty": 0, "pieces": 0}},
		)
		self.assertAlmostEqual(r.qty, 55)


class TestEffectiveRemaining(FrappeTestCase):
	"""The Production Plan rule fed with the effective planned quantity."""

	def remaining(self, so_qty, planned, released=0, given=0, delivered=0, free_open=0, free_delivered=0):
		effective = max(0, planned - released - given)
		return pending_for_new_production_plan(so_qty, effective, delivered, free_open, free_delivered)

	def test_worked_example_from_madhav(self):
		# MU-SO26-00448 / FG003791: 194 ordered and planned, 71.585 delivered,
		# 40.65 moved to another customer -> about 40.65 to plan again.
		self.assertAlmostEqual(self.remaining(194.046, 194.046, given=40.65, delivered=71.585), 40.65, places=3)

	def test_transfer_of_20_from_100(self):
		self.assertEqual(self.remaining(100, 100, given=20), 20)

	def test_second_transfer_adds_up(self):
		self.assertEqual(self.remaining(100, 100, given=35), 35)

	def test_new_plan_for_given_away_closes_the_gap(self):
		# Re-planned 20 on a new Production Plan: planned becomes 120.
		self.assertEqual(self.remaining(100, 120, given=20), 0)

	def test_closed_work_order(self):
		# Order 100, plan 60, closed after 25 -> 35 released + 40 never planned.
		self.assertEqual(self.remaining(100, 60, released=35), 75)

	def test_closed_plus_transfer_counted_once(self):
		# Same order, and 20 of the 25 produced went to another party.
		self.assertEqual(self.remaining(100, 60, released=35, given=20), 95)

	def test_receiving_order_counts_transfer_as_free_stock(self):
		# B: order 50, nothing planned, 20 received -> 30 left to plan.
		self.assertEqual(self.remaining(50, 0, free_open=20), 30)

	def test_receiving_order_with_full_plan_has_nothing_left(self):
		self.assertEqual(self.remaining(50, 50, free_open=20), 0)

	def test_delivered_beyond_effective_plan(self):
		# Delivery from production still counts once.
		self.assertEqual(self.remaining(100, 100, given=20, delivered=90), 10)
