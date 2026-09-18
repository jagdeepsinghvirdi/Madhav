"""Unit tests: DN warehouse sync vs Stock Reservation Warehouse Mismatch.

Covers the reported case: same item reserved via separate stock movements
(e.g. For Mill EXTRA) while the DN row still carries the SO / set_warehouse
(Finished Goods). Sync + monkey-patched SRE validate must realign warehouse
without weakening single-warehouse flows.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from madhav.doc_events.delivery_note import _sync_dn_item_warehouse_to_reservation
from madhav.madhav.monkey_patch.delivery_note import (
	validate_against_stock_reservation_entries as patched_validate,
)


class TestDNReservationWarehouseSync(FrappeTestCase):
	def _row(self, **kwargs):
		defaults = {
			"against_sales_order": "SO-1",
			"so_detail": "soi-1",
			"item_code": "FG001187",
			"warehouse": "Finished Goods - MUPL",
			"batch_no": None,
			"serial_and_batch_bundle": None,
			"custom_sre": None,
		}
		defaults.update(kwargs)
		return SimpleNamespace(**defaults)

	def _doc(self, rows):
		class _Doc:
			def __init__(self, items):
				self.items = items
				self.is_return = 0

			def get(self, key):
				return getattr(self, key, None)

		return _Doc(rows)

	def test_sync_rewrites_so_warehouse_to_mill_extra(self):
		"""DN stamped with FG warehouse; only Mill EXTRA is reserved."""
		row = self._row()
		doc = self._doc([row])
		reserved = ["For Mill (EXTRA) - MUPL"]

		with patch(
			"erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry.get_sre_reserved_warehouses_for_voucher",
			return_value=reserved,
		):
			_sync_dn_item_warehouse_to_reservation(doc)

		self.assertEqual(row.warehouse, "For Mill (EXTRA) - MUPL")

	def test_sync_preserves_row_already_on_a_reserved_warehouse(self):
		"""Two reserved WHs (2 stock entries / SREs): keep the row's choice."""
		row = self._row(warehouse="Finished Goods - MUPL")
		doc = self._doc([row])
		reserved = ["Finished Goods - MUPL", "For Mill (EXTRA) - MUPL"]

		with patch(
			"erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry.get_sre_reserved_warehouses_for_voucher",
			return_value=reserved,
		):
			_sync_dn_item_warehouse_to_reservation(doc)

		self.assertEqual(row.warehouse, "Finished Goods - MUPL")

	def test_sync_prefers_custom_sre_warehouse_when_active(self):
		row = self._row(
			warehouse="Finished Goods - MUPL",
			custom_sre="SRE-MILL",
			against_sales_order="SO-1",
			so_detail="soi-1",
		)
		doc = self._doc([row])
		reserved = ["Finished Goods - MUPL", "For Mill (EXTRA) - MUPL"]

		with patch(
			"erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry.get_sre_reserved_warehouses_for_voucher",
			return_value=reserved,
		), patch(
			"frappe.db.get_value",
			return_value=frappe._dict(
				warehouse="For Mill (EXTRA) - MUPL",
				docstatus=1,
				status="Reserved",
				voucher_no="SO-1",
				voucher_detail_no="soi-1",
			),
		):
			_sync_dn_item_warehouse_to_reservation(doc)

		self.assertEqual(row.warehouse, "For Mill (EXTRA) - MUPL")

	def test_sync_ignores_stale_custom_sre_not_in_reserved_list(self):
		row = self._row(
			warehouse="Stores - MUPL",
			custom_sre="SRE-OLD",
			against_sales_order="SO-1",
			so_detail="soi-1",
		)
		doc = self._doc([row])
		reserved = ["For Mill (EXTRA) - MUPL"]

		with patch(
			"erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry.get_sre_reserved_warehouses_for_voucher",
			return_value=reserved,
		), patch(
			"frappe.db.get_value",
			return_value=frappe._dict(
				warehouse="Some Other WH - MUPL",
				docstatus=1,
				status="Reserved",
				# Wrong SO line — must not pin warehouse from this SRE
				voucher_no="SO-OTHER",
				voucher_detail_no="soi-other",
			),
		):
			_sync_dn_item_warehouse_to_reservation(doc)

		self.assertEqual(row.warehouse, "For Mill (EXTRA) - MUPL")

	def test_custom_sre_pins_fg_row_when_same_item_also_on_mill(self):
		"""Same SO line reserved in FG + Mill EXTRA — FG row must stay on FG."""
		row = self._row(
			item_code="FG003198",
			warehouse="Stores - MUPL",
			custom_sre="SRE-FG",
			against_sales_order="SO-1",
			so_detail="soi-fg003198",
		)
		doc = self._doc([row])
		reserved = ["Finished Goods - MUPL", "For Mill (EXTRA) - MUPL"]

		with patch(
			"erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry.get_sre_reserved_warehouses_for_voucher",
			return_value=reserved,
		), patch(
			"frappe.db.get_value",
			return_value=frappe._dict(
				warehouse="Finished Goods - MUPL",
				docstatus=1,
				status="Partially Reserved",
				voucher_no="SO-1",
				voucher_detail_no="soi-fg003198",
			),
		):
			_sync_dn_item_warehouse_to_reservation(doc)

		self.assertEqual(row.warehouse, "Finished Goods - MUPL")

	def test_patched_validate_syncs_before_check(self):
		"""Simulate super().validate() leaving the wrong WH; patch must fix it."""
		row = self._row(warehouse="Finished Goods - MUPL")
		doc = self._doc([row])
		doc.validate_against_stock_reservation_entries = (
			patched_validate.__get__(doc, type(doc))
		)

		reserved = ["For Mill (EXTRA) - MUPL"]

		with patch(
			"erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry.get_sre_reserved_warehouses_for_voucher",
			return_value=reserved,
		):
			doc.validate_against_stock_reservation_entries()

		self.assertEqual(row.warehouse, "For Mill (EXTRA) - MUPL")

	def test_cancelled_custom_sre_keeps_fg_when_sibling_mill_remains(self):
		"""Deliver-as-Qty cancels FG SRE; sibling Mill EXTRA still active.

		Sync must not flip the FG row onto Mill EXTRA.
		"""
		row = self._row(
			item_code="FG003198",
			warehouse="Finished Goods - MUPL",
			custom_sre="SRE-FG-CANCELLED",
			against_sales_order="SO-1",
			so_detail="soi-fg003198",
		)
		doc = self._doc([row])
		# After cancel, only the sibling Mill SRE remains active.
		reserved = ["For Mill (EXTRA) - MUPL"]

		with patch(
			"erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry.get_sre_reserved_warehouses_for_voucher",
			return_value=reserved,
		), patch(
			"frappe.db.get_value",
			return_value=frappe._dict(
				warehouse="Finished Goods - MUPL",
				docstatus=2,
				status="Cancelled",
				voucher_no="SO-1",
				voucher_detail_no="soi-fg003198",
			),
		):
			_sync_dn_item_warehouse_to_reservation(doc)

		self.assertEqual(row.warehouse, "Finished Goods - MUPL")

	def test_patched_validate_allows_cancelled_batch_warehouse_without_custom_sre(self):
		"""Mill EXTRA SRE cancelled — cannot save on custom_sre; batch lookup must allow WH."""
		row = self._row(
			item_code="FG003197",
			warehouse="For Mill (EXTRA) - MUPL",
			batch_no="MUBT-08069",
			custom_sre=None,
			against_sales_order="SO-1",
			so_detail="soi-1",
			idx=6,
		)
		doc = self._doc([row])
		doc.validate_against_stock_reservation_entries = (
			patched_validate.__get__(doc, type(doc))
		)
		reserved = ["Finished Goods - MUPL"]

		with patch(
			"erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry.get_sre_reserved_warehouses_for_voucher",
			return_value=reserved,
		), patch(
			"madhav.doc_events.delivery_note._sre_warehouse_for_batch_including_cancelled",
			return_value="For Mill (EXTRA) - MUPL",
		), patch(
			"madhav.doc_events.delivery_note._warehouse_from_custom_sre",
			return_value=None,
		):
			doc.validate_against_stock_reservation_entries()

		self.assertEqual(row.warehouse, "For Mill (EXTRA) - MUPL")



if __name__ == "__main__":
	unittest.main()
