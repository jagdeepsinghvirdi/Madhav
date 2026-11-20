"""Repack: help find the right batch (round 4, team batch sheet Part B, Nov 2026).

Repack is the one screen where people pick a batch by hand, and the team's
difficulty is finding the right one, and reserved stock has to be unreserved
on another screen first. This module:

* lists the batches of an item in the source warehouse with length, pieces,
  free qty and who the rest is reserved for, free stock first, oldest first;
* suggests free batches, oldest first, for a qty or a piece count;
* "Unreserve and use": a source row can name the reservation it may take
  from. On submit only what the row needs beyond the batch's free stock comes
  off that reservation; it is recorded on the Repack and put back exactly when
  the Repack is cancelled.
"""
from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, flt

UNRESERVE_FIELD = "custom_unreserve_from"
RELEASE_LOG_FIELD = "custom_repack_released_reservations"
QTY_EPSILON = 0.0005


# ── reading stock ───────────────────────────────────────────────────────────


def _batches_with_stock(item_code, warehouse):
	from erpnext.stock.doctype.batch.batch import get_batch_qty

	rows = get_batch_qty(warehouse=warehouse, item_code=item_code) or []
	return [r for r in rows if r.get("batch_no") and flt(r.get("qty")) > QTY_EPSILON]


def batch_reservations(item_code, warehouse, batch_no):
	"""Open reservations holding this batch here, largest first."""
	rows = frappe.db.sql(
		"""
		select sre.name as sre, sre.voucher_no as sales_order,
			so.customer, so.customer_name, so.po_no,
			sum(sbe.qty - ifnull(sbe.delivered_qty, 0)) as qty,
			sum(ifnull(sbe.pieces, 0)) as pieces,
			sum(sbe.qty) as entry_qty, sum(ifnull(sbe.delivered_qty, 0)) as delivered
		from `tabStock Reservation Entry` sre
		inner join `tabSerial and Batch Entry` sbe
			on sbe.parent = sre.name and sbe.parenttype = 'Stock Reservation Entry'
		left join `tabSales Order` so
			on so.name = sre.voucher_no and sre.voucher_type = 'Sales Order'
		where sre.docstatus = 1
			and sre.status in ('Reserved', 'Partially Reserved', 'Partially Delivered')
			and sre.item_code = %(item_code)s and sre.warehouse = %(warehouse)s
			and sbe.batch_no = %(batch_no)s
		group by sre.name
		having qty > 0
		order by qty desc
		""",
		{"item_code": item_code, "warehouse": warehouse, "batch_no": batch_no},
		as_dict=True,
	)
	for r in rows:
		r.qty = flt(r.qty, 3)
		# Pieces still reserved: the stored count scaled to the open share.
		total = flt(r.entry_qty)
		r.pieces = int(round(cint(r.pieces) * flt(r.qty) / total)) if total > 0 else 0
		r.pop("entry_qty", None)
		r.pop("delivered", None)
	return rows


def get_repack_batch_options(item_code, warehouse, min_length=None):
	"""Batches of an item in a warehouse, free stock first, oldest first."""
	from madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool import (
		get_batch_reservation_limits,
	)

	if not item_code or not warehouse:
		return []
	stock = _batches_with_stock(item_code, warehouse)
	if not stock:
		return []
	names = [r["batch_no"] for r in stock]
	meta = {
		b.name: b
		for b in frappe.get_all(
			"Batch",
			filters={"name": ["in", names]},
			fields=["name", "average_length", "section_weight", "pieces", "manufacturing_date", "creation"],
		)
	}
	out = []
	for r in stock:
		b = meta.get(r["batch_no"]) or frappe._dict()
		if min_length and flt(b.average_length) < flt(min_length):
			continue
		limits = get_batch_reservation_limits(item_code, warehouse, r["batch_no"])
		reservations = batch_reservations(item_code, warehouse, r["batch_no"])
		out.append(
			frappe._dict(
				batch_no=r["batch_no"],
				length=flt(b.average_length),
				section_weight=flt(b.section_weight),
				qty=flt(r["qty"], 3),
				pieces=cint(b.pieces),
				free_qty=flt(limits.get("qty"), 3),
				free_pieces=cint(limits.get("pieces")),
				reservations=reservations,
				batch_date=b.manufacturing_date,
				creation=b.creation,
			)
		)
	out.sort(
		key=lambda d: (
			0 if d.free_qty > QTY_EPSILON else 1,
			str(d.batch_date or "9999-12-31"),
			str(d.creation or ""),
			d.batch_no,
		)
	)
	return out


