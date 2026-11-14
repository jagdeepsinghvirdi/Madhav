# Copyright (c) 2026, Finbyz pvt. ltd. and contributors
# For license information, please see license.txt
"""One-time: Work Order Pending Qty = Qty - Produced Qty (never below 0).

Finish Work Order used to write "Qty - this row's ready qty", so a Work Order
finished in two rows kept only the last row (team, 14 Nov 2026; 2,048 Work
Orders on production). The code is fixed; this corrects existing records.
Pending Qty is a display field; nothing else is recalculated.
"""

import frappe


def execute():
	if not frappe.db.has_column("Work Order", "pending_qty"):
		return
	condition = """docstatus = 1
		and abs(ifnull(pending_qty, 0) - greatest(round(qty - ifnull(produced_qty, 0), 3), 0)) > 0.0005"""
	count = frappe.db.sql(f"select count(*) from `tabWork Order` where {condition}")[0][0]
	frappe.db.sql(
		f"""update `tabWork Order`
		set pending_qty = greatest(round(qty - ifnull(produced_qty, 0), 3), 0)
		where {condition}"""
	)
	print(f"work_order_pending_qty_recount: {count} Work Orders corrected")
