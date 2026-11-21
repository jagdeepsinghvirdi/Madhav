"""Close a Sales Order that still holds reserved stock (team feedback 3, 21 Nov 2026).

ERPNext (31 Oct 2026) refuses to close an order with reserved stock: the user
first unreserves it on the order and deals with partly delivered reservations
through their Delivery Notes. The team asked for one step instead:

* on Close, what is still reserved and not delivered is unreserved;
* that stock moves to the For Mill (EXTRA) warehouse;
* the order is Closed. Re-opening it works as before: nothing comes back
  reserved; the stock in For Mill (EXTRA) can be reserved again like any other.

A reservation with nothing delivered is cancelled. One that is partly
delivered is closed at what was delivered (its Delivery Notes stay as they
are). Everything moved goes in one Material Transfer, recorded on the order.
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, nowdate, nowtime

FOR_MILL = "For Mill (EXTRA)"
QTY_EPSILON = 0.0005


def for_mill_warehouse(company):
	return frappe.db.get_value(
		"Warehouse", {"warehouse_name": FOR_MILL, "company": company, "is_group": 0, "disabled": 0}, "name"
	)


def _open_reservations(sales_order):
	return frappe.get_all(
		"Stock Reservation Entry",
		filters={
			"voucher_type": "Sales Order",
			"voucher_no": sales_order,
			"docstatus": 1,
			"status": ["not in", ["Delivered", "Cancelled"]],
		},
		fields=["name", "item_code", "warehouse", "reserved_qty", "delivered_qty", "reservation_based_on"],
		order_by="creation",
	)


def _open_batches(sre_name):
	"""Undelivered part of each batch row of a reservation, with its pieces."""
	out = []
	for e in frappe.get_all(
		"Serial and Batch Entry",
		filters={"parent": sre_name, "parenttype": "Stock Reservation Entry"},
		fields=["batch_no", "qty", "delivered_qty", "pieces", "length", "section_weight", "warehouse"],
	):
		open_qty = flt(e.qty) - flt(e.delivered_qty)
		if not e.batch_no or open_qty <= QTY_EPSILON:
			continue
		pieces = cint(round(flt(e.pieces) * open_qty / flt(e.qty))) if flt(e.qty) else 0
		out.append(frappe._dict(e, open_qty=flt(open_qty, 3), open_pieces=pieces))
	return out


def _live_batch_qty(item_code, warehouse, batch_no):
	from madhav.madhav.doctype.stock_transfer.stock_transfer import get_live_batch_qty

	return flt(get_live_batch_qty(item_code, warehouse, batch_no))


def plan_release(sales_order):
	"""What closing would unreserve and move. Nothing is changed."""
	so = frappe.get_cached_doc("Sales Order", sales_order) if isinstance(sales_order, str) else sales_order
	target = for_mill_warehouse(so.company)
	reservations, moves, unreserve_only = [], {}, []
	for sre in _open_reservations(so.name):
		open_qty = flt(sre.reserved_qty) - flt(sre.delivered_qty)
		if open_qty <= QTY_EPSILON:
			continue
		reservations.append(frappe._dict(sre, open_qty=flt(open_qty, 3)))
		if sre.reservation_based_on != "Serial and Batch":
			unreserve_only.append(frappe._dict(item_code=sre.item_code, warehouse=sre.warehouse, qty=flt(open_qty, 3)))
			continue
		for e in _open_batches(sre.name):
			source = e.warehouse or sre.warehouse
			if source == target:
				continue  # already in For Mill (EXTRA): unreserving is enough
			key = (sre.item_code, source, e.batch_no)
			m = moves.setdefault(key, frappe._dict(item_code=sre.item_code, warehouse=source, batch_no=e.batch_no,
				qty=0.0, pieces=0, length=flt(e.length), section_weight=flt(e.section_weight)))
			m.qty += e.open_qty
			m.pieces += e.open_pieces
	# Never move more than the batch physically holds in that warehouse.
	for m in moves.values():
		live = _live_batch_qty(m.item_code, m.warehouse, m.batch_no)
		if m.qty > live:
			if m.qty and m.pieces:
				m.pieces = cint(round(m.pieces * live / m.qty))
			m.qty = live
		m.qty = flt(m.qty, 3)
		if not m.length or not m.section_weight:
			b = frappe.db.get_value("Batch", m.batch_no, ["average_length", "section_weight"], as_dict=True) or {}
			m.length = m.length or flt(b.get("average_length"))
			m.section_weight = m.section_weight or flt(b.get("section_weight"))
	return frappe._dict(
		target=target,
		reservations=reservations,
		moves=[m for m in moves.values() if m.qty > QTY_EPSILON],
		unreserve_only=unreserve_only,
	)


@frappe.whitelist()
def get_close_summary(sales_order):
	"""For the Close confirmation on the form."""
	frappe.has_permission("Sales Order", "read", sales_order, throw=True)
	plan = plan_release(sales_order)
	return {
		"target": plan.target,
		"reserved_qty": flt(sum(r.open_qty for r in plan.reservations), 3),
		"reservations": len(plan.reservations),
		"move_qty": flt(sum(m.qty for m in plan.moves), 3),
		"moves": plan.moves,
	}


def release_and_move_on_close(so):
	"""Unreserve the order's open reservations and move their batches to For Mill (EXTRA)."""
	from madhav.doc_events.delivery_note import _release_leftover_on_sre

	plan = plan_release(so)
	if not plan.reservations:
		return None
	if plan.moves and not plan.target:
		frappe.throw(_("Warehouse {0} not found for {1}.").format(frappe.bold(FOR_MILL), so.company))

	for r in plan.reservations:
		if flt(r.delivered_qty) <= QTY_EPSILON:
			sre = frappe.get_doc("Stock Reservation Entry", r.name)
			sre.flags.ignore_permissions = True
			sre.cancel()
		else:
			_release_leftover_on_sre(r.name)

	se_name = _move_to_for_mill(so, plan) if plan.moves else None

	lines = [
		_("{0}: {1} t unreserved").format(r.name, r.open_qty) for r in plan.reservations
	]
	if se_name:
		lines.append(
			_("Moved {0} t to {1} by {2}").format(
				flt(sum(m.qty for m in plan.moves), 3), plan.target, frappe.get_desk_link("Stock Entry", se_name)
			)
		)
	frappe.get_doc({
		"doctype": "Comment",
		"comment_type": "Info",
		"reference_doctype": "Sales Order",
		"reference_name": so.name,
		"content": _("Closed: reserved stock released.") + "<br>" + "<br>".join(lines),
	}).insert(ignore_permissions=True)
	frappe.msgprint(
		_("{0} t reserved stock was unreserved{1}.").format(
			flt(sum(r.open_qty for r in plan.reservations), 3),
			_(" and moved to {0} ({1})").format(plan.target, se_name) if se_name else "",
		),
		indicator="green",
		alert=True,
	)
	return se_name


