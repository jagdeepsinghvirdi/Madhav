"""Round 4 bug fix: Stock Transfer counted a multi-batch movement in full
against every batch in it.

Production, 20 Nov 2026: batches delivered together on one Delivery Note row
(FG001679 MUBT-03524, FG003197 MUBT-02636, ...) showed -10.745 t, -11.215 t
in Stock Transfer while ERPNext's own batch qty is 0. Round 4 makes such
multi-batch rows common (one Delivery Note row per length).

The queries are checked against a small in-memory ledger that mirrors how the
SQL joins rows, so the old join (whole movement per batch) and the new one
(the batch's own share) give different answers.
"""
import unittest
from unittest.mock import patch

from madhav.madhav.doctype.stock_transfer import stock_transfer as st

# One delivery of two batches: SLE -3.0 t, bundle B-1 2.0 t + B-2 1.0 t.
SLES = [
	{"actual_qty": 5.0, "entries": [("B-1", 5.0)]},  # B-1 made, 5 t
	{"actual_qty": 1.0, "entries": [("B-2", 1.0)]},  # B-2 made, 1 t
	{"actual_qty": -3.0, "entries": [("B-1", 2.0), ("B-2", 1.0)]},  # one DN row, both
]
# Piece rows: one per batch, carrying batch_no (as the piece ledger writes them).
PSLES = [
	{"batch_no": "B-1", "actual_qty": 25},
	{"batch_no": "B-2", "actual_qty": 5},
	{"batch_no": "B-1", "actual_qty": -10, "bundle": "DN-BUNDLE"},
	{"batch_no": "B-2", "actual_qty": -5, "bundle": "DN-BUNDLE"},
]


def fake_sql(query, values=None, *a, **k):
	q = " ".join(query.split())
	if "tabStock Ledger Entry" in q:
		batch = values[2]
		if "SIGN(sle.actual_qty) * ABS(sbe.qty)" in q:
			total = sum(
				(1 if s["actual_qty"] > 0 else -1) * abs(qty)
				for s in SLES for b, qty in s["entries"] if b == batch
			)
		else:  # old: whole movement for each matching batch
			total = sum(s["actual_qty"] for s in SLES for b, _q in s["entries"] if b == batch)
		return [[total]]
	if "tabPiece Stock Ledger Entry" in q:
		if isinstance(values, dict):  # new: rows matched on their own batch
			rows = [p for p in PSLES if p["batch_no"] == values["batch_no"]]
		else:  # old: every row of a bundle joined to every batch in it
			batch = values[2]
			rows = []
			for p in PSLES:
				bundle_batches = (
					{"B-1", "B-2"} if p.get("bundle") == "DN-BUNDLE" else {p["batch_no"]}
				)
				if batch in bundle_batches:
					rows.append(p)
		return [[len(rows), sum(p["actual_qty"] for p in rows)]]
	raise AssertionError(q)


class TestLiveBatchCounts(unittest.TestCase):
	def test_qty_is_the_batch_own_share(self):
		with patch.object(st.frappe.db, "sql", side_effect=fake_sql):
			self.assertAlmostEqual(st.get_live_batch_qty("FG", "FG RD", "B-1"), 3.0)
			self.assertAlmostEqual(st.get_live_batch_qty("FG", "FG RD", "B-2"), 0.0)

	def test_pieces_are_the_batch_own_rows(self):
		with patch.object(st.frappe.db, "sql", side_effect=fake_sql):
			self.assertEqual(st.get_live_batch_pieces("FG", "FG RD", "B-1"), 15)
			self.assertEqual(st.get_live_batch_pieces("FG", "FG RD", "B-2"), 0)


if __name__ == "__main__":
	unittest.main()
