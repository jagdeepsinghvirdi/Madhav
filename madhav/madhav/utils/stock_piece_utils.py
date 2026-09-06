"""Helpers for integer PC pieces and reservation length/weight on bundle rows."""
from __future__ import annotations

import math

import frappe
from frappe.utils import flt


def int_pieces_from_qty(qty, length, section_weight):
	"""Whole PC count from weight — ceil of theoretical pieces (SO behaviour).

	Example: 30.458 → 31.

	Also guards float noise so values like 31.0000000002 do not become 32.
	"""
	length = flt(length)
	section_weight = flt(section_weight)
	qty = flt(qty)
	if not length or not section_weight or qty <= 0:
		return 0

	raw = (qty * 1000) / (length * section_weight)
	# Near-exact integers: trust round (float dust), do not ceil up by 1
	near = round(raw)
	if abs(raw - near) < 1e-6:
		return max(0, int(near))
	# Tiny epsilon so ceil(N) is not ceil(N + 1e-15)
	return max(0, int(math.ceil(raw - 1e-9)))


def resolve_entry_length(entry, batch_no=None, so_detail=None):
	"""Prefer actual batch length, then reservation row, then SO ordered length."""
	if isinstance(entry, dict):
		stored = flt(entry.get("length"))
		batch_no = batch_no or entry.get("batch_no")
	else:
		stored = flt(getattr(entry, "length", 0))
		batch_no = batch_no or getattr(entry, "batch_no", None)

	# Physical batch length (e.g. 6.50) must win over SO ordered length (e.g. 6).
	if batch_no:
		batch_length = flt(
			frappe.db.get_value("Batch", batch_no, "average_length") or 0
		)
		if batch_length:
			return batch_length

	if stored:
		return stored

	if so_detail:
		length = flt(frappe.db.get_value("Sales Order Item", so_detail, "length_size"))
		if length:
			return length

	return 0


def _entry_avail_qty(entry):
	"""Positive qty for aggregation; excludes delivered portion on SRE rows."""
	if isinstance(entry, dict):
		total = flt(entry.get("qty"))
		delivered = flt(entry.get("delivered_qty", 0))
	else:
		total = flt(getattr(entry, "qty", 0))
		delivered = flt(getattr(entry, "delivered_qty", 0))

	# Outward DN / bundle rows store negative qty; delivered_qty is on SRE only.
	if total < 0:
		return abs(total)

	return max(0.0, total - delivered)


def resolve_weighted_length_from_entries(entries, so_detail=None):
	"""Batch-first length for one or more SRE / bundle rows.

	Always resolves each row via ``resolve_entry_length`` (batch
	``average_length`` → reservation row → SO line).

	- Single batch, or multiple batches with the same resolved length:
	  returns that length (e.g. 6.50).
	- Multiple batches with different lengths: returns the qty-weighted
	  average. That value is display-only on the DN item row; it may not
	  match any single batch — see Serial/Batch Bundle rows for actual
	  per-batch lengths.
	"""
	total_qty = 0.0
	weighted = 0.0
	resolved_lengths = []

	for entry in entries or []:
		qty = _entry_avail_qty(entry)
		if qty <= 0:
			continue

		batch_no = (
			entry.get("batch_no")
			if isinstance(entry, dict)
			else getattr(entry, "batch_no", None)
		)
		length = resolve_entry_length(entry, batch_no, so_detail)
		if not length:
			continue

		resolved_lengths.append(length)
		total_qty += qty
		weighted += qty * length

	if not total_qty:
		return 0

	unique = {round(flt(length), 6) for length in resolved_lengths}
	if len(unique) == 1:
		return flt(resolved_lengths[0])

	return flt(weighted / total_qty)


def resolve_sre_dn_length(sre_name, so_detail=None):
	"""Batch-first length for a DN item from SRE bundle rows."""
	if not sre_name:
		return 0

	rows = frappe.db.sql(
		"""
		select batch_no, qty, delivered_qty, length, pieces, section_weight
		from `tabSerial and Batch Entry`
		where parent = %s and parenttype = 'Stock Reservation Entry'
		  and qty > ifnull(delivered_qty, 0)
		""",
		sre_name,
		as_dict=True,
	)
	return resolve_weighted_length_from_entries(rows, so_detail)


def sum_undelivered_pieces_from_sre_rows(rows, so_detail=None, item_code=None):
	"""Integer pieces remaining on SRE batch rows (excludes fully delivered batches)."""
	total = 0
	for row in rows or []:
		avail_qty = _entry_avail_qty(row)
		if avail_qty <= 0:
			continue

		batch_no = row.get("batch_no") if isinstance(row, dict) else getattr(row, "batch_no", None)
		length = resolve_entry_length(row, batch_no, so_detail)
		section_weight = resolve_entry_section_weight(row, item_code, length, batch_no)
		total += resolve_entry_pieces(row, avail_qty, length, section_weight)

	return int(total)