def _move_to_for_mill(so, plan):
	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Material Transfer"
	se.purpose = "Material Transfer"
	se.company = so.company
	se.set_posting_time = 1
	se.posting_date = nowdate()
	se.posting_time = nowtime()
	se.to_warehouse = plan.target
	if se.meta.has_field("sales_order_no"):
		se.sales_order_no = so.name
	se.remarks = _("Reserved stock of Sales Order {0}, released when the order was closed.").format(so.name)
	branch = so.get("branch")
	cost_center = so.get("cost_center")
	for m in plan.moves:
		row = {
			"item_code": m.item_code,
			"qty": m.qty,
			"s_warehouse": m.warehouse,
			"t_warehouse": plan.target,
			"batch_no": m.batch_no,
			"use_serial_batch_fields": 1,
			"pieces": cint(m.pieces),
			"average_length": flt(m.length),
			"section_weight": flt(m.section_weight),
		}
		if cost_center:
			row["cost_center"] = cost_center
		if branch:
			row["branch"] = branch
		se.append("items", row)
	if branch and se.meta.has_field("branch"):
		se.branch = branch
	se.flags.ignore_permissions = True
	se.insert(ignore_permissions=True)
	se.submit()
	return se.name


def validate_no_reserved_stock(self):
	"""Replaces ERPNext's "Unreserve it before closing" check on Sales Order.

	ERPNext calls this only when the order is being Closed.
	"""
	release_and_move_on_close(self)


def skip_reservation_for_closed_order(sales_order, item_code=None):
	"""True (with a note to the user) when the order is Closed.

	Finish Work Order and Stock Transfer reserved new stock for orders that
	were already Closed (28 of the reservations on Closed orders, Nov 2026),
	stock nobody could deliver. A re-opened order is reserved for as usual.
	"""
	if not sales_order or frappe.db.get_value("Sales Order", sales_order, "status") != "Closed":
		return False
	frappe.msgprint(
		_("Stock not reserved for {0}: Sales Order {1} is Closed.").format(
			frappe.bold(item_code or _("this item")), frappe.bold(sales_order)
		),
		title=_("Reservation Skipped"),
		indicator="orange",
		alert=True,
	)
	return True
