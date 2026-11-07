# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

"""Delivery Note cancel reverses its own shortfall Stock Reconciliation.

The reserved-stock check must block only a real shortage there, while a
manual Stock Reconciliation keeps the strict "any open reservation" rule.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from madhav.madhav.override.stock_reconciliation import StockReconciliation

KEY = ("FG003791", "Finished Goods RD - MUPL", "MUBT-TEST-1")


def _sr(dn_cancel=True, qty=6.219, current_qty=6.05):
	sr = StockReconciliation.__new__(StockReconciliation)
	sr.flags = frappe._dict(madhav_dn_cancel=dn_cancel)
	sr._action = "cancel"
	sr.items = [
		frappe._dict(item_code=KEY[0], warehouse=KEY[1], batch_no=KEY[2], qty=qty, current_qty=current_qty)
	]
	return sr


def _reserved(qty):
	return [frappe._dict(item_code=KEY[0], warehouse=KEY[1], batch_no=KEY[2], sales_orders="SO-X", reserved_qty=qty)]


class TestStockReconciliationDNCancel(FrappeTestCase):
	def _run(self, sr, reserved_qty, batch_qty):
		calls = {"n": 0}

		def fake_sql(query, *args, **kwargs):
			calls["n"] += 1
			# 1st: open reservations on the batch, 2nd: batch stock.
			return _reserved(reserved_qty) if calls["n"] == 1 else [[batch_qty]]

		with patch("frappe.db.sql", side_effect=fake_sql):
			sr.validate_reserved_stock()

	def test_dn_cancel_passes_when_stock_left_covers_reservation(self):
		# DN cancel put 6.219 back; SR took 0.169 off -> 6.05 left for 6.05 reserved.
		self._run(_sr(), reserved_qty=6.05, batch_qty=6.219)

	def test_dn_cancel_blocks_real_shortage(self):
		# Something reserved 7.0 on a batch that will only hold 6.05.
		with self.assertRaises(frappe.ValidationError):
			self._run(_sr(), reserved_qty=7.0, batch_qty=6.219)

	def test_manual_sr_cancel_keeps_strict_rule(self):
		with self.assertRaises(frappe.ValidationError):
			self._run(_sr(dn_cancel=False), reserved_qty=6.05, batch_qty=6.219)


class TestDnSreDeliveredLog(FrappeTestCase):
	def test_undeliver_from_log_reverses_only_recorded_qty(self):
		from madhav.madhav.monkey_patch import selling_controller as sc

		entry = frappe._dict(name="E1", delivered_qty=12.1, db_update=lambda: None)
		state = {}

		class FakeSRE(frappe._dict):
			def db_update(self):
				state["delivered"] = self.delivered_qty

			def update_status(self):
				pass

			def update_reserved_stock_in_bin(self):
				pass

		sre = FakeSRE(name="SRE-1", delivered_qty=12.1, sb_entries=[entry])
		with patch("frappe.db.get_value", return_value=1), patch("frappe.get_doc", return_value=sre):
			sc._undeliver_from_log([{"sre": "SRE-1", "entry": "E1", "qty": 6.05}])
		# 12.1 delivered by two DNs; cancelling the one that delivered 6.05
		# leaves the other DN's 6.05 in place.
		self.assertAlmostEqual(entry.delivered_qty, 6.05, places=3)
		self.assertAlmostEqual(state["delivered"], 6.05, places=3)

	def test_empty_log_reverses_nothing(self):
		from madhav.madhav.monkey_patch import selling_controller as sc

		with patch("frappe.get_doc") as get_doc:
			sc._undeliver_from_log([])
		get_doc.assert_not_called()
