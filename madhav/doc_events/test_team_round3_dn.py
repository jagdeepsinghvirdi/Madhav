# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

"""Team feedback 14 Nov 2026, Delivery Note items 4, 5 and 6.

4. Total Invoice Qty / Total (INR) as per Invoice Qty, display only.
5. Saved draft: changing a row Qty resizes its draft Serial and Batch Bundle
   (ERPNext otherwise refuses: "Total quantity 1.308 in the Serial and Batch
   Bundle ... does not match with the quantity 1").
6. Get Items From → Sales Order brings the Sales Order's taxes and charges.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from madhav.doc_events import delivery_note as dn

MOD = "madhav.doc_events.delivery_note"


# ------------------------------------------------------------------ item 4
class TestInvoiceQtyTotals(FrappeTestCase):
	def _totals(self, rows):
		doc = frappe._dict(items=rows)
		meta = MagicMock()
		meta.has_field.return_value = True
		with patch.object(dn.frappe, "get_meta", return_value=meta):
			dn.set_invoice_qty_totals(doc)
		return doc

	def test_deliver_as_qty_rows_use_invoice_qty_others_use_qty(self):
		doc = self._totals([
			SimpleNamespace(qty=1.308, invoice_qty=1.35, custom_deliver_as_qty=1, base_rate=50000),
			SimpleNamespace(qty=2.0, invoice_qty=0, custom_deliver_as_qty=1, base_rate=40000),
			SimpleNamespace(qty=0.5, invoice_qty=0.7, custom_deliver_as_qty=0, base_rate=60000),
		])
		self.assertEqual(doc.total_invoice_qty, 3.85)
		self.assertEqual(doc.total_as_per_invoice_qty, 1.35 * 50000 + 2.0 * 40000 + 0.5 * 60000)

	def test_existing_totals_untouched(self):
		doc = frappe._dict(items=[SimpleNamespace(qty=1.0, invoice_qty=1.2, custom_deliver_as_qty=1, base_rate=10)],
			total_qty=1.0, base_total=10)
		meta = MagicMock()
		meta.has_field.return_value = True
		with patch.object(dn.frappe, "get_meta", return_value=meta):
			dn.set_invoice_qty_totals(doc)
		self.assertEqual((doc.total_qty, doc.base_total), (1.0, 10))
		self.assertEqual((doc.total_invoice_qty, doc.total_as_per_invoice_qty), (1.2, 12))

	def test_site_without_fields_is_left_alone(self):
		doc = frappe._dict(items=[SimpleNamespace(qty=1.0, invoice_qty=0, custom_deliver_as_qty=0, base_rate=1)])
		meta = MagicMock()
		meta.has_field.return_value = False
		with patch.object(dn.frappe, "get_meta", return_value=meta):
			dn.set_invoice_qty_totals(doc)
		self.assertNotIn("total_invoice_qty", doc)


# ------------------------------------------------------------------ item 5
class FakeBundle(SimpleNamespace):
	name = "SABB-00037964"

	def save(self, **kw):
		self.saved = getattr(self, "saved", 0) + 1

	def reload(self):
		pass


def _entry(batch_no, qty, pieces, length=6.575, sw=16.58):
	return SimpleNamespace(batch_no=batch_no, qty=-qty, pieces=pieces, length=length,
		section_weight=sw, warehouse="Finished Goods RD - MUPL")


def _item(**kw):
	row = dict(name="ec2oq82e98", idx=1, item_code="FG000569", against_sales_order="MU-SO26-00380",
		so_detail="16e8ef6fv1", serial_and_batch_bundle="SABB-00037964", warehouse="Finished Goods RD - MUPL",
		qty=1.0, conversion_factor=1, pieces=12, rate=50000, section_weight=16.58, stock_qty=1.308)
	row.update(kw)
	return SimpleNamespace(**row)


class _Doc(SimpleNamespace):
	def is_new(self):
		return getattr(self, "_new", False)

	def get(self, key, default=None):
		return getattr(self, key, default)


def _doc(items, **kw):
	d = dict(name="JWD/26-27/0087", docstatus=0, is_return=0, conversion_rate=1, items=items)
	d.update(kw)
	return _Doc(**d)


def _run_sync(doc, bundle, info=None, cap=10.0):
	info = info or frappe._dict(docstatus=0, total_qty=-1.308, voucher_no=doc.name, voucher_detail_no="ec2oq82e98")
	with patch.object(dn.frappe.db, "get_value", return_value=info), \
		patch.object(dn.frappe, "get_doc", return_value=bundle), \
		patch(f"{MOD}._row_batch_cap", return_value=cap), \
		patch(f"{MOD}.get_batch_available_pieces", return_value=100), \
		patch(f"{MOD}.resolve_entry_length", side_effect=lambda e, *a: e.length), \
		patch(f"{MOD}.resolve_entry_section_weight", side_effect=lambda e, *a: e.section_weight), \
		patch(f"{MOD}.sync_dn_item_length_from_entries", return_value=None), \
		patch(f"{MOD}._delivery_tolerance_percent", return_value=3):
		dn.sync_draft_bundles_to_row_qty(doc)


class TestDraftBundleFollowsRowQty(FrappeTestCase):
	def test_lower_qty_shrinks_bundle_and_pieces(self):
		bundle = FakeBundle(entries=[_entry("MUBT-12505", 1.308, 12)], total_qty=-1.308, flags=frappe._dict())
		item = _item(qty=1.0)
		_run_sync(_doc([item]), bundle)
		self.assertAlmostEqual(bundle.total_qty, -1.0)
		self.assertAlmostEqual(bundle.entries[0].qty, -1.0)
		self.assertEqual(bundle.saved, 1)
		# 1.0 t / (6.575 m × 16.58 kg/m) = 9.17 pieces; the app's whole-piece
		# rule (int_pieces_from_qty) rounds up, as on insert.
		self.assertEqual(item.pieces, 10)
		self.assertEqual(bundle.entries[0].pieces, 10)

	def test_back_up_to_reserved_qty_restores_pieces(self):
		bundle = FakeBundle(entries=[_entry("MUBT-12505", 1.0, 9)], total_qty=-1.0, flags=frappe._dict())
		item = _item(qty=1.308, pieces=9)
		info = frappe._dict(docstatus=0, total_qty=-1.0, voucher_no="JWD/26-27/0087", voucher_detail_no="ec2oq82e98")
		_run_sync(_doc([item]), bundle, info=info, cap=1.308)
		self.assertAlmostEqual(bundle.total_qty, -1.308)
		self.assertEqual(item.pieces, 12)

	def test_multi_batch_bundle_split_in_proportion(self):
		bundle = FakeBundle(entries=[_entry("B1", 1.0, 9), _entry("B2", 0.5, 5)], total_qty=-1.5, flags=frappe._dict())
		item = _item(qty=0.75, pieces=14)
		info = frappe._dict(docstatus=0, total_qty=-1.5, voucher_no="JWD/26-27/0087", voucher_detail_no="ec2oq82e98")
		_run_sync(_doc([item]), bundle, info=info)
		self.assertAlmostEqual(bundle.entries[0].qty, -0.5)
		self.assertAlmostEqual(bundle.entries[1].qty, -0.25)

	def test_typed_pieces_win(self):
		bundle = FakeBundle(entries=[_entry("MUBT-12505", 1.308, 12)], total_qty=-1.308, flags=frappe._dict())
		item = _item(qty=1.0, pieces=8)
		_run_sync(_doc([item]), bundle)
		self.assertEqual(item.pieces, 8)

	def test_more_than_batch_can_give_is_refused(self):
		bundle = FakeBundle(entries=[_entry("MUBT-12505", 1.308, 12)], total_qty=-1.308, flags=frappe._dict())
		item = _item(qty=2.0)
		with self.assertRaises(frappe.ValidationError) as cm:
			_run_sync(_doc([item]), bundle, cap=1.308)
		self.assertIn("MUBT-12505", str(cm.exception))
		self.assertFalse(getattr(bundle, "saved", 0))

	def test_unchanged_row_is_not_touched(self):
		bundle = FakeBundle(entries=[_entry("MUBT-12505", 1.308, 12)], total_qty=-1.308, flags=frappe._dict())
		item = _item(qty=1.308)
		_run_sync(_doc([item]), bundle)
		self.assertFalse(getattr(bundle, "saved", 0))
		self.assertEqual(item.pieces, 12)

	def test_new_submitted_and_return_docs_skip(self):
		for kw in ({"_new": True}, {"docstatus": 1}, {"is_return": 1}):
			bundle = FakeBundle(entries=[_entry("MUBT-12505", 1.308, 12)], total_qty=-1.308, flags=frappe._dict())
			_run_sync(_doc([_item(qty=1.0)], **kw), bundle)
			self.assertFalse(getattr(bundle, "saved", 0), kw)

	def test_submitted_or_foreign_bundle_is_not_touched(self):
		for info in (
			frappe._dict(docstatus=1, total_qty=-1.308, voucher_no="JWD/26-27/0087", voucher_detail_no="ec2oq82e98"),
			frappe._dict(docstatus=0, total_qty=-1.308, voucher_no="OTHER-DN", voucher_detail_no="ec2oq82e98"),
			frappe._dict(docstatus=0, total_qty=-1.308, voucher_no="JWD/26-27/0087", voucher_detail_no="other-row"),
		):
			bundle = FakeBundle(entries=[_entry("MUBT-12505", 1.308, 12)], total_qty=-1.308, flags=frappe._dict())
			_run_sync(_doc([_item(qty=1.0)]), bundle, info=info)
			self.assertFalse(getattr(bundle, "saved", 0), info)

	def test_needs_resize_tolerance(self):
		self.assertFalse(dn.bundle_needs_resize(-1.308, 1.3084))
		self.assertTrue(dn.bundle_needs_resize(-1.308, 1.0))


# ------------------------------------------------------------------ item 6
class TestGetItemsMapsSalesOrderTaxes(FrappeTestCase):
	def test_mapping_includes_taxes_table(self):
		captured = {}

		def fake_mapped(source_dt, source_name, mapping, target_doc=None, *a, **kw):
			captured.setdefault("mappings", []).append(mapping)
			return MagicMock(items=[], get=lambda *a, **k: [])

		so = SimpleNamespace(name="MU-SO26-00487", items=[], deliver_as_qty=1)
		with patch.object(dn.frappe, "get_doc", return_value=so), \
			patch(f"{MOD}.get_mapped_doc", side_effect=fake_mapped), \
			patch(f"{MOD}._restore_dn_warehouses_from_custom_sre", return_value=None), \
			patch(f"{MOD}.make_packing_list", return_value=None):
			dn.make_delivery_note_custom("MU-SO26-00487", kwargs={"for_reserved_stock": False})
		first = captured["mappings"][0]
		self.assertIn("Sales Taxes and Charges", first)
		self.assertEqual(first["Sales Taxes and Charges"]["doctype"], "Sales Taxes and Charges")


class TestBatchCapAcrossRows(FrappeTestCase):
	def test_other_row_on_same_batch_reduces_the_cap(self):
		bundle = FakeBundle(entries=[_entry("MUBT-12505", 1.0, 10)], total_qty=-1.0, flags=frappe._dict())
		item = _item(qty=1.3)
		other = SimpleNamespace(name="row2", serial_and_batch_bundle="SABB-OTHER", warehouse="Finished Goods RD - MUPL", batch_no=None, qty=0.5)
		doc = _doc([item, other])
		with patch(f"{MOD}._row_batch_cap", return_value=1.5), \
			patch.object(dn.frappe, "get_all", return_value=[frappe._dict(batch_no="MUBT-12505", qty=-0.5, warehouse="Finished Goods RD - MUPL")]):
			with self.assertRaises(frappe.ValidationError):
				dn._validate_bundle_increase(item, bundle, 1.3, doc)  # 1.5 cap - 0.5 other row = 1.0 < 1.3

	def test_other_uom_row_is_left_to_erpnext(self):
		bundle = FakeBundle(entries=[_entry("MUBT-12505", 1.308, 12)], total_qty=-1.308, flags=frappe._dict())
		_run_sync(_doc([_item(qty=1.0, conversion_factor=1000)]), bundle)
		self.assertFalse(getattr(bundle, "saved", 0))
