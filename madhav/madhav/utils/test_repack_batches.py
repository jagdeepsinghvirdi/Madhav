"""Round 4, team batch sheet Part B section 2 (Nov 2026): Repack.

"The user selects a batch manually only in Stock Entry for REPACK. If the
stock is reserved against another order, the user must first unreserve it
before using it for REPACK. Only difficulty faced: identifying the correct
batch."
"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from madhav.madhav.utils import repack_batches as rb

MOD = "madhav.madhav.utils.repack_batches"
BWRT = "madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool"


def option(batch, length, free_qty, free_pieces, date, qty=None):
	return frappe._dict(
		batch_no=batch, length=length, section_weight=25.0, qty=qty or free_qty, pieces=free_pieces,
		free_qty=free_qty, free_pieces=free_pieces, reservations=[], batch_date=date, creation=date,
	)


class TestSuggestBatches(unittest.TestCase):
	def _suggest(self, options, **kw):
		with patch(f"{MOD}.get_repack_batch_options", return_value=options):
			return rb.suggest_repack_batches("FG001109", "Finished Goods RD - MUPL", **kw)

	def test_free_stock_oldest_first_by_pieces(self):
		res = self._suggest(
			[option("B-2", 8, 1.0, 5, "2026-11-05"), option("B-1", 8, 2.0, 10, "2026-11-01")], pieces=12
		)
		self.assertEqual([(r["batch_no"], r["pieces"]) for r in res["rows"]], [("B-1", 10), ("B-2", 2)])
		self.assertEqual(res["short_pieces"], 0)

	def test_reserved_only_batches_are_not_suggested(self):
		res = self._suggest([option("B-1", 8, 0.0, 0, "2026-11-01"), option("B-2", 8, 1.0, 5, "2026-11-05")], qty=0.6)
		self.assertEqual([r["batch_no"] for r in res["rows"]], ["B-2"])
		self.assertAlmostEqual(res["rows"][0]["qty"], 0.6)

	def test_length_filter_and_shortfall(self):
		res = self._suggest(
			[option("B-1", 8, 2.0, 10, "2026-11-01"), option("B-3", 7.5, 1.0, 5, "2026-11-02")],
			pieces=7, length=7.5,
		)
		self.assertEqual([r["batch_no"] for r in res["rows"]], ["B-3"])
		self.assertEqual(res["short_pieces"], 2)


class FakeSRE(SimpleNamespace):
	def cancel(self):
		self.cancelled = True


def _entry(name, batch, qty, delivered=0, pieces=10):
	e = MagicMock()
	e.name, e.batch_no, e.qty, e.delivered_qty, e.pieces = name, batch, qty, delivered, pieces
	e.get = lambda k, d=None: getattr(e, k, d)
	return e


def _repack(rows, **kw):
	d = frappe._dict(name="MUST26-REPACK-1", purpose="Repack", stock_entry_type="Repack", items=rows)
	d.update(kw)
	d.set = lambda k, v: d.__setitem__(k, v)
	return d


def _row(**kw):
	r = frappe._dict(name="r1", idx=1, item_code="FG001109", s_warehouse="FG", batch_no="B-1",
		transfer_qty=3.0, qty=3.0, pieces=15, conversion_factor=1, custom_unreserve_from="SRE-9")
	r.update(kw)
	return r


class TestUnreserveAndUse(unittest.TestCase):
	def _release(self, doc, sre, free=1.0, allowed=True):
		with patch.object(rb.frappe, "has_permission", return_value=allowed), \
			patch(f"{BWRT}.get_batch_available_qty", return_value=free), \
			patch.object(rb.frappe, "get_doc", side_effect=lambda *a, **k: sre if a and a[0] == "Stock Reservation Entry" else MagicMock()), \
			patch(f"{MOD}._snapshot", return_value={"name": sre.name, "reserved_qty": sre.reserved_qty}), \
			patch(f"{MOD}._refresh") as refresh:
			rb.release_reservations_for_repack(doc)
		return refresh

	def test_takes_only_what_the_row_needs_beyond_free_stock(self):
		e = _entry("e1", "B-1", 5.0, pieces=25)
		sre = FakeSRE(name="SRE-9", docstatus=1, item_code="FG001109", warehouse="FG", sb_entries=[e],
			reserved_qty=5.0, delivered_qty=0, db_set=MagicMock())
		doc = _repack([_row()])
		refresh = self._release(doc, sre)
		e.db_set.assert_any_call("qty", 3.0, update_modified=False)  # 5 - (3 needed - 1 free)
		e.db_set.assert_any_call("pieces", 15, update_modified=False)  # 25 - 10 pcs of 2 t
		sre.db_set.assert_called_with("reserved_qty", 3.0, update_modified=False)
		refresh.assert_called_once_with("SRE-9")
		log = json.loads(doc[rb.RELEASE_LOG_FIELD])
		self.assertEqual(log[0]["entries"][0]["qty"], 2.0)
		self.assertFalse(log[0].get("cancelled"))

	def test_whole_undelivered_reservation_is_cancelled_and_kept_for_restore(self):
		e = _entry("e1", "B-1", 2.0)
		sre = FakeSRE(name="SRE-9", docstatus=1, item_code="FG001109", warehouse="FG", sb_entries=[e],
			reserved_qty=2.0, delivered_qty=0, db_set=MagicMock(), flags=frappe._dict())
		doc = _repack([_row()])
		self._release(doc, sre)
		self.assertTrue(getattr(sre, "cancelled", False))
		log = json.loads(doc[rb.RELEASE_LOG_FIELD])
		self.assertEqual(log[0]["cancelled"], 1)
		self.assertEqual(log[0]["snapshot"]["name"], "SRE-9")

	def test_free_stock_enough_touches_no_reservation(self):
		sre = FakeSRE(name="SRE-9", docstatus=1, item_code="FG001109", warehouse="FG", sb_entries=[], reserved_qty=2.0,
			delivered_qty=0, db_set=MagicMock())
		doc = _repack([_row(transfer_qty=0.8, qty=0.8)])
		self._release(doc, sre, free=1.0)
		self.assertNotIn(rb.RELEASE_LOG_FIELD, doc)
		sre.db_set.assert_not_called()

	def test_reservation_too_small_is_refused(self):
		e = _entry("e1", "B-1", 1.0)
		sre = FakeSRE(name="SRE-9", docstatus=1, item_code="FG001109", warehouse="FG", sb_entries=[e],
			reserved_qty=1.0, delivered_qty=0, db_set=MagicMock())
		with self.assertRaises(frappe.ValidationError):
			self._release(_repack([_row()]), sre, free=0.5)  # needs 2.5 off a 1.0 reservation

	def test_without_permission_is_refused(self):
		sre = FakeSRE(name="SRE-9", reserved_qty=2.0)
		with self.assertRaises(frappe.PermissionError):
			self._release(_repack([_row()]), sre, allowed=False)

	def test_other_stock_entries_are_left_alone(self):
		doc = _repack([_row()], purpose="Material Transfer", stock_entry_type="Material Transfer")
		with patch.object(rb.frappe, "has_permission") as perm:
			rb.release_reservations_for_repack(doc)
		perm.assert_not_called()


class TestRestoreOnCancel(unittest.TestCase):
	def test_trimmed_reservation_gets_its_qty_and_pieces_back(self):
		e = _entry("e1", "B-1", 3.0, pieces=15)
		sre = FakeSRE(name="SRE-9", sb_entries=[e], reserved_qty=3.0, db_set=MagicMock())
		log = [{"sre": "SRE-9", "batch_no": "B-1", "entries": [{"entry": "e1", "qty": 2.0, "pieces": 10}]}]
		doc = _repack([], **{rb.RELEASE_LOG_FIELD: json.dumps(log)})
		with patch.object(rb.frappe.db, "get_value", return_value=1), \
			patch.object(rb.frappe, "get_doc", return_value=sre), \
			patch(f"{MOD}._refresh") as refresh:
			rb.restore_reservations_after_repack_cancel(doc)
		e.db_set.assert_any_call("qty", 5.0, update_modified=False)
		e.db_set.assert_any_call("pieces", 25, update_modified=False)
		sre.db_set.assert_called_with("reserved_qty", 5.0, update_modified=False)
		refresh.assert_called_once_with("SRE-9")

	def test_cancelled_reservation_is_recreated(self):
		log = [{"sre": "SRE-9", "batch_no": "B-1", "cancelled": 1, "snapshot": {"name": "SRE-9"}, "entries": []}]
		doc = _repack([], **{rb.RELEASE_LOG_FIELD: json.dumps(log)})
		with patch(f"{MOD}._recreate_from_snapshot") as recreate:
			rb.restore_reservations_after_repack_cancel(doc)
		recreate.assert_called_once()
		self.assertEqual(recreate.call_args[0][0]["name"], "SRE-9")


if __name__ == "__main__":
	unittest.main()



class TestRepackTakesOnlyFreeStock(unittest.TestCase):
	"""Team feedback 3 (21 Nov 2026): skipping the unreserve pop-up let a Repack
	use reserved stock; ERPNext's own check passed it."""

	def _check(self, rows, free, holders=None, entry_type="Repack"):
		doc = _repack(rows, stock_entry_type=entry_type)
		with patch(f"{BWRT}.get_batch_available_qty", side_effect=lambda *a: free.get(a[2], 0)), \
			patch(f"{MOD}.batch_reservations", return_value=holders or [frappe._dict(sales_order="SO-1", qty=2.0)]):
			rb.validate_repack_reserved_stock(doc)

	def test_taking_reserved_stock_is_refused(self):
		with self.assertRaises(frappe.ValidationError) as cm:
			self._check([_row(custom_unreserve_from=None, transfer_qty=3.0)], {"B-1": 1.0})
		self.assertIn("SO-1", str(cm.exception))

	def test_free_stock_is_fine(self):
		self._check([_row(custom_unreserve_from=None, transfer_qty=0.8)], {"B-1": 1.0})

	def test_two_rows_on_one_batch_add_up(self):
		rows = [_row(name="r1", transfer_qty=0.6, custom_unreserve_from=None),
			_row(name="r2", idx=2, transfer_qty=0.6, custom_unreserve_from=None)]
		with self.assertRaises(frappe.ValidationError):
			self._check(rows, {"B-1": 1.0})

	def test_target_rows_and_production_stages_are_not_checked(self):
		self._check([_row(s_warehouse=None, t_warehouse="FG", transfer_qty=9)], {})
		self._check([_row(custom_unreserve_from=None, transfer_qty=9)], {"B-1": 0}, entry_type="Production Entry")