def sync_dn_item_length_from_entries(item, entries, so_detail=None):
	"""Stamp DN item length_size using batch-first length resolution."""
	result = resolve_weighted_length_from_entries(entries, so_detail)
	if result and hasattr(item, "length_size"):
		item.length_size = result
	return result


def resolve_entry_section_weight(entry, item_code, length, batch_no=None):
	if isinstance(entry, dict):
		section_weight = flt(entry.get("section_weight"))
		batch_no = batch_no or entry.get("batch_no")
		qty = flt(entry.get("qty")) - flt(entry.get("delivered_qty"))
		pieces = flt(entry.get("pieces"))
	else:
		section_weight = flt(getattr(entry, "section_weight", 0))
		batch_no = batch_no or getattr(entry, "batch_no", None)
		qty = flt(getattr(entry, "qty", 0)) - flt(getattr(entry, "delivered_qty", 0))
		pieces = flt(getattr(entry, "pieces", 0))

	# Outward DN / bundle rows store negative qty — use absolute weight basis.
	qty = abs(qty) if qty else 0.0

	if section_weight:
		return section_weight

	if batch_no:
		section_weight = flt(frappe.db.get_value("Batch", batch_no, "section_weight") or 0)
		if section_weight:
			return section_weight

	# Derive from actual row weight/pieces/length before Item master default.
	# Item weight_per_meter can inflate PC when it differs from reserved batch weight.
	if pieces and length and qty:
		return (qty * 1000) / (pieces * flt(length))

	if item_code:
		section_weight = flt(frappe.db.get_value("Item", item_code, "weight_per_meter") or 0)
		if section_weight:
			return section_weight

	return 0


def resolve_entry_pieces(entry, avail_qty, length, section_weight):
	"""Integer pieces for undelivered qty on a reservation/bundle row.

	Prefer stored integer pieces scaled to undelivered qty; derive from weight
	only when pieces are missing on the row.
	"""
	avail_qty = flt(avail_qty)
	length = flt(length)
	section_weight = flt(section_weight)

	if isinstance(entry, dict):
		stored = flt(entry.get("pieces"))
		total_qty = flt(entry.get("qty"))
		delivered = flt(entry.get("delivered_qty"))
	else:
		stored = flt(getattr(entry, "pieces", 0))
		total_qty = flt(getattr(entry, "qty", 0))
		delivered = flt(getattr(entry, "delivered_qty", 0))

	if stored > 0 and total_qty > 0 and avail_qty > 0:
		orig_avail = max(0.0, total_qty - delivered)
		if orig_avail > 0:
			# Proportional share of already-integer reservation pieces
			return max(0, int(round(stored * (avail_qty / orig_avail))))

	# No stored pieces — derive once from undelivered weight
	if avail_qty > 0 and length and section_weight:
		return int_pieces_from_qty(avail_qty, length, section_weight)

	return max(0, int(round(stored))) if stored > 0 else 0


def qty_from_pieces(pieces, length, section_weight):
	pieces = flt(pieces)
	length = flt(length)
	section_weight = flt(section_weight)
	if not pieces or not length or not section_weight:
		return 0
	return (pieces * length * section_weight) / 1000


def distribute_integer_pieces(total_pieces, weights):
	"""Split integer pieces across rows; sum of result always equals total_pieces.

	Uses largest-remainder (Hamilton) on non-negative weights so multi-batch
	bundles never exceed the DN row PC total or drift due to per-row rounding.
	"""
	total_pieces = max(0, int(total_pieces))
	n = len(weights or [])
	if not n:
		return []
	if total_pieces == 0:
		return [0] * n

	weight_sum = sum(max(0.0, flt(w)) for w in weights)
	if weight_sum <= 0:
		return [0] * n

	raw = [total_pieces * max(0.0, flt(w)) / weight_sum for w in weights]
	floors = [int(math.floor(r)) for r in raw]
	allocated = sum(floors)
	remainder = total_pieces - allocated

	if remainder > 0:
		# Give +1 to rows with the largest fractional parts first.
		order = sorted(
			range(n),
			key=lambda i: (raw[i] - floors[i], weights[i]),
			reverse=True,
		)
		for i in order[:remainder]:
			floors[i] += 1

	return floors


def stored_entry_pieces(entry):
	"""Integer pieces already stamped on a bundle/reservation row."""
	from frappe.utils import cint

	if isinstance(entry, dict):
		return max(0, cint(flt(entry.get("pieces"))))
	return max(0, cint(flt(getattr(entry, "pieces", 0))))


def preserve_entry_pieces(entry, item_pieces=0, qty_ratio=1.0):
	"""Keep physical PC when only billed weight (invoice qty) changes.

	Prefer stored row pieces; otherwise allocate a share of the DN item total.
	For multi-batch rows use ``distribute_integer_pieces`` at the caller.
	"""
	from frappe.utils import cint

	existing = stored_entry_pieces(entry)
	if existing > 0:
		return existing

	item_pieces = cint(flt(item_pieces))
	if item_pieces > 0 and qty_ratio > 0:
		return max(0, int(round(item_pieces * qty_ratio)))

	return 0
