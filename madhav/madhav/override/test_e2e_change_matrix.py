# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool import (
	remaining_qty_within_production_plan_bucket,
)
from madhav.madhav.doctype.finish_work_order.finish_work_order import (
	_is_qty_rounding_dust,
	_pwo_basis_qty,
	_stock_qty_dust,
	qty_needed_to_cover_fg,
)
from madhav.madhav.override.production_plan import remaining_qty_for_new_production_plan
from madhav.madhav.utils.stock_piece_utils import (
	absorb_rounding_dust,
	dn_pieces_for_invoice_change,
	invoice_qty_tolerance_result,
)


class TestE2EChangeMatrix(FrappeTestCase):
	"""End-to-end guards: our changes must not block happy paths."""

	def test_fwo_0_001_dust_allows_pen_wo_style(self):
		self.assertEqual(_stock_qty_dust(3), 0.001)
		self.assertTrue(_is_qty_rounding_dust(0.001, 3))
		self.assertFalse(_is_qty_rounding_dust(0.002, 3))

	def test_fwo_absorb_and_fifo_no_block(self):
		parts = absorb_rounding_dust([0.333, 0.333, 0.333], 1.0, 3)
		self.assertEqual(sum(parts), 1.0)
		bases = [1.0, 1.0, 1.0, 1.0, 0.926]
		rm = 4.925
		raw = [b * (rm / sum(bases)) for b in bases]
		adj = absorb_rounding_dust(raw, rm, 3)
		self.assertAlmostEqual(sum(adj), flt(rm, 3), places=3)
		pool = flt(rm, 3)
		short = 0.0
		for c in adj:
			need = flt(c, 3)
			if need > pool + 1e-12:
				short = flt(need - pool, 3)
				pool = 0
			else:
				pool = flt(pool - need, 3)
		self.assertTrue(short == 0 or _is_qty_rounding_dust(short, 3), short)

	def test_fwo_basis_ready_vs_calculated(self):
		dav = type(
			"R",
			(),
			{
				"make_it_unplanned": 0,
				"sales_order": "SO",
				"deliver_as_qty": 1,
				"ready_qty": 0.925,
				"calculated_qty": 0.926,
			},
		)()
		calc = type(
			"R",
			(),
			{
				"make_it_unplanned": 0,
				"sales_order": "SO",
				"deliver_as_qty": 0,
				"ready_qty": 0.925,
				"calculated_qty": 0.926,
			},
		)()
		self.assertEqual(_pwo_basis_qty(dav), 0.925)
		self.assertEqual(_pwo_basis_qty(calc), 0.926)
		# PEN-WO-00430: mill FG 0.218 must raise WO remaining 0.2, not block.
		self.assertEqual(qty_needed_to_cover_fg(0.2, 0, 0.218), 0.218)

	def test_dn_tolerance_and_piece_preserve_no_block(self):
		inside = invoice_qty_tolerance_result(1.189, 1.190, 3, precision=3)
		outside = invoice_qty_tolerance_result(2, 2.5, 3, precision=3)
		self.assertTrue(inside.get("within"))
		self.assertFalse(outside.get("within"))
		self.assertEqual(
			dn_pieces_for_invoice_change(5, 5, 1.189, 1.190, piece_weight=0.2378),
			5,
		)
		self.assertEqual(
			dn_pieces_for_invoice_change(4, 4, 2, 2.5, piece_weight=0.5),
			4,
		)

	def test_pp_bwrt_buckets_legacy_and_client(self):
		self.assertEqual(remaining_qty_for_new_production_plan(140, 0), 140)
		self.assertEqual(remaining_qty_for_new_production_plan(140, 60), 80)
		self.assertAlmostEqual(
			remaining_qty_within_production_plan_bucket(140, 0, 39.120),
			100.88,
			places=3,
		)
		self.assertAlmostEqual(
			remaining_qty_within_production_plan_bucket(140, 60, 39.120),
			20.88,
			places=3,
		)

	def test_critical_modules_import(self):
		import madhav.doc_events.delivery_note as dn
		import madhav.madhav.doctype.finish_work_order.finish_work_order as fwo
		import madhav.madhav.override.production_plan as pp

		self.assertTrue(hasattr(fwo, "FinishWorkOrder"))
		self.assertTrue(hasattr(dn, "_enforce_delivery_tolerance"))
		self.assertTrue(hasattr(pp, "CustomProductionPlan"))
