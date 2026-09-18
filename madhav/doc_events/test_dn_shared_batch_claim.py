# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

"""Unit tests: shared batch budget across Deliver-as-Qty DN rows."""

from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from madhav.doc_events.delivery_note import (
	_expand_entries_within_live_stock,
	_register_dn_row_batch_claims,
	get_available_qty_for_item,
)


class TestDNSharedBatchClaim(FrappeTestCase):
	def test_available_qty_subtracts_sibling_claims(self):
		"""Two DaQ rows on one batch must not each see the full live balance."""
		row = SimpleNamespace(
			item_code="FG003197",
			warehouse="Finished Goods - MUPL",
			batch_no="MUBT-10096",
			serial_and_batch_bundle=None,
			against_sales_order=None,
			so_detail=None,
		)
		claimed = {("FG003197", "Finished Goods - MUPL", "MUBT-10096"): 0.390}

		with patch(
			"madhav.doc_events.delivery_note._has_live_batch_ledger",
			return_value=True,
		), patch(
			"madhav.doc_events.delivery_note.get_batch_qty_from_sle",
			return_value=0.765,
		):
			avail = get_available_qty_for_item(row, already_claimed=claimed)

		self.assertAlmostEqual(avail, 0.375, places=6)

	def test_expand_caps_second_row_to_remaining_budget(self):
		"""Row1 claimed 0.390 of 0.765; row2 expanding to 0.390 must stop at 0.375."""
		item = SimpleNamespace(
			item_code="FG003197",
			warehouse="Finished Goods - MUPL",
			serial_and_batch_bundle="SABB-00023753",
		)
		entry = SimpleNamespace(batch_no="MUBT-10096", qty=-0.383)
		claimed = {("FG003197", "Finished Goods - MUPL", "MUBT-10096"): 0.390}

		with patch(
			"madhav.doc_events.delivery_note._has_live_batch_ledger",
			return_value=True,
		), patch(
			"madhav.doc_events.delivery_note.get_batch_qty_from_sle",
			return_value=0.765,
		):
			desired = _expand_entries_within_live_stock(
				item, [entry], [0.383], 0.390, already_claimed=claimed
			)

		self.assertEqual(len(desired), 1)
		self.assertAlmostEqual(desired[0], 0.375, places=6)
		self.assertLess(sum(desired), 0.390)

	def test_two_row_register_then_remaining_matches_dn_case(self):
		"""Mirrors DN-26-00333: 0.382+0.383 bundles on a 0.765 batch."""
		wh = "Finished Goods - MUPL"
		item = "FG003197"
		batch = "MUBT-10096"
		claimed = {}

		r1 = SimpleNamespace(
			item_code=item,
			warehouse=wh,
			batch_no=None,
			serial_and_batch_bundle="SABB-1",
			stock_qty=0,
			qty=0.382,
		)
		r2 = SimpleNamespace(
			item_code=item,
			warehouse=wh,
			batch_no=None,
			serial_and_batch_bundle="SABB-2",
			stock_qty=0,
			qty=0.383,
		)

		with patch(
			"madhav.doc_events.delivery_note.frappe.get_all",
			side_effect=[
				[SimpleNamespace(batch_no=batch, qty=-0.382)],
				[SimpleNamespace(batch_no=batch, qty=-0.383)],
			],
		):
			_register_dn_row_batch_claims(claimed, r1)
			_register_dn_row_batch_claims(claimed, r2)

		self.assertAlmostEqual(claimed[(item, wh, batch)], 0.765, places=6)

		row_check = SimpleNamespace(
			item_code=item,
			warehouse=wh,
			batch_no=batch,
			serial_and_batch_bundle=None,
			against_sales_order=None,
			so_detail=None,
		)
		with patch(
			"madhav.doc_events.delivery_note._has_live_batch_ledger",
			return_value=True,
		), patch(
			"madhav.doc_events.delivery_note.get_batch_qty_from_sle",
			return_value=0.765,
		):
			# After both physical claims, nothing left to expand into invoice
			avail = get_available_qty_for_item(row_check, already_claimed=claimed)

		self.assertAlmostEqual(flt(avail), 0.0, places=6)
