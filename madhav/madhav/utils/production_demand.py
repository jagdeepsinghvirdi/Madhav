# Copyright (c) 2026, Finbyz pvt. ltd. and contributors
# For license information, please see license.txt
"""What part of a Sales Order line's planned production is still alive.

``Sales Order Item.production_plan_qty`` (and Madhav's
``production_plan_pieces``) only ever grow when a Production Plan is
submitted. Two things make part of that planned quantity disappear
without touching the plan itself:

* **Released by a Work Order.**
  - Closed Work Order: the part it never produced (qty - produced).
  - Cancelled Work Order: its quantity, capped by what its plan row has not
    ordered again. A plan row that released quantity this way cannot make a
    new Work Order for it (see override/work_order.py), so the quantity can
    only be planned again on a new Production Plan.
* **Given away by a Party Transfer** of production stock (Finish Work Order /
  Stock Transfer reservations) to another order - but only the part that
  takes the line's production below its plan. Over-production that was
  reserved beyond the plan and then given away was never planned, so it
  does not open anything.

Effective planned = planned - released - given away. The Production Plan
remaining-quantity rule uses the effective figure. Nothing is stored: every
figure is read from Work Orders, plan rows, reservations and transfers, so
cancelling a transfer corrects it automatically.

Performance: inside Production Plan Get Sales Orders / Get Items the caller
sets ``frappe.flags.pp_reduced_lines`` (lines that have any release or
transfer) and ``frappe.flags.pp_demand_cache``; lines outside the set skip
every extra query.
"""

import frappe
from frappe.utils import cint, flt

# Reservations that come from stock not produced for that order line.
# The Production Plan rule subtracts them on top of the planned quantity.
FREE_STOCK_SOURCES = ("Batch Wise Reservation Tool", "Party Transfer")

# Reservations that are the output of the line's own Work Orders.
PRODUCTION_SOURCES = ("Finish Work Order", "Stock Transfer")

QTY_EPSILON = 0.0005


def free_stock_sources_sql():
	"""SQL tuple literal for FREE_STOCK_SOURCES."""
	return "(" + ", ".join(frappe.db.escape(s) for s in FREE_STOCK_SOURCES) + ")"


def is_free_stock_source(from_voucher_type):
	return (from_voucher_type or "") in FREE_STOCK_SOURCES


def source_type_for(from_voucher_type):
	"""Production (made for that line), Spare (free stock) or Other (manual etc.)."""
	if (from_voucher_type or "") in PRODUCTION_SOURCES:
		return "Production"
	if is_free_stock_source(from_voucher_type):
		return "Spare"
	return "Other"


def _combined_plans():
	"""Production Plans that combine items across Sales Orders (not used by Madhav).

	Their Work Orders serve several lines, so a release cannot be pinned to
	one line; they are left out of the release figures.
	"""
	return set(
		frappe.db.sql_list(
			"select distinct parent from `tabProduction Plan Item Reference`"
		)
	)


