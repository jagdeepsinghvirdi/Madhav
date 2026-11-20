"""Reserved stock grouped by length, used oldest batch first (team, Nov 2026).

A Sales Order line is usually reserved as several batches: Finish Work Order
makes one batch per row, Stock Transfer and the reservation tool add more.
The team wants one Delivery Note / Stock Transfer row per item + length with
the pieces added up, the batches kept behind it in the Serial and Batch
Bundle, and the oldest batch used first when fewer pieces go out.

The pure functions here take plain dicts so they can be tested without a
database; ``get_reserved_pool`` reads the reservations.

A pool entry is a dict with:
    sre, batch_no, warehouse, qty (open, stock UOM), pieces (open, whole),
    length, section_weight, assorted_length, batch_date, creation
"""
from __future__ import annotations

import json
import math

import frappe
from frappe.utils import cint, flt, getdate

QTY_PRECISION = 3  # stock qty in tonnes, as on the Delivery Note row
QTY_EPSILON = 1e-6


# ── pure helpers ────────────────────────────────────────────────────────────


def length_key(length, assorted_length=None):
	"""Rows are merged only when the length matches to the centimetre.

	Assorted-length lots (T/L, F/L, R/L) never merge with fixed lengths.
	"""
	return (round(flt(length), 2), (assorted_length or "").strip())


def fifo_sort_key(entry):
	"""Oldest first: production date, then when the batch was created."""
	batch_date = entry.get("batch_date")
	creation = entry.get("creation")
	return (
		str(getdate(batch_date)) if batch_date else "9999-12-31",
		str(creation or ""),
		entry.get("batch_no") or "",
	)


def merge_entries_by_batch(entries):
	"""One pool entry per (warehouse, batch).

	The same batch can sit on two reservations of one line (two Stock
	Transfers, or a Party Transfer). A Serial and Batch Bundle cannot list a
	batch twice, so the open qty and pieces are added up; ``sres`` keeps every
	reservation the batch came from.
	"""
	merged = {}
	order = []
	for entry in entries or []:
		key = (entry.get("warehouse") or "", entry.get("batch_no"))
		if key not in merged:
			first = dict(entry)
			first["sres"] = [entry.get("sre")] if entry.get("sre") else []
			merged[key] = first
			order.append(key)
			continue
		existing = merged[key]
		existing["qty"] = flt(existing.get("qty")) + flt(entry.get("qty"))
		existing["pieces"] = cint(existing.get("pieces")) + cint(entry.get("pieces"))
		if entry.get("sre") and entry.get("sre") not in existing["sres"]:
			existing["sres"].append(entry.get("sre"))
	return [merged[k] for k in order]


def group_pool_by_length(entries):
	"""Group pool entries into Delivery Note rows: warehouse + length.

	Returns a list of groups, each ``{"warehouse", "length", "assorted_length",
	"entries" (oldest first), "qty", "pieces", "sres"}``. Groups come in the
	order of their oldest batch, so the oldest stock is listed first.
	"""
	groups = {}
	for entry in merge_entries_by_batch(entries):
		if flt(entry.get("qty")) <= QTY_EPSILON:
			continue
		lkey = length_key(entry.get("length"), entry.get("assorted_length"))
		key = (entry.get("warehouse") or "",) + lkey
		group = groups.setdefault(
			key,
			{
				"warehouse": entry.get("warehouse"),
				"length": lkey[0],
				"assorted_length": lkey[1] or None,
				"entries": [],
			},
		)
		group["entries"].append(entry)

	out = []
	for group in groups.values():
		group["entries"].sort(key=fifo_sort_key)
		group["qty"] = flt(sum(flt(e.get("qty")) for e in group["entries"]), QTY_PRECISION)
		group["pieces"] = sum(cint(e.get("pieces")) for e in group["entries"])
		sres = []
		for e in group["entries"]:
			for sre in e.get("sres") or ([e.get("sre")] if e.get("sre") else []):
				if sre not in sres:
					sres.append(sre)
		group["sres"] = sres
		out.append(group)
	out.sort(key=lambda g: fifo_sort_key(g["entries"][0]))
	return out


def _qty_for_pieces(entry, pieces):
	"""Weight of ``pieces`` bars out of one batch's open qty.

	All open pieces → all open qty, so no weight dust is left reserved.
	"""
	open_pieces = cint(entry.get("pieces"))
	open_qty = flt(entry.get("qty"))
	if pieces <= 0:
		return 0.0
	if open_pieces <= 0 or pieces >= open_pieces:
		return open_qty
	return flt(open_qty * pieces / open_pieces, QTY_PRECISION)


def _pieces_for_qty(entry, qty):
	"""Whole bars for ``qty`` out of one batch: a started bar counts as one."""
	open_pieces = cint(entry.get("pieces"))
	open_qty = flt(entry.get("qty"))
	if qty <= QTY_EPSILON or open_pieces <= 0 or open_qty <= 0:
		return 0
	if qty >= open_qty - QTY_EPSILON:
		return open_pieces
	return min(open_pieces, max(1, int(math.ceil(open_pieces * qty / open_qty - 1e-9))))