def _option_label(d):
	parts = [
		_("L {0} m").format(flt(d.length, 2)),
		_("{0} pcs").format(d.pieces),
		_("{0} t").format(flt(d.qty, 3)),
		_("free {0} t / {1} pcs").format(flt(d.free_qty, 3), d.free_pieces),
	]
	for r in d.reservations[:2]:
		who = r.customer_name or r.customer or ""
		parts.append(_("reserved {0} t: {1} {2}").format(flt(r.qty, 3), r.sales_order, who).strip())
	if len(d.reservations) > 2:
		parts.append(_("+{0} more reservations").format(len(d.reservations) - 2))
	if d.batch_date:
		parts.append(frappe.utils.formatdate(d.batch_date))
	return " · ".join(parts)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def repack_batch_query(doctype, txt, searchfield, start, page_len, filters):
	"""Batch picker for Repack source rows: free stock first, oldest first."""
	filters = frappe._dict(filters or {})
	options = get_repack_batch_options(filters.item_code, filters.warehouse, filters.average_length)
	txt = (txt or "").lower()
	if txt:
		options = [d for d in options if txt in d.batch_no.lower()]
	start, page_len = cint(start), cint(page_len) or 20
	return [(d.batch_no, _option_label(d)) for d in options[start : start + page_len]]


# ── suggestions ─────────────────────────────────────────────────────────────


@frappe.whitelist()
def suggest_repack_batches(item_code, warehouse, pieces=0, qty=0, length=None):
	"""Free batches, oldest first, for a piece count or a qty.

	Only free stock is suggested; reserved stock needs "Unreserve and use".
	"""
	from madhav.madhav.utils.reserved_length_groups import allocate_pieces_fifo, allocate_qty_fifo

	pieces, qty = cint(pieces), flt(qty)
	pool = []
	for d in get_repack_batch_options(item_code, warehouse):
		if length and abs(flt(d.length) - flt(length)) > 0.005:
			continue
		if d.free_qty <= QTY_EPSILON:
			continue
		pool.append(
			{
				"batch_no": d.batch_no,
				"warehouse": warehouse,
				"qty": d.free_qty,
				"pieces": d.free_pieces,
				"length": d.length,
				"section_weight": d.section_weight,
				"batch_date": d.batch_date,
				"creation": d.creation,
			}
		)
	if pieces > 0:
		allocations, short_pieces = allocate_pieces_fifo(pool, pieces)
		short_qty = 0
	else:
		allocations, short_qty = allocate_qty_fifo(pool, qty)
		short_pieces = 0
	return {
		"rows": [
			{
				"batch_no": a["batch_no"],
				"qty": flt(a["take_qty"], 3),
				"pieces": cint(a["take_pieces"]),
				"average_length": flt(a["length"]),
				"section_weight": flt(a["section_weight"]),
			}
			for a in allocations
		],
		"short_pieces": cint(short_pieces),
		"short_qty": flt(short_qty, 3),
		"free_pieces": sum(cint(p["pieces"]) for p in pool),
		"free_qty": flt(sum(flt(p["qty"]) for p in pool), 3),
	}


@frappe.whitelist()
def get_batch_reservations_for_repack(item_code, warehouse, batch_no):
	"""What holds a batch, for the "Unreserve and use" dialog."""
	from madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool import (
		get_batch_reservation_limits,
	)

	limits = get_batch_reservation_limits(item_code, warehouse, batch_no)
	return {
		"free_qty": flt(limits.get("qty"), 3),
		"free_pieces": cint(limits.get("pieces")),
		"reservations": batch_reservations(item_code, warehouse, batch_no),
		"can_unreserve": bool(frappe.has_permission("Stock Reservation Entry", "cancel")),
	}


