"""Helpers for integer PC pieces and reservation length/weight on bundle rows."""
from __future__ import annotations

import math

import frappe
from frappe.utils import cint, flt


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

	SRE ``pieces`` stays at the original reserved count when ``delivered_qty``
	moves — only qty is reduced. Scale against full reserved qty so a later
	DN gets remaining pieces (e.g. 74 reserved, 49 delivered → 25 left), not
	the full 74 again. Derive from weight only when pieces are missing.
	"""
	avail_qty = abs(flt(avail_qty))
	length = flt(length)
	section_weight = flt(section_weight)

	if isinstance(entry, dict):
		stored = flt(entry.get("pieces"))
		total_qty = abs(flt(entry.get("qty")))
		delivered = flt(entry.get("delivered_qty"))
	else:
		stored = flt(getattr(entry, "pieces", 0))
		total_qty = abs(flt(getattr(entry, "qty", 0)))
		delivered = flt(getattr(entry, "delivered_qty", 0))

	if stored > 0 and total_qty > 0 and avail_qty > 0:
		remaining_qty = max(0.0, total_qty - delivered)
		if remaining_qty > 0:
			# Pieces that still belong to the undelivered reserved qty.
			remaining_pieces = max(
				0, int(round(stored * (remaining_qty / total_qty)))
			)
			# Tiny weight tweak on what is left must not drop a bar.
			if should_preserve_pieces_for_qty_change(
				remaining_qty, avail_qty, length, section_weight
			):
				return remaining_pieces
			return max(0, int(round(stored * (avail_qty / total_qty))))

	# No stored pieces — derive once from undelivered weight
	if avail_qty > 0 and length and section_weight:
		return int_pieces_from_qty(avail_qty, length, section_weight)

	return max(0, int(round(stored))) if stored > 0 else 0


def physical_batch_pieces(batch_no):
	"""Piece count stamped on Batch (FWO / piece ledger), else 0."""
	if not batch_no:
		return 0
	return cint(frappe.db.get_value("Batch", batch_no, "pieces") or 0)


def cap_pieces_to_physical_batch(pieces, physical_pieces):
	"""Never stamp more pieces than the batch physically holds.

	SO auto-reserve ceils reserved weight (21 bars → 22 PC) while FWO
	left 21 on the batch. Cap the count only — reserved qty (tonnes)
	stays untouched so Sales remaining-qty math does not move.
	A missing / zero physical count is left as-is (batch not stamped yet).
	"""
	pieces = cint(pieces)
	physical = cint(physical_pieces)
	if physical > 0 and pieces > physical:
		return physical
	return max(0, pieces)


def cap_resolved_pieces_for_batch(pieces, batch_no):
	return cap_pieces_to_physical_batch(pieces, physical_batch_pieces(batch_no))


def cap_pieces_using_sre_batches(pieces, sre_rows, physical_lookup=None):
	"""Cap a DN/SRE piece total against the unique batches on those rows."""
	lookup = physical_lookup or physical_batch_pieces
	seen = set()
	physical_total = 0
	for row in sre_rows or []:
		batch_no = (
			row.get("batch_no") if isinstance(row, dict) else getattr(row, "batch_no", None)
		)
		if not batch_no or batch_no in seen:
			continue
		seen.add(batch_no)
		physical_total += cint(lookup(batch_no) or 0)
	if not seen:
		return cint(pieces)
	return cap_pieces_to_physical_batch(pieces, physical_total)


def qty_from_pieces(pieces, length, section_weight):
	pieces = flt(pieces)
	length = flt(length)
	section_weight = flt(section_weight)
	if not pieces or not length or not section_weight:
		return 0
	return (pieces * length * section_weight) / 1000


def should_preserve_pieces_for_qty_change(
	qty_before, qty_after, length=0, section_weight=0, piece_weight=None
):
	"""True when a weight change is too small to add or remove a physical piece.

	Pieces are a count of bars, not a rounding of tonnes. A Delivery Note
	must not turn 32 PC into 33, or 40 PC into 39, only because quantity
	moved by less than one piece. A real partial delivery (about half the
	weight) still returns False so the caller can scale.

	``int_pieces_from_qty`` itself is unchanged — Sales Order and planning
	still ceil from weight.
	"""
	delta = abs(flt(qty_before) - flt(qty_after))
	if delta <= 0.0001:
		return True

	weight = (
		flt(piece_weight)
		if piece_weight is not None
		else qty_from_pieces(1, length, section_weight)
	)
	if weight <= 0:
		return False
	return delta < weight


def pieces_for_weight_reconciliation(prior_qty, target_qty, length=0, section_weight=0):
	"""Pieces a qty-only Stock Reconciliation may add.

	Deliver-as-Qty can bill more tonnes than the batch holds. That weight
	is topped up by reconciliation. It is not a new bar, so the piece
	count must stay whatever the Delivery Note already has — which is
	capped to the physical batch and is not recalculated from the extra
	quantity. A full delivery of those pieces then leaves the batch at 0.

	Arguments are accepted so callers can pass the weight gap that used
	to be converted into pieces (for example 10.250 → 10.340 at
	9.350 m × 9.60 kg/m, which is just over one piece and used to post +1).
	"""
	return 0


def pieces_for_direct_batch_delivery(existing_pieces, qty, length, section_weight):
	"""Pieces to post on a Delivery Note row that has a batch and no bundle.

	An entered count is the physical delivery and is kept. Weight is used
	only when the row has no pieces yet.
	"""
	existing = max(0, int(round(flt(existing_pieces))))
	if existing > 0:
		return existing
	return int_pieces_from_qty(qty, length, section_weight)


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


def dn_pieces_for_invoice_change(
	entered_pieces, bundle_pieces, qty_before, qty_after, piece_weight=0,
	tolerance_percent=None,
):
	"""Piece count to keep when a Delivery Note's billed weight changes.

	An entered count never increases. A gap smaller than one piece, and a
	missing piece weight, also leave that count alone. A real shrink is
	scaled down only when the row still carries the whole bundle, so a
	partial the user already counted (8 PC of a 10 PC batch) stays 8.
	"""
	entered = max(0, int(round(flt(entered_pieces))))
	bundle = max(0, int(round(flt(bundle_pieces))))
	if entered <= 0:
		return 0

	if flt(qty_after) + 0.0001 >= flt(qty_before):
		return entered

	if tolerance_percent is not None:
		result = invoice_qty_tolerance_result(
			qty_before, qty_after, flt(tolerance_percent)
		)
		if not result.get("skip") and not result.get("zero_qty") and result.get("within"):
			return entered

	weight = flt(piece_weight)
	if weight <= 0 or should_preserve_pieces_for_qty_change(
		qty_before, qty_after, piece_weight=weight
	):
		return entered

	# The user already counted a partial. Do not scale that count again.
	if bundle <= 0 or entered < bundle or flt(qty_before) <= 0:
		return entered

	scaled = max(0, int(round(bundle * flt(qty_after) / flt(qty_before))))
	return min(entered, scaled)


def invoice_qty_tolerance_result(
	qty, invoice_qty, tolerance_percent=3, precision=3, conversion_factor=1
):
	"""Compare Invoice Qty with Qty using (Invoice Qty - Qty) / Qty × 100.

	Invoice Qty absent or zero skips the check. Qty zero with an Invoice Qty
	present is reported separately so the caller can avoid dividing by zero.
	Exactly the configured percent is allowed. Both quantities are converted
	and rounded to stock precision before the comparison.
	"""
	if invoice_qty in (None, "") or flt(invoice_qty) == 0:
		return {"skip": True, "zero_qty": False, "within": True, "percent": 0.0}

	prec = int(precision) if precision not in (None, "") else 3
	prec = max(0, prec)
	factor = flt(conversion_factor) or 1
	qty_s = flt(flt(qty) * factor, prec)
	inv_s = flt(flt(invoice_qty) * factor, prec)

	if qty_s == 0:
		return {"skip": False, "zero_qty": True, "within": False, "percent": None}

	if qty_s == inv_s:
		return {"skip": False, "zero_qty": False, "within": True, "percent": 0.0}

	diff = inv_s - qty_s
	percent = (diff / qty_s) * 100.0
	allowed = abs(qty_s) * (flt(tolerance_percent) / 100.0)
	margin = 0.5 * (10 ** (-prec))
	return {
		"skip": False,
		"zero_qty": False,
		"within": abs(diff) <= allowed + margin,
		"percent": percent,
	}


def absorb_rounding_dust(amounts, target, precision=3):
	"""Make rounded shares add up to the rounded target.

	The leftover is one precision unit spread across the rows. It is applied
	to the last positive row. A gap larger than one unit per row is left as
	it is, so a real shortage is still visible to the caller.
	"""
	prec = max(0, int(precision or 0))
	rounded = [flt(amount, prec) for amount in (amounts or [])]
	target = flt(target, prec)
	dust = flt(target - sum(rounded), prec)
	if dust == 0 or not rounded:
		return rounded

	step = flt(10 ** (-prec), prec) if prec else 1
	if abs(dust) > flt(step * max(len(rounded), 1), prec):
		return rounded

	for index in range(len(rounded) - 1, -1, -1):
		if rounded[index] <= 0:
			continue
		updated = flt(rounded[index] + dust, prec)
		if updated < 0:
			continue
		rounded[index] = updated
		return rounded
	return rounded
