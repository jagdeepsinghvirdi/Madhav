# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

"""Unit tests: DN piece cap before submit validate."""

from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from madhav.doc_events.delivery_note import (
	_cap_dn_row_pieces_to_available,
	_validate_piece_availability,
)


class TestDNPieceCap(FrappeTestCase):
	def test_cap_reduces_invoice_pieces_to_batch_available(self):
		"""Deliver-as-Qty asked for 8; batch only holds 4 — must cap before validate."""
		row = SimpleNamespace(
			item_code="FG003198",
			warehouse="For Mill (EXTRA) - MUPL",
			batch_no="MUBT-10087",
			pieces=8,
			custom_deliver_as_qty=1,
		)
		doc = SimpleNamespace(items=[row])

		with patch(
			"madhav.doc_events.delivery_note._batch_available_pieces_for_row",
			return_value=4,
		):
			_cap_dn_row_pieces_to_available(doc)
			_validate_piece_availability(doc)

		self.assertEqual(row.pieces, 4)

	def test_cap_skips_non_deliver_as_qty_rows(self):
		"""Normal reserved DN must keep explicit pieces (hard error path)."""
		row = SimpleNamespace(
			item_code="FG003198",
			warehouse="For Mill (EXTRA) - MUPL",
			batch_no="MUBT-10087",
			pieces=8,
			custom_deliver_as_qty=0,
		)
		doc = SimpleNamespace(items=[row])

		with patch(
			"madhav.doc_events.delivery_note._batch_available_pieces_for_row",
			return_value=4,
		):
			_cap_dn_row_pieces_to_available(doc)

		self.assertEqual(row.pieces, 8)

	def test_cap_shares_budget_across_rows_on_same_batch(self):
		r1 = SimpleNamespace(
			item_code="FG002776",
			warehouse="Finished Goods - MUPL",
			batch_no="BATCH-A",
			pieces=3,
			custom_deliver_as_qty=1,
		)
		r2 = SimpleNamespace(
			item_code="FG002776",
			warehouse="Finished Goods - MUPL",
			batch_no="BATCH-A",
			pieces=19,
			custom_deliver_as_qty=1,
		)
		doc = SimpleNamespace(items=[r1, r2])

		with patch(
			"madhav.doc_events.delivery_note._batch_available_pieces_for_row",
			return_value=10,
		):
			_cap_dn_row_pieces_to_available(doc)

		self.assertEqual(r1.pieces, 3)
		self.assertEqual(r2.pieces, 7)