def get_work_order_release(sales_order_item):
	"""Quantity and pieces of planned production that Work Orders gave back."""
	out = frappe._dict(qty=0.0, pieces=0.0, closed_qty=0.0, cancelled_qty=0.0,
		closed_pieces=0.0, cancelled_pieces=0.0)
	if not sales_order_item:
		return out
	combined = _combined_plans()

	# Closed: only what was never produced comes back.
	closed = frappe.db.sql(
		"""
		select wo.production_plan, wo.qty, wo.produced_qty, ifnull(wo.pieces, 0) as pieces,
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
		if wo.production_plan in combined:
			continue
		out.closed_qty += max(0.0, flt(wo.qty) - flt(wo.produced_qty))
		if flt(wo.pieces) > 0:
			out.closed_pieces += max(0.0, flt(wo.pieces) - flt(wo.completed_pcs))

	# Cancelled: per plan row, capped by what that row has not re-ordered.
	cancelled = frappe.db.sql(
		"""
		select wo.production_plan as pp, wo.production_plan_item as ppi,
			sum(wo.qty) as qty, sum(ifnull(wo.pieces, 0)) as pieces
		from `tabWork Order` wo
		inner join `tabProduction Plan` pp on pp.name = wo.production_plan
		where wo.sales_order_item = %s
			and wo.docstatus = 2
			and pp.docstatus = 1
			and ifnull(wo.production_plan_item, '') != ''
			and ifnull(wo.production_plan_sub_assembly_item, '') = ''
		group by wo.production_plan, wo.production_plan_item
		""",
		sales_order_item,
		as_dict=True,
	)
	for row in cancelled:
		if row.pp in combined:
			continue
		rel = get_plan_row_cancel_release(row.ppi, cancelled_qty=row.qty, cancelled_pieces=row.pieces)
		out.cancelled_qty += rel.qty
		out.cancelled_pieces += rel.pieces

	out.qty = out.closed_qty + out.cancelled_qty
	out.pieces = out.closed_pieces + out.cancelled_pieces
	return out


def get_plan_row_cancel_release(production_plan_item, cancelled_qty=None, cancelled_pieces=None, exclude_work_order=None):
	"""What a plan row gave back through cancelled Work Orders.

	min(cancelled, planned - still ordered). ``exclude_work_order`` leaves a
	Work Order being validated out of "still ordered".
	"""
	out = frappe._dict(qty=0.0, pieces=0.0)
	ppi = frappe.db.get_value(
		"Production Plan Item", production_plan_item, ["planned_qty", "pieces"], as_dict=True
	)
	if not ppi:
		return out
	if cancelled_qty is None:
		cancelled_qty, cancelled_pieces = frappe.db.sql(
			"""
			select ifnull(sum(qty), 0), ifnull(sum(ifnull(pieces, 0)), 0) from `tabWork Order`
			where production_plan_item = %s and docstatus = 2
			""",
			production_plan_item,
		)[0]
	ordered_qty, ordered_pieces = frappe.db.sql(
		"""
		select ifnull(sum(qty), 0), ifnull(sum(ifnull(pieces, 0)), 0) from `tabWork Order`
		where production_plan_item = %s and docstatus = 1 and name != %s
		""",
		(production_plan_item, exclude_work_order or ""),
	)[0]
	out.qty = min(flt(cancelled_qty), max(0.0, flt(ppi.planned_qty) - flt(ordered_qty)))
	if flt(ppi.pieces) > 0 and flt(cancelled_pieces) > 0:
		out.pieces = min(flt(cancelled_pieces), max(0.0, flt(ppi.pieces) - flt(ordered_pieces)))
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


def get_production_coverage(sales_order_item):
	"""Production reservations still held by the line, plus what they delivered."""
	row = frappe.db.sql(
		f"""
		select ifnull(sum(reserved_qty - delivered_qty), 0), ifnull(sum(delivered_qty), 0)
		from `tabStock Reservation Entry`
		where voucher_type = 'Sales Order' and voucher_detail_no = %s and docstatus = 1
			and from_voucher_type in ({", ".join(frappe.db.escape(s) for s in PRODUCTION_SOURCES)})
		""",
		sales_order_item,
	)[0]
	return flt(row[0]), flt(row[1])


def effective_given(given, planned_after_release, production_open, production_delivered):
	"""Part of the given-away production that takes the line below its plan.

	Production held or delivered + given away = what was produced for the line
	and reserved. Anything above the (released-adjusted) plan was surplus:
	giving surplus away opens nothing.

	>>> effective_given(5, 100, 100, 0)        # 105 made, 5 surplus given
	0
	>>> effective_given(20, 100, 80, 0)        # 100 made, 20 given
	20
	"""
	given = flt(given)
	if given <= 0:
		return 0.0
	surplus = max(0.0, flt(production_open) + flt(production_delivered) + given - flt(planned_after_release))
	return max(0.0, given - surplus)


def get_effective_planned(sales_order_item, production_plan_qty=None, production_plan_pieces=None):
	"""Planned quantity and pieces still standing behind this line."""
	cache = frappe.flags.get("pp_demand_cache")
	if cache is not None and sales_order_item in cache:
		return cache[sales_order_item]

	if production_plan_qty is None or production_plan_pieces is None:
		vals = frappe.db.get_value(
			"Sales Order Item", sales_order_item, ["production_plan_qty", "production_plan_pieces"], as_dict=True
		) or frappe._dict()
		if production_plan_qty is None:
			production_plan_qty = vals.get("production_plan_qty")
		if production_plan_pieces is None:
			production_plan_pieces = vals.get("production_plan_pieces")
	planned_qty, planned_pieces = flt(production_plan_qty), flt(production_plan_pieces)

	reduced = frappe.flags.get("pp_reduced_lines")
	if reduced is not None and sales_order_item not in reduced:
		out = frappe._dict(qty=planned_qty, pieces=planned_pieces, released=frappe._dict(qty=0.0, pieces=0.0),
			given=frappe._dict(qty=0.0, pieces=0.0))
	else:
		released = get_work_order_release(sales_order_item)
		given_raw = get_given_away(sales_order_item)
		given = frappe._dict(qty=0.0, pieces=0.0)
		if given_raw.qty > QTY_EPSILON:
			prod_open, prod_delivered = get_production_coverage(sales_order_item)
			given.qty = effective_given(given_raw.qty, planned_qty - released.qty, prod_open, prod_delivered)
			if given_raw.pieces:
				given.pieces = given_raw.pieces * (given.qty / given_raw.qty)
		out = frappe._dict(
			qty=max(0.0, flt(planned_qty - released.qty - given.qty, 6)),
			pieces=max(0.0, flt(planned_pieces - released.pieces - given.pieces, 6)),
			released=released,
			given=given,
		)
	if cache is not None:
		cache[sales_order_item] = out
	return out


def lines_with_planned_reductions(sales_order_items=None, open_orders_only=False):
	"""SO line names that have a Work Order release or a production transfer-out.

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
	join_open_wo = join_open_pt = ""
	if open_orders_only:
		join_open_wo = """inner join `tabSales Order` so on so.name = wo.sales_order
			and so.docstatus = 1 and so.status not in ('Closed', 'Completed', 'Cancelled')"""
		join_open_pt = """inner join `tabSales Order` so on so.name = pti.from_sales_order
			and so.docstatus = 1 and so.status not in ('Closed', 'Completed', 'Cancelled')"""

	names = set(
		frappe.db.sql_list(
			f"""
			select distinct wo.sales_order_item from `tabWork Order` wo
			{join_open_wo}
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
				{join_open_pt}
				where pt.docstatus = 1 and pti.source_type = 'Production'
					{cond_pt}
				""",
				params,
			)
		)
	return names
