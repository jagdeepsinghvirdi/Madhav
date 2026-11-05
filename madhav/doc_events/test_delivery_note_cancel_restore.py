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

	def test_capacity_error_detected_for_allowed_qty_message(self):
		from madhav.doc_events.delivery_note import _is_reservation_capacity_error

		self.assertTrue(
			_is_reservation_capacity_error(
				Exception("Cannot reserve more than Allowed Qty 1.177 Tonne for Item FG003197")
			)
		)
		self.assertFalse(_is_reservation_capacity_error(Exception("Serial No missing")))

	def test_snapshot_qty_within_allowed_qty_uses_min_of_stock_and_so(self):
		from madhav.doc_events.delivery_note import _snapshot_qty_within_allowed_qty

		with patch(
			"erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry.get_available_qty_to_reserve",
			return_value=1.56,
		), patch(
			"madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool.get_so_line_allowance",
			return_value=1.177,
		):
			self.assertFalse(
				_snapshot_qty_within_allowed_qty(
					"FG003197",
					"For Mill (EXTRA) - MUPL",
					"MU-SO26-00739",
					"v04epfe1kp",
					1.56,
					"Batch Wise Reservation Tool",
					0,
				)
			)
			self.assertTrue(
				_snapshot_qty_within_allowed_qty(
					"FG003197",
					"For Mill (EXTRA) - MUPL",
					"MU-SO26-00739",
					"v04epfe1kp",
					1.177,
					"Batch Wise Reservation Tool",
					0,
				)
			)


class _FakeSRE(frappe._dict):
	"""Stand-in for a new Stock Reservation Entry (no DB writes)."""

	def __init__(self):
		super().__init__(sb_entries=[], flags=frappe._dict(), name=None)

	def append(self, table, row):
		row = frappe._dict(row)
		self[table].append(row)
		return row

	def insert(self):
		self.name = "SRE-NEW"

	def submit(self):
		pass

	def db_set(self, field, value, **kwargs):
		self[field] = value

	def reload(self):
		pass

	def update_status(self):
		pass

	def update_reserved_qty_in_voucher(self):
		pass

	def update_reserved_stock_in_bin(self):
		pass


def _snap(qty, batch_qty=None, pieces=0):
	snap = {
		"name": "SRE-OLD",
		"item_code": "FG000531",
		"warehouse": "For Mill (EXTRA) - MUPL",
		"company": "MADHAV UDYOG PRIVATE LIMITED",
		"stock_uom": "Tonne",
		"voucher_type": "Sales Order",
		"voucher_no": "SO-TEST-X",
		"voucher_detail_no": "soi-test-x",
		"voucher_qty": 0.643,
		"reserved_qty": qty,
		"delivered_qty": 0,
		"from_voucher_type": "Batch Wise Reservation Tool",
		"from_voucher_no": "BWRT-TEST-X",
		"custom_is_tolerance": 0,
		"reservation_based_on": "Qty",
		"sb_entries": [],
	}
	if batch_qty is not None:
		snap["reservation_based_on"] = "Serial and Batch"
		snap["sb_entries"] = [
			{"batch_no": "MUBT-TEST-1", "qty": batch_qty, "delivered_qty": 0,
			 "warehouse": "For Mill (EXTRA) - MUPL", "pieces": pieces, "length": 6, "section_weight": 13.4}
		]
	return snap


