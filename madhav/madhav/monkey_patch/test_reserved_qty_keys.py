"""Round 4 bug fix: Reserved Stock read 0 in the Item dashboard, Stock Balance
and Stock Balance Madhav.

They look up ``(item_code, warehouse)``; the Madhav patch returned
``(item_code, warehouse, batch_no)`` keys to every caller. Seen on production
20 Nov 2026: FG003793 in Finished Goods RD, 20.865 t reserved, dashboard 0.
"""
import unittest
from unittest.mock import MagicMock, patch

from madhav.madhav.monkey_patch import stock_reservation_entry as patch_mod


class FakeQuery:
	"""Query-builder stand-in: every call chains; run() returns the next result."""

	def __init__(self, results):
		self.results = list(results)

	def __getattr__(self, name):
		return lambda *a, **k: self

	def run(self, **kw):
		return self.results.pop(0)


def _call(results, **kw):
	q = FakeQuery(results)
	qb = MagicMock()
	qb.from_.return_value = q
	with patch.object(patch_mod.frappe, "qb", qb):
		return patch_mod.get_sre_reserved_qty_for_items_and_warehouses(["FG003793"], ["FG RD"], **kw)


class TestReservedQtyKeys(unittest.TestCase):
	def test_dashboard_and_reports_get_item_warehouse_keys(self):
		out = _call([
			[{"item_code": "FG003793", "warehouse": "FG RD", "reserved_qty": 20.865}],  # batch rows
			[],  # qty reservations
		])
		self.assertEqual(out, {("FG003793", "FG RD"): 20.865})

	def test_qty_reservations_are_counted_too(self):
		out = _call([
			[{"item_code": "FG003793", "warehouse": "FG RD", "reserved_qty": 2.0}],
			[{"item_code": "FG003793", "warehouse": "FG RD", "reserved_qty": 0.5}],
		])
		self.assertAlmostEqual(out[("FG003793", "FG RD")], 2.5)

	def test_batch_keys_only_when_batches_are_asked_for(self):
		out = _call(
			[[{"item_code": "FG003793", "warehouse": "FG RD", "batch_no": "B-1", "reserved_qty": 1.2}]],
			batch_list=["B-1"],
		)
		self.assertEqual(out, {("FG003793", "FG RD", "B-1"): 1.2})

	def test_nothing_reserved(self):
		self.assertEqual(_call([[], []]), {})
		self.assertEqual(patch_mod.get_sre_reserved_qty_for_items_and_warehouses([]), {})


if __name__ == "__main__":
	unittest.main()
