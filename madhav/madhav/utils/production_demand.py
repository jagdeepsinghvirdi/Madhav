# Copyright (c) 2026, Finbyz pvt. ltd. and contributors
# For license information, please see license.txt
"""What part of a Sales Order line's planned production is still alive.

``Sales Order Item.production_plan_qty`` (and Madhav's
``production_plan_pieces``) only ever grow when a Production Plan is
submitted. Two things make part of that planned quantity disappear
without touching the plan itself:

* **Released by a Work Order.**
  - Closed Work Order: the part it never produced (qty - produced).
  - Cancelled Work Order: its quantity, as long as nobody has made a new
    Work Order from the same plan row for it. ERPNext lowers the plan
    row's ``ordered_qty`` on cancel, so the plan row's un-ordered balance
    caps what is released; if a replacement Work Order is made from the
    old plan, the release shrinks by itself.
* **Given away by a Party Transfer.** Production stock reserved for this
  line that was moved to another party's order.

Effective planned = planned - released - given away. The Production Plan
remaining-quantity rule uses the effective figure, so the released and
given-away quantity can be put on a new Production Plan. Nothing is
stored: every figure is read from the Work Orders, plan rows and
transfers, so reopening a Work Order or cancelling a transfer corrects
it automatically.
"""

import frappe
from frappe.utils import cint, flt

# Reservations that come from stock not produced for that order line.
# The Production Plan rule subtracts them on top of the planned quantity.
FREE_STOCK_SOURCES = ("Batch Wise Reservation Tool", "Party Transfer")

# Reservations that are the output of the line's own Work Orders.
PRODUCTION_SOURCES = ("Finish Work Order", "Stock Transfer")


def free_stock_sources_sql():
	"""SQL tuple literal for FREE_STOCK_SOURCES."""
	return "(" + ", ".join(frappe.db.escape(s) for s in FREE_STOCK_SOURCES) + ")"


def is_free_stock_source(from_voucher_type):
	return (from_voucher_type or "") in FREE_STOCK_SOURCES


def get_work_order_release(sales_order_item):
	"""Quantity and pieces of planned production that Work Orders gave back.

	Returns frappe._dict(qty=..., pieces=..., closed_qty=..., cancelled_qty=...).
	"""
	out = frappe._dict(qty=0.0, pieces=0.0, closed_qty=0.0, cancelled_qty=0.0,
		closed_pieces=0.0, cancelled_pieces=0.0)
	if not sales_order_item:
		return out

	# Closed: only what was never produced comes back.
	closed = frappe.db.sql(
		"""
		select wo.qty, wo.produced_qty, ifnull(wo.pieces, 0) as pieces,
			ifnull(wo.completed_pcs, 0) as completed_pcs
		from `tabWork Order` wo
		where wo.sales_order_item = %s
			and wo.docstatus = 1
			and wo.status = 'Closed'
			and ifnull(wo.production_plan, '') != ''
			and ifnull(wo.production_plan_sub_assembly_item, '') = ''
		""",
		sales_order_item,
		as_dict=True,
	)
	for wo in closed:
		unmade = max(0.0, flt(wo.qty) - flt(wo.produced_qty))
		out.closed_qty += unmade
		if flt(wo.pieces) > 0:
			out.closed_pieces += max(0.0, flt(wo.pieces) - flt(wo.completed_pcs))

	# Cancelled: per plan row, capped by what that row has not re-ordered.
	cancelled = frappe.db.sql(
		"""
		select wo.production_plan_item as ppi,
			sum(wo.qty) as qty, sum(ifnull(wo.pieces, 0)) as pieces
		from `tabWork Order` wo
		inner join `tabProduction Plan` pp on pp.name = wo.production_plan
		where wo.sales_order_item = %s
			and wo.docstatus = 2
			and pp.docstatus = 1
			and ifnull(wo.production_plan_item, '') != ''
			and ifnull(wo.production_plan_sub_assembly_item, '') = ''
		group by wo.production_plan_item
		""",
		sales_order_item,
		as_dict=True,
	)
	for row in cancelled:
		ppi = frappe.db.get_value(
			"Production Plan Item",
			row.ppi,
			["planned_qty", "ordered_qty", "pieces"],
			as_dict=True,
		)
		if not ppi:
			continue
		unordered_qty = max(0.0, flt(ppi.planned_qty) - flt(ppi.ordered_qty))
		out.cancelled_qty += min(flt(row.qty), unordered_qty)

		if flt(ppi.pieces) > 0 and flt(row.pieces) > 0:
			active_pieces = flt(
				frappe.db.sql(
					"""
					select sum(ifnull(pieces, 0)) from `tabWork Order`
					where production_plan_item = %s and docstatus = 1
					""",
					row.ppi,
				)[0][0]
				or 0
			)
			unordered_pieces = max(0.0, flt(ppi.pieces) - active_pieces)
			out.cancelled_pieces += min(flt(row.pieces), unordered_pieces)

	out.qty = out.closed_qty + out.cancelled_qty
	out.pieces = out.closed_pieces + out.cancelled_pieces
	return out


def get_given_away(sales_order_item):
	"""Production stock of this line moved to other parties (submitted transfers)."""
	out = frappe._dict(qty=0.0, pieces=0.0)
	if not sales_order_item or not frappe.db.table_exists("Party Transfer Item"):
		return out
	row = frappe.db.sql(
		"""
		select sum(pti.transfer_qty), sum(pti.transfer_pieces)
		from `tabParty Transfer Item` pti
		inner join `tabParty Transfer` pt on pt.name = pti.parent
		where pti.from_sales_order_item = %s
			and pt.docstatus = 1
			and pti.source_type = 'Production'
		""",
		sales_order_item,
	)
	if row:
		out.qty = flt(row[0][0])
		out.pieces = flt(row[0][1])
	return out


def get_effective_planned(sales_order_item, production_plan_qty, production_plan_pieces=0):
	"""Planned quantity and pieces still standing behind this line."""
	released = get_work_order_release(sales_order_item)
	given = get_given_away(sales_order_item)
	return frappe._dict(
		qty=max(0.0, flt(production_plan_qty) - released.qty - given.qty),
		pieces=max(0.0, flt(production_plan_pieces) - released.pieces - given.pieces),
		released=released,
		given=given,
	)


def lines_with_planned_reductions(sales_order_items=None):
	"""SO line names that have a Work Order release or a transfer-out.

	Used to widen the Production Plan's SQL pre-filters, which only look at
	``qty > production_plan_qty`` / ``qty > work_order_qty``.
	"""
	params = {}
	cond_wo = cond_pt = ""
	if sales_order_items is not None:
		if not sales_order_items:
			return set()
		params["lines"] = tuple(sales_order_items)
		cond_wo = "and wo.sales_order_item in %(lines)s"
		cond_pt = "and pti.from_sales_order_item in %(lines)s"

	names = set(
		frappe.db.sql_list(
			f"""
			select distinct wo.sales_order_item from `tabWork Order` wo
			where ifnull(wo.sales_order_item, '') != ''
				and ifnull(wo.production_plan, '') != ''
				and ((wo.docstatus = 1 and wo.status = 'Closed') or wo.docstatus = 2)
				{cond_wo}
			""",
			params,
		)
	)
	if frappe.db.table_exists("Party Transfer Item"):
		names.update(
			frappe.db.sql_list(
				f"""
				select distinct pti.from_sales_order_item
				from `tabParty Transfer Item` pti
				inner join `tabParty Transfer` pt on pt.name = pti.parent
				where pt.docstatus = 1 and pti.source_type = 'Production'
					{cond_pt}
				""",
				params,
			)
		)
	return names
