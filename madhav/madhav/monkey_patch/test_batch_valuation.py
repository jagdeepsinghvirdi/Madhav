import unittest
from types import SimpleNamespace

from frappe.utils import flt

from madhav.madhav.monkey_patch.batch_valuation import fill_unvalued_outgoing_batches


def _valuation(actual_qty, batches, available=None, avg=None):
	return SimpleNamespace(
		sle=SimpleNamespace(actual_qty=actual_qty),
		batch_nos=batches,
		available_qty=available or {},
		batch_avg_rate=avg or {},
		non_batchwise_valuation_batches=[],
		stock_value_change=0.0,
	)


class TestFillUnvaluedOutgoingBatches(unittest.TestCase):
	def test_uses_source_rate_when_posting_time_qty_is_zero(self):
		ledger = SimpleNamespace(qty=-24.593)
		valuation = _valuation(-24.593, {"MUBT-11125": ledger})
		fill_unvalued_outgoing_batches(valuation, {"MUBT-11125": 53492.629937304})
		self.assertAlmostEqual(valuation.batch_avg_rate["MUBT-11125"], 53492.629937304)
		self.assertAlmostEqual(valuation.stock_value_change, 53492.629937304 * -24.593)

	def test_keeps_rate_already_calculated_from_available_stock(self):
		ledger = SimpleNamespace(qty=-19.619)
		valuation = _valuation(
			-19.619,
			{"MUBT-06856": ledger},
			available={"MUBT-06856": 65.8},
			avg={"MUBT-06856": 52571.537285285},
		)
		fill_unvalued_outgoing_batches(valuation, {"MUBT-06856": 1})
		self.assertAlmostEqual(valuation.batch_avg_rate["MUBT-06856"], 52571.537285285)
		self.assertEqual(valuation.stock_value_change, 0.0)

	def test_does_not_replace_a_real_zero_source_rate(self):
		ledger = SimpleNamespace(qty=-4)
		valuation = _valuation(-4, {"B1": ledger})
		fill_unvalued_outgoing_batches(valuation, {"B1": 0})
		self.assertEqual(flt(valuation.batch_avg_rate.get("B1")), 0)
		self.assertEqual(valuation.stock_value_change, 0.0)

	def test_respects_allow_zero_valuation(self):
		ledger = SimpleNamespace(qty=-4)
		valuation = _valuation(-4, {"B1": ledger})
		fill_unvalued_outgoing_batches(valuation, {"B1": 100}, allow_zero=True)
		self.assertEqual(flt(valuation.batch_avg_rate.get("B1")), 0)
		self.assertEqual(valuation.stock_value_change, 0.0)

	def test_inward_entry_is_not_revalued(self):
		ledger = SimpleNamespace(qty=63.8)
		valuation = _valuation(63.8, {"MUBT-11125": ledger})
		fill_unvalued_outgoing_batches(valuation, {"MUBT-11125": 53492.63})
		self.assertEqual(flt(valuation.batch_avg_rate.get("MUBT-11125")), 0)
		self.assertEqual(valuation.stock_value_change, 0.0)