# ── unreserve and use (submit / cancel of the Repack) ───────────────────────


def _is_repack(doc):
	return doc.get("purpose") == "Repack" or doc.get("stock_entry_type") == "Repack"


def _snapshot(sre):
	from madhav.doc_events.delivery_note import _snapshot_sre

	return _snapshot_sre(sre)


def _refresh(sre_name):
	from madhav.doc_events.delivery_note import _refresh_sre_totals

	_refresh_sre_totals(sre_name)


def release_reservations_for_repack(doc, method=None):
	"""before_submit: take what each marked row needs off its named reservation."""
	from madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool import (
		get_batch_available_qty,
	)

	if not _is_repack(doc):
		return
	rows = [r for r in doc.get("items") or [] if r.get(UNRESERVE_FIELD) and r.get("s_warehouse")]
	if not rows:
		return
	if not frappe.has_permission("Stock Reservation Entry", "cancel"):
		frappe.throw(
			_("You are not allowed to unreserve stock. Ask someone who can cancel Stock Reservation Entries."),
			frappe.PermissionError,
		)

	log = []
	# Free stock already used by earlier rows on this Repack.
	used_free = {}
	for row in rows:
		if not row.get("batch_no"):
			frappe.throw(_("Row #{0}: pick a batch to unreserve.").format(row.idx))
		key = (row.item_code, row.s_warehouse, row.batch_no)
		need = flt(row.get("transfer_qty") or row.get("qty")) * (
			1 if row.get("transfer_qty") else flt(row.get("conversion_factor") or 1)
		)
		free = max(0.0, flt(get_batch_available_qty(*key)) - flt(used_free.get(key)))
		from_free = min(free, need)
		used_free[key] = flt(used_free.get(key)) + from_free
		take = flt(need - from_free, 3)
		if take <= QTY_EPSILON:
			continue

		sre = frappe.get_doc("Stock Reservation Entry", row.get(UNRESERVE_FIELD))
		if sre.docstatus != 1 or sre.item_code != row.item_code or sre.warehouse != row.s_warehouse:
			frappe.throw(
				_("Row #{0}: reservation {1} is not an open reservation of {2} in {3}.").format(
					row.idx, sre.name, row.item_code, row.s_warehouse
				)
			)
		entries = [e for e in sre.sb_entries if e.batch_no == row.batch_no]
		open_qty = sum(flt(e.qty) - flt(e.delivered_qty) for e in entries)
		if open_qty + QTY_EPSILON < take:
			frappe.throw(
				_(
					"Row #{0}: batch {1} needs {2} more than its free stock, but reservation {3} holds only {4} of it."
				).format(row.idx, frappe.bold(row.batch_no), take, sre.name, flt(open_qty, 3))
			)

		snapshot = _snapshot(sre)
		released = {"sre": sre.name, "batch_no": row.batch_no, "row": row.name, "entries": []}
		left = take
		for e in entries:
			if left <= QTY_EPSILON:
				break
			e_open = flt(e.qty) - flt(e.delivered_qty)
			if e_open <= QTY_EPSILON:
				continue
			cut = min(e_open, left)
			# Pieces leave with the stock, in the entry's own pieces-per-qty.
			e_pieces = cint(flt(e.get("pieces")))
			cut_pieces = int(round(e_pieces * cut / flt(e.qty))) if flt(e.qty) else 0
			released["entries"].append({"entry": e.name, "qty": flt(cut, 6), "pieces": cut_pieces})
			left -= cut

		total_cut = sum(x["qty"] for x in released["entries"])
		remaining_open = flt(sre.reserved_qty) - flt(sre.delivered_qty) - total_cut
		if flt(sre.delivered_qty) <= QTY_EPSILON and remaining_open <= QTY_EPSILON:
			# Nothing delivered and nothing left: cancel rather than leave a
			# reservation of 0 that ERPNext would show as "Delivered".
			released["cancelled"] = 1
			released["snapshot"] = snapshot
			sre.flags.ignore_permissions = True
			sre.cancel()
		else:
			by_name = {e.name: e for e in sre.sb_entries}
			for x in released["entries"]:
				e = by_name[x["entry"]]
				e.db_set("qty", flt(e.qty) - x["qty"], update_modified=False)
				if x["pieces"]:
					e.db_set("pieces", max(0, cint(flt(e.pieces)) - x["pieces"]), update_modified=False)
			sre.db_set("reserved_qty", flt(sre.reserved_qty) - total_cut, update_modified=False)
			_refresh(sre.name)
		log.append(released)

	if log:
		doc.set(RELEASE_LOG_FIELD, json.dumps(log, default=str))
		for r in log:
			frappe.get_doc(
				{
					"doctype": "Comment",
					"comment_type": "Info",
					"reference_doctype": "Stock Reservation Entry",
					"reference_name": r["sre"],
					"content": _("{0} of batch {1} unreserved for Repack {2}.").format(
						flt(sum(x["qty"] for x in r["entries"]), 3), r["batch_no"], doc.name
					),
				}
			).insert(ignore_permissions=True)


