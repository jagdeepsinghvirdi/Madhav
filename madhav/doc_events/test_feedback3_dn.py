# Copyright (c) 2026, Finbyz pvt. ltd. and Contributors
# See license.txt

"""Team feedback 3 (21 Nov 2026): Delivery Note."""

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from madhav.doc_events import delivery_note as dn

MOD = "madhav.doc_events.delivery_note"


def _batches(**sw):
	return [frappe._dict(name=b, section_weight=v) for b, v in sw.items()]


class TestSectionWeightFromBatch(FrappeTestCase):
	"""A3 / C1: "The section weight should remain as mentioned in the batch."
	After save it moved with the qty (34.200 -> 34.615)."""

	def test_one_batch_gives_its_section_weight(self):
		with patch.object(dn.frappe, "get_all", return_value=_batches(B1=34.2)):
			self.assertEqual(dn.batch_section_weight([("B1", 1, 7.8)]), 34.2)

	def test_several_batches_average_by_metres(self):
		with patch.object(dn.frappe, "get_all", return_value=_batches(B1=34.2, B2=34.6)):
			sw = dn.batch_section_weight([("B1", 20, 8.0), ("B2", 2, 8.0)])
		self.assertAlmostEqual(sw, (34.2 * 160 + 34.6 * 16) / 176, places=3)

	def test_no_batch_section_weight_keeps_the_row(self):
		row = frappe._dict(idx=1, serial_and_batch_bundle=None, batch_no="B1", pieces=1, length_size=7.8,
			section_weight=34.615)
		doc = frappe._dict(items=[row])
		with patch.object(dn.frappe, "get_all", return_value=[]), patch(f"{MOD}.set_dn_sw_qty"):
			dn.stamp_batch_section_weights(doc)
		self.assertEqual(row.section_weight, 34.615)

	def test_save_puts_back_the_batch_section_weight(self):
		row = frappe._dict(idx=1, serial_and_batch_bundle="SABB-1", batch_no=None, pieces=1, length_size=7.8,
			section_weight=34.615)  # worked out from 0.270 t / 7.8 m
		doc = frappe._dict(items=[row])

		def get_all(doctype, **kw):
			if doctype == "Serial and Batch Entry":
				return [frappe._dict(batch_no="B1", pieces=-1, length=7.8)]
			return _batches(B1=34.2)

		with patch.object(dn.frappe, "get_all", side_effect=get_all), patch(f"{MOD}.set_dn_sw_qty") as sw_qty:
			dn.stamp_batch_section_weights(doc)
		self.assertEqual(row.section_weight, 34.2)
		sw_qty.assert_called_once_with(doc)

	def test_section_weight_is_read_only(self):
		import json
		import os

		path = os.path.join(os.path.dirname(dn.__file__), "..", "fixtures", "custom_field.json")
		field = [f for f in json.load(open(path)) if f["dt"] == "Delivery Note Item" and f["fieldname"] == "section_weight"]
		self.assertEqual(field[0]["read_only"], 1)


class _Note(frappe._dict):
	def get_doc_before_save(self):
		return self._before

	def is_new(self):
		return False


class TestRemovedLineLeavesNoDraftBundle(FrappeTestCase):
	"""C2: a line removed after Save left its draft Serial and Batch Bundle."""

	def _run(self, docstatus=0, used_elsewhere=False):
		before = frappe._dict(items=[frappe._dict(serial_and_batch_bundle="SABB-1"), frappe._dict(serial_and_batch_bundle="SABB-2")])
		doc = _Note(name="DCA-1", items=[frappe._dict(serial_and_batch_bundle="SABB-1")], _before=before)
		with patch.object(dn.frappe, "get_all", return_value=[]), \
			patch.object(dn.frappe.db, "get_value", return_value=docstatus), \
			patch.object(dn.frappe.db, "exists", return_value=True), \
			patch.object(dn.frappe.db, "sql", return_value=[(1,)] if used_elsewhere else []), \
			patch.object(dn.frappe, "delete_doc") as delete:
			dn.delete_orphan_draft_bundles(doc)
		return delete

	def test_removed_line_bundle_is_deleted(self):
		delete = self._run()
		delete.assert_called_once_with("Serial and Batch Bundle", "SABB-2", force=True, ignore_permissions=True)

	def test_submitted_bundle_is_kept(self):
		self._run(docstatus=1).assert_not_called()

	def test_bundle_used_by_another_note_is_kept(self):
		self._run(used_elsewhere=True).assert_not_called()

	def test_bundle_booked_to_the_note_but_on_no_row_is_deleted(self):
		doc = _Note(name="DCA-1", items=[frappe._dict(serial_and_batch_bundle="SABB-1")], _before=None)
		with patch.object(dn.frappe, "get_all", return_value=["SABB-1", "SABB-9"]), \
			patch.object(dn.frappe.db, "get_value", return_value=0), \
			patch.object(dn.frappe.db, "exists", return_value=True), \
			patch.object(dn.frappe.db, "sql", return_value=[]), \
			patch.object(dn.frappe, "delete_doc") as delete:
			dn.delete_orphan_draft_bundles(doc)
		delete.assert_called_once_with("Serial and Batch Bundle", "SABB-9", force=True, ignore_permissions=True)
