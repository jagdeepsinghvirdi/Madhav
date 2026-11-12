# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt
"""Effective planned quantity: Work Order releases and Party Transfers.

The database reads are patched, so these tests check the arithmetic and
the rules (closed vs cancelled, re-ordered plan rows, production vs spare
stock) without needing Sales Orders or Work Orders.
"""

import unittest
from unittest.mock import patch

import frappe

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
			return [frappe._dict(r, production_plan="PP-1") for r in self.closed]
		if "wo.docstatus = 2" in query:
			return [frappe._dict(r, pp="PP-1") for r in self.cancelled]
		if "docstatus = 1 and name !=" in query:
			ppi = args[0][0]
			row = self.plan_rows.get(ppi, {})
			return [[row.get("ordered_qty", 0), self.active_pieces]]
		raise AssertionError(query)

	def sql_list(self, query, *args, **kwargs):
		return []  # no combined Production Plans

	def get_value(self, doctype, name, fields, as_dict=False):
		row = self.plan_rows.get(name)
		return frappe._dict(row) if row else None


def release(**kwargs):
	fake = FakeDB(**kwargs)
	with patch.object(frappe, "db", fake):
		return pd.get_work_order_release("SOI-1")


class TestWorkOrderRelease(unittest.TestCase):
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


class TestEffectiveGiven(unittest.TestCase):
	"""Only production given away below the line's plan opens anything."""

	def test_surplus_given_away_opens_nothing(self):
		# 105 produced and reserved against a plan of 100; 5 given away.
		self.assertEqual(pd.effective_given(5, 100, 100, 0), 0)

	def test_planned_production_given_away(self):
		self.assertEqual(pd.effective_given(20, 100, 80, 0), 20)

	def test_part_surplus(self):
		# Plan 194.046; 71.585 delivered, 84.424 still reserved, 40.65 given:
		# 196.659 produced, so 2.613 of what was given was surplus.
		self.assertAlmostEqual(pd.effective_given(40.65, 194.046, 84.424, 71.585), 38.037, places=3)

	def test_after_close_release(self):
		# Plan 100, Work Order closed after 60 (40 released), all 60 given away.
		self.assertEqual(pd.effective_given(60, 60, 0, 0), 60)

	def test_nothing_given(self):
		self.assertEqual(pd.effective_given(0, 100, 50, 0), 0)


class TestEffectivePlanned(unittest.TestCase):
	"""get_effective_planned with its data sources patched."""

	def run_case(self, planned, released=0, given=0, prod_open=0, prod_delivered=0, pieces=0, released_pieces=0, given_pieces=0, reduced=True):
		rel = frappe._dict(qty=released, pieces=released_pieces)
		giv = frappe._dict(qty=given, pieces=given_pieces)
		with patch.object(pd, "get_work_order_release", return_value=rel), patch.object(
			pd, "get_given_away", return_value=giv
		), patch.object(pd, "get_production_coverage", return_value=(prod_open, prod_delivered)), patch.object(
			frappe, "flags", frappe._dict(pp_reduced_lines=({"SOI-1"} if reduced else set()))
		):
			return pd.get_effective_planned("SOI-1", planned, pieces)

	def test_transfer_reduces_plan(self):
		out = self.run_case(100, given=20, prod_open=80, pieces=1000, given_pieces=200)
		self.assertEqual(out.qty, 80)
		self.assertEqual(out.pieces, 800)

	def test_surplus_transfer_keeps_plan(self):
		self.assertEqual(self.run_case(100, given=5, prod_open=100).qty, 100)

	def test_close_and_transfer(self):
		self.assertEqual(self.run_case(100, released=40, given=60).qty, 0)

	def test_line_without_reductions_skips_queries(self):
		out = self.run_case(100, released=40, given=60, reduced=False)
		self.assertEqual(out.qty, 100)

	def test_never_negative(self):
		self.assertEqual(self.run_case(10, released=8, given=5).qty, 0)


class TestEffectiveRemaining(unittest.TestCase):
	"""The Production Plan rule fed by get_effective_planned (data sources patched)."""

	def remaining(self, so_qty, planned, released=0, given=0, delivered=0, free_open=0, free_delivered=0, prod_open=None):
		if prod_open is None:
			prod_open = max(0, planned - released - given - delivered)
		rel = frappe._dict(qty=released, pieces=0)
		giv = frappe._dict(qty=given, pieces=0)
		with patch.object(pd, "get_work_order_release", return_value=rel), patch.object(
			pd, "get_given_away", return_value=giv
		), patch.object(pd, "get_production_coverage", return_value=(prod_open, delivered)), patch.object(
			frappe, "flags", frappe._dict(pp_reduced_lines={"SOI-1"})
		):
			effective = pd.get_effective_planned("SOI-1", planned, 0).qty
		return pending_for_new_production_plan(so_qty, effective, delivered, free_open, free_delivered)

	def test_worked_example_from_madhav(self):
		# MU-SO26-00448 / FG003791: 194.046 ordered and planned, 196.749 produced,
		# 71.585 delivered, 84.424 still reserved, 40.65 moved away.
		self.assertAlmostEqual(
			self.remaining(194.046, 194.046, given=40.65, delivered=71.585, prod_open=84.424), 38.037, places=3
		)

	def test_transfer_of_20_from_100(self):
		self.assertEqual(self.remaining(100, 100, given=20), 20)

	def test_second_transfer_adds_up(self):
		self.assertEqual(self.remaining(100, 100, given=35), 35)

	def test_new_plan_for_given_away_closes_the_gap(self):
		self.assertEqual(self.remaining(100, 120, given=20, prod_open=80), 0)

	def test_closed_work_order(self):
		self.assertEqual(self.remaining(100, 60, released=35), 75)

	def test_closed_plus_transfer_counted_once(self):
		self.assertEqual(self.remaining(100, 60, released=35, given=20, prod_open=5), 95)

	def test_receiving_order_counts_transfer_as_free_stock(self):
		self.assertEqual(self.remaining(50, 0, free_open=20), 30)

	def test_receiving_order_with_full_plan_has_nothing_left(self):
		self.assertEqual(self.remaining(50, 50, free_open=20), 0)

	def test_delivered_beyond_effective_plan(self):
		self.assertEqual(self.remaining(100, 100, given=20, delivered=80, prod_open=0), 20)