def allocate_pieces_fifo(entries, pieces):
	"""Take ``pieces`` bars from the oldest batch first.

	Returns ``(allocations, short_pieces)``; each allocation is the pool entry
	plus ``take_pieces`` and ``take_qty``. Batches not needed are left out.
	"""
	left = max(0, cint(pieces))
	allocations = []
	for entry in sorted(entries or [], key=fifo_sort_key):
		if left <= 0:
			break
		open_pieces = cint(entry.get("pieces"))
		if open_pieces <= 0:
			continue
		take = min(open_pieces, left)
		alloc = dict(entry)
		alloc["take_pieces"] = take
		alloc["take_qty"] = _qty_for_pieces(entry, take)
		allocations.append(alloc)
		left -= take
	return allocations, left


def allocate_qty_fifo(entries, qty):
	"""Take ``qty`` (stock UOM) from the oldest batch first.

	Pieces follow as whole bars per batch. Returns ``(allocations, short_qty)``.
	"""
	left = max(0.0, flt(qty))
	allocations = []
	for entry in sorted(entries or [], key=fifo_sort_key):
		if left <= QTY_EPSILON:
			break
		open_qty = flt(entry.get("qty"))
		if open_qty <= QTY_EPSILON:
			continue
		take = min(open_qty, left)
		if open_qty - take <= QTY_EPSILON:
			take = open_qty
		alloc = dict(entry)
		alloc["take_qty"] = flt(take, QTY_PRECISION)
		alloc["take_pieces"] = _pieces_for_qty(entry, take)
		allocations.append(alloc)
		left -= take
	return allocations, (left if left > QTY_EPSILON else 0.0)


def pool_to_json(entries):
	"""Compact record of a row's batch pool, oldest first (stored on the row)."""
	return json.dumps(
		[
			{"batch_no": e.get("batch_no"), "sres": e.get("sres") or ([e["sre"]] if e.get("sre") else [])}
			for e in sorted(entries or [], key=fifo_sort_key)
			if e.get("batch_no")
		]
	)


def pool_batches_from_json(value):
	"""Batch numbers recorded on a row, oldest first; [] when blank or bad."""
	if not value:
		return []
	try:
		data = json.loads(value) if isinstance(value, str) else value
	except Exception:
		return []
	out = []
	for d in data or []:
		batch_no = d.get("batch_no") if isinstance(d, dict) else d
		if batch_no and batch_no not in out:
			out.append(batch_no)
	return out


# ── database ───────────────────────────────────────────────────────────────


def get_reserved_pool(sales_order, so_detail, warehouse=None, sre_names=None, batch_nos=None):
	"""Open (undelivered) batch reservations of one Sales Order line.

	Only active Serial-and-Batch reservations. Pieces are whole bars still
	reserved on each batch, capped at what the batch physically holds.
	"""
	from madhav.madhav.utils.stock_piece_utils import (
		cap_resolved_pieces_for_batch,
		resolve_entry_length,
		resolve_entry_pieces,
		resolve_entry_section_weight,
	)

	conditions = [
		"sre.docstatus = 1",
		"sre.voucher_type = 'Sales Order'",
		"sre.voucher_no = %(sales_order)s",
		"sre.voucher_detail_no = %(so_detail)s",
		"sre.reservation_based_on = 'Serial and Batch'",
		"sre.status not in ('Delivered', 'Cancelled')",
		"sbe.qty > ifnull(sbe.delivered_qty, 0)",
		"ifnull(sbe.batch_no, '') != ''",
	]
	values = {"sales_order": sales_order, "so_detail": so_detail}
	if warehouse:
		conditions.append("sre.warehouse = %(warehouse)s")
		values["warehouse"] = warehouse
	if sre_names:
		conditions.append("sre.name in %(sre_names)s")
		values["sre_names"] = tuple(sre_names)
	if batch_nos:
		conditions.append("sbe.batch_no in %(batch_nos)s")
		values["batch_nos"] = tuple(batch_nos)

	has_assorted = frappe.db.has_column("Batch", "assorted_length")
	rows = frappe.db.sql(
		f"""
		select sre.name as sre, sre.item_code, sre.warehouse as sre_warehouse,
			sbe.batch_no, sbe.qty, sbe.delivered_qty, sbe.pieces, sbe.length,
			sbe.section_weight, sbe.warehouse,
			b.manufacturing_date as batch_date, b.creation,
			{"b.assorted_length" if has_assorted else "null"} as assorted_length
		from `tabStock Reservation Entry` sre
		inner join `tabSerial and Batch Entry` sbe
			on sbe.parent = sre.name and sbe.parenttype = 'Stock Reservation Entry'
		left join `tabBatch` b on b.name = sbe.batch_no
		where {" and ".join(conditions)}
		order by sre.creation, sbe.idx
		""",
		values,
		as_dict=True,
	)

	pool = []
	for row in rows:
		open_qty = flt(row.qty) - flt(row.delivered_qty)
		if open_qty <= QTY_EPSILON:
			continue
		length = resolve_entry_length(row, row.batch_no, so_detail)
		section_weight = resolve_entry_section_weight(row, row.item_code, length, row.batch_no)
		pieces = resolve_entry_pieces(row, open_qty, length, section_weight)
		pieces = cap_resolved_pieces_for_batch(pieces, row.batch_no)
		pool.append(
			frappe._dict(
				sre=row.sre,
				batch_no=row.batch_no,
				warehouse=row.warehouse or row.sre_warehouse,
				qty=open_qty,
				pieces=cint(pieces),
				length=flt(length),
				section_weight=flt(section_weight),
				assorted_length=row.assorted_length,
				batch_date=row.batch_date,
				creation=row.creation,
			)
		)
	return pool
