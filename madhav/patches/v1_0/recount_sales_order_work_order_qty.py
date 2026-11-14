# Copyright (c) 2026, Finbyz pvt. ltd. and contributors
# For license information, please see license.txt
"""Recount Sales Order Item.work_order_qty / work_order_pieces on every line.

Two causes left lines wrong (14 Nov 2026: 654 lines on production, 74 on open
orders):
- Before 29 Jul 2026 the Work Order recount summed every Work Order of the
  item on the whole Sales Order, not only the line's own (too high).
- Finish Work Order raised Work Order.qty to the mill weight directly in the
  database, skipping the recount (too low). Fixed in finish_work_order.py.

Rule (same as override/work_order.py): sum of the line's submitted Work
Orders, a Closed one counting only what it produced, sub-assembly Work Orders
left out. Product-bundle lines are skipped. Lines with no Work Order go to 0.
"""

import frappe
from frappe.utils import flt


def execute():
	from madhav.madhav.override.work_order import line_work_order_qty
	from madhav.doc_events.work_order import recompute_work_order_pieces

	lines = frappe.db.sql_list(
		"""
		select soi.name from `tabSales Order Item` soi
		where soi.docstatus = 1 and (
			ifnull(soi.work_order_qty, 0) != 0 or ifnull(soi.work_order_pieces, 0) != 0
			or exists (select 1 from `tabWork Order` wo
				where wo.sales_order_item = soi.name and wo.docstatus = 1)
		)
		"""
	)
	changed = 0
	for name in lines:
		qty = line_work_order_qty(name)
		if qty is None:
			continue
		stored = flt(frappe.db.get_value("Sales Order Item", name, "work_order_qty"), 3)
		if abs(stored - qty) > 0.0005:
			frappe.db.set_value("Sales Order Item", name, "work_order_qty", qty, update_modified=False)
			changed += 1
		recompute_work_order_pieces(name)
	print(f"recount_sales_order_work_order_qty: {changed} of {len(lines)} lines changed")
