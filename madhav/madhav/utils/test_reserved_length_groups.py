"""Round 4 (team batch sheet, Nov 2026): merge reserved batches by length and
use the oldest batch first. Examples are the team's own (Batch Problem
Statement, Part A)."""
import json
import unittest

from madhav.madhav.utils.reserved_length_groups import (
	allocate_pieces_fifo,
	allocate_qty_fifo,
	group_pool_by_length,
	length_key,
	merge_entries_by_batch,
	pool_batches_from_json,
	pool_to_json,
)


def entry(batch, length, pieces, qty, date, sre="SRE-1", warehouse="FG", assorted=None):
	return {
		"sre": sre,
		"batch_no": batch,
		"warehouse": warehouse,
		"qty": qty,
		"pieces": pieces,
		"length": length,
		"section_weight": 25.0,
		"assorted_length": assorted,
		"batch_date": date,
		"creation": date + " 10:00:00",
	}


# The team's example: two 8 m batches and one 7.5 m batch for one SO line.
TEAM_POOL = [
	entry("B-002", 8, 5, 1.0, "2026-11-05", sre="SRE-2"),
	entry("B-001", 8, 10, 2.0, "2026-11-01", sre="SRE-1"),
	entry("B-003", 7.5, 2, 0.375, "2026-11-03", sre="SRE-3"),
]


class TestGroupByLength(unittest.TestCase):
	def test_team_example_gives_one_row_per_length(self):
		groups = group_pool_by_length(TEAM_POOL)
		self.assertEqual([(g["length"], g["pieces"]) for g in groups], [(8.0, 15), (7.5, 2)])
		self.assertEqual([e["batch_no"] for e in groups[0]["entries"]], ["B-001", "B-002"])
		self.assertEqual(groups[0]["qty"], 3.0)
		self.assertEqual(groups[0]["sres"], ["SRE-1", "SRE-2"])
		self.assertEqual(groups[1]["sres"], ["SRE-3"])

	def test_warehouses_stay_apart(self):
		pool = [
			entry("B-001", 8, 10, 2.0, "2026-11-01", warehouse="FG"),
			entry("B-009", 8, 4, 0.8, "2026-11-02", warehouse="For Mill (EXTRA)"),
		]
		groups = group_pool_by_length(pool)
		self.assertEqual(len(groups), 2)
		self.assertEqual({g["warehouse"] for g in groups}, {"FG", "For Mill (EXTRA)"})

	def test_assorted_length_is_its_own_row(self):
		pool = [
			entry("B-001", 8, 10, 2.0, "2026-11-01"),
			entry("B-004", 8, 6, 1.2, "2026-11-02", assorted="R/L"),
		]
		groups = group_pool_by_length(pool)
		self.assertEqual(len(groups), 2)
		self.assertEqual(sorted(str(g["assorted_length"]) for g in groups), ["None", "R/L"])

	def test_lengths_match_to_the_centimetre(self):
		self.assertEqual(length_key(8.001), length_key(8.0))
		self.assertNotEqual(length_key(6.4), length_key(6.45))

	def test_same_batch_on_two_reservations_is_one_entry(self):
		pool = [
			entry("B-001", 8, 6, 1.2, "2026-11-01", sre="SRE-1"),
			entry("B-001", 8, 4, 0.8, "2026-11-01", sre="SRE-7"),
		]
		merged = merge_entries_by_batch(pool)
		self.assertEqual(len(merged), 1)
		self.assertEqual(merged[0]["pieces"], 10)
		self.assertAlmostEqual(merged[0]["qty"], 2.0)
		self.assertEqual(merged[0]["sres"], ["SRE-1", "SRE-7"])

	def test_fully_delivered_entries_are_skipped(self):
		pool = [entry("B-001", 8, 0, 0.0, "2026-11-01"), entry("B-002", 8, 5, 1.0, "2026-11-05")]
		groups = group_pool_by_length(pool)
		self.assertEqual([e["batch_no"] for e in groups[0]["entries"]], ["B-002"])


class TestOldestFirst(unittest.TestCase):
	def eight_metre(self):
		return group_pool_by_length(TEAM_POOL)[0]["entries"]

	def test_case_2_two_pieces_come_from_the_older_batch(self):
		allocations, short = allocate_pieces_fifo(self.eight_metre(), 2)
		self.assertEqual(short, 0)
		self.assertEqual([(a["batch_no"], a["take_pieces"]) for a in allocations], [("B-001", 2)])
		self.assertAlmostEqual(allocations[0]["take_qty"], 0.4)

	def test_case_1_full_delivery_takes_every_batch_whole(self):
		allocations, short = allocate_pieces_fifo(self.eight_metre(), 15)
		self.assertEqual(short, 0)
		self.assertEqual(
			[(a["batch_no"], a["take_pieces"], a["take_qty"]) for a in allocations],
			[("B-001", 10, 2.0), ("B-002", 5, 1.0)],
		)

	def test_pieces_spill_into_the_next_batch(self):
		allocations, _ = allocate_pieces_fifo(self.eight_metre(), 12)
		self.assertEqual([(a["batch_no"], a["take_pieces"]) for a in allocations], [("B-001", 10), ("B-002", 2)])
		self.assertAlmostEqual(sum(a["take_qty"] for a in allocations), 2.4)

	def test_more_pieces_than_reserved_reports_the_shortfall(self):
		allocations, short = allocate_pieces_fifo(self.eight_metre(), 18)
		self.assertEqual(short, 3)
		self.assertEqual(sum(a["take_pieces"] for a in allocations), 15)

	def test_qty_is_taken_oldest_first_and_a_started_bar_counts(self):
		allocations, short = allocate_qty_fifo(self.eight_metre(), 2.5)
		self.assertEqual(short, 0)
		self.assertEqual([a["batch_no"] for a in allocations], ["B-001", "B-002"])
		self.assertEqual([a["take_pieces"] for a in allocations], [10, 3])
		self.assertAlmostEqual(sum(a["take_qty"] for a in allocations), 2.5)

	def test_whole_batch_leaves_no_weight_dust(self):
		pool = [entry("B-001", 6.4, 61, 17.395, "2026-11-01")]
		allocations, _ = allocate_pieces_fifo(pool, 61)
		self.assertEqual(allocations[0]["take_qty"], 17.395)

	def test_pool_record_round_trip_keeps_oldest_first(self):
		value = pool_to_json(TEAM_POOL)
		self.assertEqual(pool_batches_from_json(value), ["B-001", "B-003", "B-002"])
		self.assertEqual(json.loads(value)[0]["sres"], ["SRE-1"])
		self.assertEqual(pool_batches_from_json("not json"), [])
		self.assertEqual(pool_batches_from_json(None), [])


if __name__ == "__main__":
	unittest.main()
