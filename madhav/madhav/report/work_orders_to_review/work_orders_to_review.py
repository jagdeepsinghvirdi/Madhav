# Copyright (c) 2026, Finbyz pvt. ltd. and contributors
# For license information, please see license.txt
"""Open Work Orders that have stopped moving, to close in planned batches.

Closing a Work Order gives its unproduced quantity (and pieces) back to the
Sales Order for a new Production Plan, so old open Work Orders should be
reviewed rather than closed all at once.
"""

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, getdate, nowdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Work Order"), "fieldname": "name", "fieldtype": "Link", "options": "Work Order", "width": 150},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 90},
		{"label": _("Item"), "fieldname": "production_item", "fieldtype": "Link", "options": "Item", "width": 100},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 170},
		{"label": _("Sales Order"), "fieldname": "sales_order", "fieldtype": "Link", "options": "Sales Order", "width": 130},
		{"label": _("Customer"), "fieldname": "customer_name", "fieldtype": "Data", "width": 170},
		{"label": _("Order Status"), "fieldname": "so_status", "fieldtype": "Data", "width": 120},
		{"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "precision": 3, "width": 85},
		{"label": _("Produced"), "fieldname": "produced_qty", "fieldtype": "Float", "precision": 3, "width": 85},
		{"label": _("Not Produced"), "fieldname": "unproduced_qty", "fieldtype": "Float", "precision": 3, "width": 95},
		{"label": _("Pieces"), "fieldname": "pieces", "fieldtype": "Int", "width": 70},
		{"label": _("Pieces Not Made"), "fieldname": "unmade_pieces", "fieldtype": "Int", "width": 95},
		{"label": _("Created"), "fieldname": "created", "fieldtype": "Date", "width": 95},
		{"label": _("Last Production"), "fieldname": "last_production", "fieldtype": "Date", "width": 105},
		{"label": _("Idle Days"), "fieldname": "idle_days", "fieldtype": "Int", "width": 80},
		{"label": _("Suggestion"), "fieldname": "suggestion", "fieldtype": "Data", "width": 260},
	]


def get_data(filters):
	idle_days = cint(filters.idle_days) if filters.get("idle_days") is not None else 30
	conditions = ["wo.docstatus = 1", "wo.status in ('Not Started', 'In Process')"]
	values = {}
	if filters.status:
		conditions.append("wo.status = %(status)s")
		values["status"] = filters.status
	if filters.sales_order:
		conditions.append("wo.sales_order = %(sales_order)s")
		values["sales_order"] = filters.sales_order
	if filters.production_item:
		conditions.append("wo.production_item = %(production_item)s")
		values["production_item"] = filters.production_item
	if filters.customer:
		conditions.append("so.customer = %(customer)s")
		values["customer"] = filters.customer

	rows = frappe.db.sql(
		f"""
		select wo.name, wo.status, wo.production_item, wo.item_name, wo.sales_order,
			wo.qty, wo.produced_qty, ifnull(wo.pieces, 0) as pieces,
			ifnull(wo.completed_pcs, 0) as completed_pcs, date(wo.creation) as created,
			so.customer_name, so.status as so_status,
			(select max(se.posting_date) from `tabStock Entry` se
				where se.work_order = wo.name and se.docstatus = 1
				and se.purpose = 'Manufacture') as last_production
		from `tabWork Order` wo
		left join `tabSales Order` so on so.name = wo.sales_order
		where {" and ".join(conditions)}
		order by wo.creation asc
		""",
		values,
		as_dict=True,
	)

	today = getdate(nowdate())
	out = []
	for r in rows:
		since = r.last_production or r.created
		r.idle_days = date_diff(today, since) if since else 0
		if r.idle_days < idle_days:
			continue
		r.unproduced_qty = max(0.0, flt(r.qty) - flt(r.produced_qty))
		r.unmade_pieces = max(0, cint(r.pieces) - cint(r.completed_pcs))
		r.suggestion, r.suggestion_level = suggest(r)
		out.append(r)
	return out


def suggest(r):
	if r.so_status in ("Closed", "Completed", "Cancelled"):
		return _("Order is {0}: close this Work Order").format(r.so_status), "close"
	if flt(r.unproduced_qty) <= 0.001:
		return _("Everything produced: close this Work Order"), "close"
	if r.status == "Not Started":
		return _("Never started: cancel it or start it"), "review"
	return _("Check with production; closing gives {0} back to planning").format(
		flt(r.unproduced_qty, 3)
	), "review"