class TestRecreateDoesNotRePickBatches(unittest.TestCase):
	"""Demo, 21 Nov 2026: cancelling Repack MUST26-09778 failed because the
	recreated reservation ran Madhav's auto batch pick, which crashes on Sales
	Order lines that have a length."""

	def test_snapshot_batches_are_kept(self):
		class FakeNewSRE(SimpleNamespace):
			def append(self, table, row):
				self.sb_entries.append(row)

			def set(self, k, v):
				setattr(self, k, v)

			def auto_reserve_serial_and_batch(self, *a, **k):
				raise AttributeError("'NoneType' object has no attribute 'get'")

			def insert(self, **k):
				self.name = "SRE-NEW"

			def submit(self):
				self.auto_reserve_serial_and_batch()  # what before_submit does

		sre = FakeNewSRE(sb_entries=[], flags=frappe._dict())
		snap = {"name": "SRE-9", "item_code": "FG000522", "warehouse": "FG", "voucher_no": "SO-1",
			"reservation_based_on": "Serial and Batch", "reserved_qty": 2.427,
			"sb_entries": [{"batch_no": "B-1", "qty": 2.427, "pieces": 15}]}
		with patch.object(rb.frappe, "new_doc", return_value=sre), patch.object(rb.frappe, "msgprint"):
			self.assertEqual(rb._recreate_from_snapshot(snap, frappe._dict(name="MUST26-1")), "SRE-NEW")
		self.assertEqual([e["batch_no"] for e in sre.sb_entries], ["B-1"])