class TestDNCancelRestoresExactReservation(FrappeTestCase):
	"""Client rule: DN cancel brings back the qty that was reserved before the DN,
	under the same allowance it was created with - not the stricter per-line cap."""

	DN = "madhav.doc_events.delivery_note"

	def _recreate(self, snap, allowed, batch_free=None):
		from madhav.doc_events.delivery_note import _recreate_stock_reservation_from_snapshot

		fake = _FakeSRE()
		patches = [
			patch(f"{self.DN}._snapshot_allowed_qty", return_value=allowed),
			# Old per-line cap (0.643 x 1.2 = 0.7716) must no longer cut the restore.
			patch(f"{self.DN}._voucher_reservation_headroom", return_value=0.7716),
			patch("frappe.new_doc", return_value=fake),
		]
		if batch_free is not None:
			patches.append(
				patch(
					"madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool.get_batch_available_qty",
					return_value=batch_free,
				)
			)
		for p in patches:
			p.start()
		try:
			restored = _recreate_stock_reservation_from_snapshot(snap, exclude_dn="DN-X")
		finally:
			for p in reversed(patches):
				p.stop()
		return restored, fake

	def test_restores_full_snapshot_above_line_cap(self):
		# BWRT reserved 2.55 on a 0.643 line from the shared SO pool.
		restored, fake = self._recreate(_snap(2.55), allowed=3.73)
		self.assertAlmostEqual(restored, 2.55, places=3)
		self.assertAlmostEqual(flt(fake.reserved_qty), 2.55, places=3)

	def test_batch_restore_keeps_full_qty_and_pieces(self):
		restored, fake = self._recreate(_snap(2.55, batch_qty=2.55, pieces=16), allowed=3.73, batch_free=2.55)
		self.assertAlmostEqual(restored, 2.55, places=3)
		self.assertEqual(len(fake.sb_entries), 1)
		self.assertAlmostEqual(flt(fake.sb_entries[0].qty), 2.55, places=3)
		self.assertEqual(fake.sb_entries[0].pieces, 16)

	def test_capped_when_allowance_used_meanwhile(self):
		restored, _fake = self._recreate(_snap(2.55), allowed=1.0)
		self.assertAlmostEqual(restored, 1.0, places=3)

	def test_nothing_restored_when_no_allowance(self):
		restored, fake = self._recreate(_snap(2.55), allowed=0)
		self.assertEqual(restored, 0)
		self.assertIsNone(fake.name)

	def test_batch_capped_to_free_batch_stock(self):
		restored, fake = self._recreate(_snap(2.55, batch_qty=2.55, pieces=16), allowed=3.73, batch_free=2.0)
		self.assertAlmostEqual(restored, 2.0, places=3)
		self.assertAlmostEqual(flt(fake.sb_entries[0].qty), 2.0, places=3)
		self.assertEqual(fake.sb_entries[0].pieces, round(16 * 2.0 / 2.55))

	def test_batch_cap_uses_open_qty_not_delivered_part(self):
		# 2.55 reserved, 1.0 already delivered: only 1.55 needs free stock.
		snap = _snap(2.55, batch_qty=2.55, pieces=16)
		snap["delivered_qty"] = 1.0
		snap["sb_entries"][0]["delivered_qty"] = 1.0
		with patch("frappe.db.set_value"):
			restored, fake = self._recreate(snap, allowed=3.73, batch_free=1.55)
		self.assertAlmostEqual(restored, 2.55, places=3)
		self.assertAlmostEqual(flt(fake.sb_entries[0].delivered_qty), 1.0, places=3)

	def test_same_batch_rows_share_free_stock(self):
		snap = _snap(2.0, batch_qty=1.0, pieces=6)
		snap["sb_entries"].append(dict(snap["sb_entries"][0]))
		restored, fake = self._recreate(snap, allowed=3.73, batch_free=1.5)
		self.assertAlmostEqual(restored, 1.5, places=3)
		self.assertEqual([round(flt(e.qty), 3) for e in fake.sb_entries], [1.0, 0.5])

	def test_partial_restore_notifies_user(self):
		from madhav.doc_events.delivery_note import restore_stock_reservations_after_cancel

		doc = frappe._dict(name="DN-X", items=[])
		snap = _snap(2.55)
		with patch(f"{self.DN}._load_cancelled_sre_snapshot", return_value=([snap], None)), patch(
			f"{self.DN}._snapshot_sre_still_active", return_value=False
		), patch(f"{self.DN}._recreate_stock_reservation_from_snapshot", return_value=1.0), patch(
			f"{self.DN}._notify_partial_reservation_restore"
		) as notify:
			restore_stock_reservations_after_cancel(doc)
		notify.assert_called_once()
		short = notify.call_args[0][1]
		self.assertEqual(len(short), 1)
		self.assertAlmostEqual(short[0][1], 1.0, places=3)

	def test_full_restore_does_not_notify(self):
		from madhav.doc_events.delivery_note import restore_stock_reservations_after_cancel

		doc = frappe._dict(name="DN-X", items=[])
		snap = _snap(2.55)
		with patch(f"{self.DN}._load_cancelled_sre_snapshot", return_value=([snap], None)), patch(
			f"{self.DN}._snapshot_sre_still_active", return_value=False
		), patch(f"{self.DN}._recreate_stock_reservation_from_snapshot", return_value=2.55), patch(
			f"{self.DN}._notify_partial_reservation_restore"
		) as notify:
			restore_stock_reservations_after_cancel(doc)
		notify.assert_not_called()


if __name__ == "__main__":
	unittest.main()