def restore_reservations_after_repack_cancel(doc, method=None):
	"""on_cancel: put every reservation this Repack took from back exactly."""
	if not _is_repack(doc) or not doc.get(RELEASE_LOG_FIELD):
		return
	try:
		log = json.loads(doc.get(RELEASE_LOG_FIELD))
	except Exception:
		return
	for r in reversed(log):
		if r.get("cancelled"):
			_recreate_from_snapshot(r["snapshot"], doc)
			continue
		if cint(frappe.db.get_value("Stock Reservation Entry", r["sre"], "docstatus")) != 1:
			frappe.msgprint(
				_("Reservation {0} was cancelled after this Repack; it was not restored.").format(r["sre"]),
				indicator="orange",
			)
			continue
		sre = frappe.get_doc("Stock Reservation Entry", r["sre"])
		by_name = {e.name: e for e in sre.sb_entries}
		added = 0.0
		for x in r["entries"]:
			e = by_name.get(x["entry"])
			if not e:
				continue
			e.db_set("qty", flt(e.qty) + flt(x["qty"]), update_modified=False)
			if cint(x.get("pieces")):
				e.db_set("pieces", cint(flt(e.pieces)) + cint(x["pieces"]), update_modified=False)
			added += flt(x["qty"])
		sre.db_set("reserved_qty", flt(sre.reserved_qty) + added, update_modified=False)
		_refresh(sre.name)


def _recreate_from_snapshot(snap, doc):
	sre = frappe.new_doc("Stock Reservation Entry")
	for field in (
		"item_code", "warehouse", "company", "stock_uom", "voucher_type", "voucher_no",
		"voucher_detail_no", "voucher_qty", "reservation_based_on", "has_batch_no", "has_serial_no",
		"from_voucher_type", "from_voucher_no", "from_voucher_detail_no",
	):
		if snap.get(field) is not None:
			sre.set(field, snap.get(field))
	sre.available_qty = flt(snap.get("available_qty")) or flt(snap.get("reserved_qty"))
	sre.reserved_qty = flt(snap.get("reserved_qty"))
	for e in snap.get("sb_entries") or []:
		sre.append(
			"sb_entries",
			{
				"batch_no": e.get("batch_no"),
				"serial_no": e.get("serial_no"),
				"qty": flt(e.get("qty")),
				"warehouse": e.get("warehouse") or snap.get("warehouse"),
				"pieces": flt(e.get("pieces")),
				"length": flt(e.get("length")),
				"section_weight": flt(e.get("section_weight")),
			},
		)
	sre.flags.ignore_permissions = True
	sre.insert(ignore_permissions=True)
	sre.submit()
	frappe.msgprint(
		_("Reservation {0} for {1} was recreated as {2} after cancelling Repack {3}.").format(
			snap.get("name"), snap.get("voucher_no"), sre.name, doc.name
		),
		indicator="green",
	)
	return sre.name
