# Copyright (c) 2026, Finbyz pvt. ltd. and contributors
# For license information, please see license.txt
"""Index Work Order.sales_order_item and recount Work Order qty / pieces on
Sales Order lines that have a Closed Work Order.

The planning rule now reads Work Orders per Sales Order line, and a Closed
Work Order now keeps counting what it produced (it used to drop out
entirely). Lines with no Closed Work Order are unchanged.
"""

import frappe
from frappe.utils import flt


def execute():
	frappe.db.add_index("Work Order", ["sales_order_item"])

	from madhav.doc_events.work_order import recompute_work_order_pieces

	rows = frappe.db.sql(
		"""
		select distinct sales_order, sales_order_item, production_item, product_bundle_item
		from `tabWork Order`
		where docstatus = 1 and status = 'Closed' and ifnull(sales_order_item, '') != ''
		""",
		as_dict=True,
	)
	for r in rows:
		qty = frappe.db.sql(
			"""
			select ifnull(sum(case when status = 'Closed' then least(produced_qty, qty) else qty end), 0)
			from `tabWork Order`
			where sales_order = %s and sales_order_item = %s and docstatus = 1 and production_item = %s
			""",
			(r.sales_order, r.sales_order_item, r.production_item),
		)[0][0]
		if not r.product_bundle_item:
			frappe.db.set_value("Sales Order Item", r.sales_order_item, "work_order_qty", flt(qty, 3), update_modified=False)
		recompute_work_order_pieces(r.sales_order_item)
