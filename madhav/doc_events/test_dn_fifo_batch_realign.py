# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

"""Unit tests: replace FIFO batch with reserved SRE batch on DN."""

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from madhav.doc_events.delivery_note import populate_missing_batch_bundle


class TestDNFifoBatchRealign(FrappeTestCase):
	def test_clears_fifo_batch_not_on_active_sre(self):
		"""SO-linked row with a non-reserved FIFO batch must be rebuilt."""
		row = SimpleNamespace(
			against_sales_order="SO-1",
			so_detail="soi-1",
			item_code="FG001",
			batch_no="FIFO-OLD",
			serial_and_batch_bundle=None,
			warehouse="Finished Goods - MUPL",
			custom_sre=None,
		)
		doc = SimpleNamespace(items=[row])

		with patch(
			"madhav.doc_events.delivery_note._batch_is_reserved_for_so_line",
			return_value=False,
		), patch(
			"madhav.doc_events.delivery_note._so_line_has_active_batch_sre_in_warehouse",
			return_value=True,
		), patch(
			"frappe.get_meta",
			return_value=SimpleNamespace(has_field=lambda *a, **k: True),
		), patch(
			"frappe.get_all",
			return_value=[],
		):
			populate_missing_batch_bundle(doc)

		self.assertIsNone(row.batch_no)

	def test_keeps_batch_when_still_reserved(self):
		row = SimpleNamespace(
			against_sales_order="SO-1",
			so_detail="soi-1",
			item_code="FG001",
			batch_no="RESERVED-BATCH",
			serial_and_batch_bundle=None,
			warehouse="For Mill (EXTRA) - MUPL",
			custom_sre=None,
		)
		doc = SimpleNamespace(items=[row])

		with patch(
			"madhav.doc_events.delivery_note._batch_is_reserved_for_so_line",
			return_value=True,
		), patch(
			"frappe.get_meta",
			return_value=SimpleNamespace(has_field=lambda *a, **k: True),
		), patch(
			"madhav.doc_events.delivery_note._stamp_custom_sre_from_batch",
		) as stamp:
			populate_missing_batch_bundle(doc)
			stamp.assert_called_once_with(row)

		self.assertEqual(row.batch_no, "RESERVED-BATCH")

	def test_keeps_physical_batch_when_no_sre_in_row_warehouse(self):
		"""Mill EXTRA batch after BWRT SRE cancel — do not clear and steal FG SREs."""
		row = SimpleNamespace(
			against_sales_order="SO-1",
			so_detail="soi-1",
			item_code="FG001",
			batch_no="MILL-BATCH",
			serial_and_batch_bundle=None,
			warehouse="For Mill (EXTRA) - MUPL",
			custom_sre=None,
		)
		doc = SimpleNamespace(items=[row])

		with patch(
			"madhav.doc_events.delivery_note._batch_is_reserved_for_so_line",
			return_value=False,
		), patch(
			"madhav.doc_events.delivery_note._so_line_has_active_batch_sre_in_warehouse",
			return_value=False,
		), patch(
			"frappe.get_meta",
			return_value=SimpleNamespace(has_field=lambda *a, **k: True),
		), patch(
			"frappe.get_all",
		) as get_all, patch(
			"madhav.doc_events.delivery_note._stamp_custom_sre_from_batch",
		) as stamp:
			populate_missing_batch_bundle(doc)
			get_all.assert_not_called()
			stamp.assert_called_once_with(row)

		self.assertEqual(row.batch_no, "MILL-BATCH")

