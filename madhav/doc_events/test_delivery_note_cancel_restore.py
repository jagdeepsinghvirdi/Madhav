# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

"""Unit tests for DN cancel SRE restore helpers."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from madhav.doc_events.delivery_note import (
	_sort_snapshots_for_restore,
	_voucher_reservation_headroom,
)


class TestDNCancelSRERestoreHelpers(FrappeTestCase):
	def test_sort_restores_primary_reservation_before_bwrt(self):
		snaps = [
			{
				"name": "SRE-BWRT",
				"from_voucher_type": "Batch Wise Reservation Tool",
				"reserved_qty": 1.145,
			},
			{
				"name": "SRE-FWO",
				"from_voucher_type": "Finish Work Order",
				"reserved_qty": 15.0,
			},
		]
		ordered = _sort_snapshots_for_restore(snaps)
		self.assertEqual([s["name"] for s in ordered], ["SRE-FWO", "SRE-BWRT"])

	def test_voucher_headroom_is_line_wide_not_warehouse(self):
		# Simulates: SO line qty 15, already restored 1.145 elsewhere → room 13.855
		with patch(
			"madhav.doc_events.delivery_note._delivered_qty_excluding_dn",
			return_value=0,
		), patch(
			"madhav.doc_events.delivery_note._get_active_reserved_qty",
			return_value=1.145,
		), patch(
			"madhav.doc_events.delivery_note.frappe.db.get_single_value",
			return_value=0,
		):
			room = _voucher_reservation_headroom("SO-1", "soi-1", 15.0, exclude_dn="DN-1")
			self.assertAlmostEqual(flt(room), 13.855)

	def test_voucher_headroom_zero_when_fully_reserved(self):
		with patch(
			"madhav.doc_events.delivery_note._delivered_qty_excluding_dn",
			return_value=0,
		), patch(
			"madhav.doc_events.delivery_note._get_active_reserved_qty",
			return_value=15.0,
		), patch(
			"madhav.doc_events.delivery_note.frappe.db.get_single_value",
			return_value=0,
		):
			room = _voucher_reservation_headroom("SO-1", "soi-1", 15.0)
			self.assertEqual(room, 0)

	def test_voucher_headroom_does_not_count_other_dn_delivery_twice(self):
		"""SO qty 10, DN-A delivered 4, DN-B delivered 6 and is cancelling.

		DN-A's entry is fully delivered, so its undelivered reservation is
		0 and the only claim on the line is DN-A's 4 delivered units. All
		6 of DN-B's units must be restorable — counting the gross reserved
		qty instead left just 2.
		"""
		with patch(
			"madhav.doc_events.delivery_note._delivered_qty_excluding_dn",
			return_value=4.0,
		), patch(
			"madhav.doc_events.delivery_note._get_active_reserved_qty",
			return_value=0.0,
		), patch(
			"madhav.doc_events.delivery_note.frappe.db.get_single_value",
			return_value=0,
		):
			room = _voucher_reservation_headroom("SO-1", "soi-1", 10.0, exclude_dn="DN-B")
			self.assertAlmostEqual(flt(room), 6.0)