class TestBatchOptions(unittest.TestCase):
	"""Found on the local copy of production (20 Nov): reserved batches were
	missing from the list, and a fully reserved batch showed 21 free pieces."""

	def _options(self, limits, stock):
		batch = frappe._dict(name="MUBT-13666", average_length=8.43, section_weight=19.72, pieces=21,
			manufacturing_date="2026-10-15", creation="2026-10-15")
		with patch("erpnext.stock.doctype.batch.batch.get_batch_qty", return_value=stock) as gbq, \
			patch.object(rb.frappe, "get_all", return_value=[batch]), \
			patch(f"{BWRT}.get_batch_reservation_limits", return_value=limits), \
			patch(f"{MOD}.batch_reservations", return_value=[frappe._dict(sre="SRE-1", qty=3.435)]):
			out = rb.get_repack_batch_options("FG000873", "FG RD")
		return out, gbq

	def test_reserved_batch_is_listed_with_no_free_pieces(self):
		out, gbq = self._options({"qty": 0, "pieces": 21}, [{"batch_no": "MUBT-13666", "qty": 3.435}])
		self.assertTrue(gbq.call_args.kwargs.get("ignore_reserved_stock"))
		self.assertEqual(len(out), 1)
		self.assertEqual((out[0].free_qty, out[0].free_pieces), (0, 0))
		self.assertEqual(out[0].reservations[0].sre, "SRE-1")


class TestTwoRowsOneBatch(unittest.TestCase):
	"""Edge case on the local copy of production (20 Nov): two Repack rows of
	1 t each from one fully reserved batch released only 1 t."""

	def test_each_row_takes_its_own_share_off_the_reservation(self):
		e = _entry("e1", "B-1", 3.435, pieces=21)
		state = {"open": 3.435}

		def set_qty(field, value, **kw):
			if field == "qty":
				state["open"] = value
				e.qty = value

		e.db_set.side_effect = set_qty
		sre = FakeSRE(name="SRE-9", docstatus=1, item_code="FG001109", warehouse="FG", sb_entries=[e],
			reserved_qty=3.435, delivered_qty=0, db_set=MagicMock(side_effect=lambda f, v, **k: setattr(sre, f, v)))
		doc = _repack([_row(name="r1", transfer_qty=1.0, qty=1.0, pieces=6), _row(name="r2", idx=2, transfer_qty=1.0, qty=1.0, pieces=6)])
		# Free stock grows as the first row unreserves: 3.435 physical - open reserved.
		with patch.object(rb.frappe, "has_permission", return_value=True), \
			patch(f"{BWRT}.get_batch_available_qty", side_effect=lambda *a: round(3.435 - state["open"], 3)), \
			patch.object(rb.frappe, "get_doc", side_effect=lambda *a, **k: sre if a and a[0] == "Stock Reservation Entry" else MagicMock()), \
			patch(f"{MOD}._snapshot", return_value={}), patch(f"{MOD}._refresh"):
			rb.release_reservations_for_repack(doc)
		self.assertAlmostEqual(sre.reserved_qty, 1.435)
