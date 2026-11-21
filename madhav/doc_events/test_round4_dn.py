# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

"""Round 4, team batch sheet Part A (Nov 2026): Delivery Note.

1. Get Items From → Sales Order: one row per item + length, pieces added up,
   batches behind it in the bundle, oldest first.
2. Fewer pieces (or less qty) on a draft: the bundle is rebuilt from the
   oldest batch first; the rest stays reserved for the Sales Order line.

The examples are the team's own (B-001 8 m 10 pcs 1 Nov, B-002 8 m 5 pcs
5 Nov, B-003 7.5 m 2 pcs 3 Nov).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from madhav.doc_events import delivery_note as dn

MOD = "madhav.doc_events.delivery_note"
POOL_MOD = "madhav.madhav.utils.reserved_length_groups"


def pool_entry(batch, length, pieces, qty, date, sre):
	return frappe._dict(
		sre=sre, batch_no=batch, warehouse="Finished Goods RD - MUPL", qty=qty, pieces=pieces,
		length=length, section_weight=25.0, assorted_length=None, batch_date=date,
		creation=date + " 10:00:00",
	)


def team_pool():
	return [
		pool_entry("B-001", 8, 10, 2.0, "2026-11-01", "SRE-1"),
		pool_entry("B-002", 8, 5, 1.0, "2026-11-05", "SRE-2"),
		pool_entry("B-003", 7.5, 2, 0.375, "2026-11-03", "SRE-3"),
	]


class Row(SimpleNamespace):
	"""Stand-in for a Delivery Note Item: every field the code may stamp exists."""

	def __init__(self, **kw):
		base = dict(
			name=None, idx=1, item_code="FG001109", qty=0, stock_qty=0, conversion_factor=1, rate=50000,
			amount=0, base_amount=0, warehouse=None, serial_and_batch_bundle=None, batch_no=None,
			pieces=0, lengthpieces_so=0, length_size=0, length_sizeso=0, average_length=0,
			section_weight=0, custom_sre=None, custom_reserved_batch_pool=None, sw_qty=0,
			custom_deliver_as_qty=0, against_sales_order="MU-SO26-00600", so_detail="soline1", branch=None,
		)
		base.update(kw)
		super().__init__(**base)

	def set(self, key, value):
		setattr(self, key, value)

	def get(self, key, default=None):
		return getattr(self, key, default)


class TargetDoc(SimpleNamespace):
	def append(self, field, row):
		getattr(self, field).append(row)
		return row

	def run_method(self, *a, **k):
		pass

	def get(self, key, default=None):
		return getattr(self, key, default)


def _meta():
	meta = MagicMock()
	meta.has_field.return_value = True
	meta.get_field.return_value = SimpleNamespace(fieldtype="Data", options="")
	return meta


class TestGetItemsOneRowPerLength(FrappeTestCase):
	def _fetch(self, sres, pool):
		so_item = frappe._dict(name="soline1", item_code="FG001109", length_size=8, section_weight=25.0)
		so = SimpleNamespace(name="MU-SO26-00600", items=[so_item], deliver_as_qty=0)
		bundles = []

		def fake_mapped(source_dt, source_name, mapping, target_doc=None, *a, **kw):
			if source_dt == "Sales Order":
				return TargetDoc(items=[], taxes=[])
			return Row(so_detail=source_name)

		def fake_bundle(item_code, warehouse, allocations):
			bundles.append([(a["batch_no"], a["take_pieces"], a["take_qty"]) for a in allocations])
			return f"SABB-{len(bundles)}"

		with patch.object(dn.frappe, "get_doc", return_value=so), \
			patch.object(dn.frappe, "get_meta", return_value=_meta()), \
			patch(f"{MOD}.get_mapped_doc", side_effect=fake_mapped), \
			patch(f"{MOD}.get_sre_details_for_voucher", return_value=sres), \
			patch(f"{POOL_MOD}.get_reserved_pool", return_value=pool), \
			patch(f"{MOD}._make_dn_bundle_from_allocations", side_effect=fake_bundle), \
			patch(f"{MOD}._dn_sw_qty", return_value=0), \
			patch(f"{MOD}.batch_section_weight", return_value=0), \
			patch(f"{MOD}._restore_dn_warehouses_from_custom_sre", return_value=None), \
			patch(f"{MOD}.make_packing_list", return_value=None):
			doc = dn.make_delivery_note_custom(
				"MU-SO26-00600", kwargs={"for_reserved_stock": True, "selected_sre": ["soline1"]}
			)
		return doc, bundles

	def _sres(self):
		return [
			frappe._dict(name=n, voucher_detail_no="soline1", reservation_based_on="Serial and Batch",
				reserved_qty=q, delivered_qty=0, warehouse="Finished Goods RD - MUPL")
			for n, q in (("SRE-1", 2.0), ("SRE-2", 1.0), ("SRE-3", 0.375))
		]

	def test_team_example_two_rows_not_three(self):
		doc, bundles = self._fetch(self._sres(), team_pool())
		rows = doc.items
		self.assertEqual([(r.length_size, r.pieces) for r in rows], [(8.0, 15), (7.5, 2)])
		self.assertAlmostEqual(rows[0].qty, 3.0)
		self.assertAlmostEqual(rows[1].qty, 0.375)
		# Batches stay behind the row, oldest first.
		self.assertEqual(bundles[0], [("B-001", 10, 2.0), ("B-002", 5, 1.0)])
		self.assertEqual(bundles[1], [("B-003", 2, 0.375)])
		self.assertEqual([r.idx for r in rows], [1, 2])

	def test_row_from_several_reservations_is_not_pinned_to_one(self):
		doc, _ = self._fetch(self._sres(), team_pool())
		eight, seven_half = doc.items
		self.assertIsNone(eight.custom_sre)  # submit matches SRE-1 and SRE-2 by batch
		self.assertEqual(seven_half.custom_sre, "SRE-3")
		self.assertIn("B-001", eight.custom_reserved_batch_pool)
		self.assertEqual(eight.serial_and_batch_bundle, "SABB-1")

	def test_qty_reservation_keeps_its_own_row(self):
		sres = self._sres()[:1] + [
			frappe._dict(name="SRE-Q", voucher_detail_no="soline1", reservation_based_on="Qty",
				reserved_qty=0.5, delivered_qty=0, warehouse="Finished Goods RD - MUPL")
		]
		with patch(f"{MOD}._apply_reserved_dims_to_dn_item", return_value=None), \
			patch(f"{MOD}._set_dn_assorted_length_from_sre", return_value=None):
			doc, _ = self._fetch(sres, team_pool()[:1])
		self.assertEqual(len(doc.items), 2)
		self.assertEqual(doc.items[1].custom_sre, "SRE-Q")
		self.assertAlmostEqual(doc.items[1].qty, 0.5)


# ------------------------------------------------------------------ draft rebuild
class FakeBundle(SimpleNamespace):
	def set(self, field, value):
		setattr(self, field, list(value))

	def append(self, field, value):
		getattr(self, field).append(SimpleNamespace(**value))

	def save(self, **kw):
		self.saved = getattr(self, "saved", 0) + 1

	def reload(self):
		pass


def _bundle(entries):
	return FakeBundle(
		name="SABB-1", docstatus=0, flags=frappe._dict(),
		entries=[SimpleNamespace(batch_no=b, qty=-q, pieces=p, length=8, section_weight=25.0,
			warehouse="Finished Goods RD - MUPL") for b, p, q in entries],
		total_qty=-sum(q for _b, _p, q in entries),
	)


def _eight_metre_row(**kw):
	row = dict(name="row1", serial_and_batch_bundle="SABB-1", warehouse="Finished Goods RD - MUPL",
		qty=3.0, stock_qty=3.0, pieces=15, length_size=8,
		custom_reserved_batch_pool='[{"batch_no": "B-001"}, {"batch_no": "B-002"}]')
	row.update(kw)
	return Row(**row)


class _Doc(SimpleNamespace):
	def get(self, key, default=None):
		return getattr(self, key, default)

	def is_new(self):
		return False


def _rebuild(item, bundle, pool=None, cap=10.0):
	doc = _Doc(name="DN-1", docstatus=0, is_return=0, conversion_rate=1, items=[item])
	pool = team_pool()[:2] if pool is None else pool
	with patch(f"{POOL_MOD}.get_reserved_pool", return_value=pool), \
		patch(f"{MOD}._other_rows_batch_use", return_value={}), \
		patch(f"{MOD}._row_batch_cap", return_value=cap), \
		patch(f"{MOD}.sync_dn_item_length_from_entries", return_value=None), \
		patch(f"{MOD}._dn_sw_qty", return_value=0), \
		patch.object(dn.frappe, "get_meta", return_value=_meta()):
		handled = dn.rebuild_row_bundle_fifo(doc, item, bundle)
	return handled


class TestOldestBatchFirstOnDraft(FrappeTestCase):
	def test_case_2_two_pieces_come_from_the_older_batch(self):
		bundle = _bundle([("B-001", 10, 2.0), ("B-002", 5, 1.0)])
		item = _eight_metre_row(pieces=2)
		self.assertTrue(_rebuild(item, bundle))
		self.assertEqual([(e.batch_no, e.pieces, -e.qty) for e in bundle.entries], [("B-001", 2, 0.4)])
		self.assertAlmostEqual(item.qty, 0.4)
		self.assertEqual(item.pieces, 2)
		self.assertAlmostEqual(bundle.total_qty, -0.4)
		self.assertEqual(bundle.saved, 1)

	def test_back_up_to_fifteen_takes_both_batches_again(self):
		bundle = _bundle([("B-001", 2, 0.4)])
		item = _eight_metre_row(pieces=15, qty=0.4)
		_rebuild(item, bundle)
		self.assertEqual([(e.batch_no, e.pieces) for e in bundle.entries], [("B-001", 10), ("B-002", 5)])
		self.assertAlmostEqual(item.qty, 3.0)

	def test_lower_qty_takes_weight_oldest_first(self):
		bundle = _bundle([("B-001", 10, 2.0), ("B-002", 5, 1.0)])
		item = _eight_metre_row(qty=2.5, stock_qty=2.5)
		_rebuild(item, bundle)
		self.assertEqual([(e.batch_no, -e.qty) for e in bundle.entries], [("B-001", 2.0), ("B-002", 0.5)])
		self.assertEqual(item.pieces, 13)  # 10 + a started 3 of B-002's 5

	def test_unchanged_row_is_not_rewritten(self):
		bundle = _bundle([("B-001", 10, 2.0), ("B-002", 5, 1.0)])
		item = _eight_metre_row()
		self.assertTrue(_rebuild(item, bundle))
		self.assertFalse(getattr(bundle, "saved", 0))

	def test_more_than_reserved_needs_free_stock_of_the_newest_batch(self):
		bundle = _bundle([("B-001", 10, 2.0), ("B-002", 5, 1.0)])
		item = _eight_metre_row(pieces=17)
		with self.assertRaises(frappe.ValidationError) as cm:
			_rebuild(item, bundle, cap=1.0)  # B-002: 1.0 reserved, no free stock
		self.assertIn("B-002", str(cm.exception))
		self.assertFalse(getattr(bundle, "saved", 0))

	def test_more_than_reserved_inside_free_stock_is_allowed(self):
		bundle = _bundle([("B-001", 10, 2.0), ("B-002", 5, 1.0)])
		item = _eight_metre_row(pieces=16)
		_rebuild(item, bundle, cap=1.5)
		self.assertEqual([(e.batch_no, e.pieces) for e in bundle.entries], [("B-001", 10), ("B-002", 6)])
		self.assertAlmostEqual(item.qty, 3.2)

	def test_row_without_reserved_pool_is_left_to_the_old_resize(self):
		bundle = _bundle([("B-001", 10, 2.0)])
		item = _eight_metre_row(pieces=2)
		self.assertFalse(_rebuild(item, bundle, pool=[]))
		self.assertFalse(getattr(bundle, "saved", 0))

	def test_older_rows_follow_qty_not_their_display_pieces(self):
		# Rows saved before round 4 have no pool record; their Pieces can differ
		# from the bundle (piece caps), so only a Qty change re-takes batches.
		bundle = _bundle([("B-001", 10, 2.0), ("B-002", 5, 1.0)])
		item = _eight_metre_row(pieces=14, custom_reserved_batch_pool=None)
		with patch.object(dn.frappe, "get_all", return_value=["B-001", "B-002"]):
			self.assertTrue(_rebuild(item, bundle))
		self.assertFalse(getattr(bundle, "saved", 0))


class TestPreviewOnForm(FrappeTestCase):
	def test_preview_two_pieces(self):
		row = dict(name="row1", item_code="FG001109", against_sales_order="MU-SO26-00600", so_detail="soline1",
			warehouse="Finished Goods RD - MUPL", serial_and_batch_bundle="SABB-1", pieces=2, qty=3.0,
			conversion_factor=1, _changed="pieces",
			custom_reserved_batch_pool='[{"batch_no": "B-001"}, {"batch_no": "B-002"}]')
		with patch(f"{POOL_MOD}.get_reserved_pool", return_value=team_pool()[:2]), \
			patch(f"{MOD}._other_rows_batch_use", return_value={}):
			res = dn.preview_fifo_for_dn_row(row, items=[row])
		self.assertAlmostEqual(res["qty"], 0.4)
		self.assertEqual(res["batches"], [{"batch_no": "B-001", "pieces": 2, "qty": 0.4}])
		self.assertEqual(res["reserved_pieces"], 15)


# ------------------------------------------------------------------ cancel: no double undo
class TestCancelUndoesDeliveryOnce(FrappeTestCase):
	"""Two notes ship 2 t and 1 t of batch B-1 from one reservation. Cancelling
	the second gives back its 1 t from the submit log; the batch-based undo
	then took back another 1 t that belongs to the first note."""

	def _cancel(self, flags):
		doc = SimpleNamespace(is_return=0, flags=frappe._dict(flags), items=[], name="DN-B")
		with patch(f"{MOD}.release_stock_used_by_delivery_note"), \
			patch(f"{MOD}.restore_released_so_reservations"), \
			patch(f"{MOD}.restore_stock_reservations_after_cancel"), \
			patch(f"{MOD}.update_sales_order_quantities_on_cancel"), \
			patch(f"{MOD}.sync_batch_master_qty_from_delivery"), \
			patch(f"{MOD}.reverse_sre_delivery_for_dn") as reverse:
			dn.on_cancel(doc)
		return reverse

	def test_log_already_undid_it(self):
		self.assertFalse(self._cancel({"madhav_sre_undelivered_from_log": True}).called)

	def test_older_notes_without_a_log_still_undo_by_batch(self):
		self.assertTrue(self._cancel({}).called)

	def test_batch_undo_would_take_another_notes_delivery(self):
		# Why the guard matters: after the log put back DN-B's 1 t, the entry
		# still shows DN-A's 2 t delivered, and the batch-based undo for DN-B's
		# 1 t would take 1 t of it.
		entry = SimpleNamespace(batch_no="B-1", delivered_qty=2.0)
		sre = SimpleNamespace(reservation_based_on="Serial and Batch", delivered_qty=2.0,
			get=lambda k, d=None: [entry] if k == "sb_entries" else d)
		self.assertEqual(dn._reversible_delivered_qty([sre], {"B-1": 1.0}), 1.0)


class TestDeliverAsQtyInvoiceQtyFollows(FrappeTestCase):
	"""Edge case on the local copy (20 Nov): on a Deliver-as-Qty order, lowering
	Pieces changed Qty but left Invoice Qty at the old figure, so submit failed
	the 3% check. An Invoice Qty nobody typed follows Qty; a typed one stays."""

	def test_untyped_invoice_qty_follows(self):
		bundle = _bundle([("B-001", 10, 2.0), ("B-002", 5, 1.0)])
		item = _eight_metre_row(pieces=2, custom_deliver_as_qty=1, invoice_qty=3.0)
		_rebuild(item, bundle)
		self.assertAlmostEqual(item.invoice_qty, 0.4)

	def test_typed_invoice_qty_is_kept(self):
		bundle = _bundle([("B-001", 10, 2.0), ("B-002", 5, 1.0)])
		item = _eight_metre_row(pieces=2, custom_deliver_as_qty=1, invoice_qty=0.42)
		_rebuild(item, bundle)
		self.assertAlmostEqual(item.invoice_qty, 0.42)


class TestRowBranchFromSalesOrder(FrappeTestCase):
	"""Team, 21 Nov: Branch (a required accounting dimension) came in blank on
	fetched rows, so submit stopped until it was typed in."""

	def test_row_takes_the_sales_order_branch(self):
		so = SimpleNamespace(name="MU-SO26-00443", branch="Rolling Division", get=lambda k, d=None: {"branch": "Rolling Division"}.get(k, d))
		row = Row(branch=None)
		with patch.object(dn.frappe, "get_meta", return_value=_meta()):
			dn._fill_dn_item_branch(row, so, frappe._dict(branch=None))
		self.assertEqual(row.branch, "Rolling Division")

	def test_line_branch_wins_and_a_set_branch_is_kept(self):
		so = SimpleNamespace(name="SO", branch="Rolling Division", get=lambda k, d=None: None)
		row = Row(branch=None)
		with patch.object(dn.frappe, "get_meta", return_value=_meta()):
			dn._fill_dn_item_branch(row, so, frappe._dict(branch="Fabrication/Galvanization"))
		self.assertEqual(row.branch, "Fabrication/Galvanization")
		kept = Row(branch="Other")
		with patch.object(dn.frappe, "get_meta", return_value=_meta()):
			dn._fill_dn_item_branch(kept, so, frappe._dict(branch=None))
		self.assertEqual(kept.branch, "Other")

	def test_fetch_puts_branch_on_the_row(self):
		t = TestGetItemsOneRowPerLength()
		so_item = frappe._dict(name="soline1", item_code="FG001109", length_size=8, section_weight=25.0)
		with patch(f"{MOD}._fill_dn_item_branch", side_effect=lambda r, so, si: setattr(r, "branch", "Rolling Division")):
			doc, _ = t._fetch(t._sres(), team_pool())
		self.assertEqual({r.branch for r in doc.items}, {"Rolling Division"})
