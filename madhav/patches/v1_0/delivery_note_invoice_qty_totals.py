# Copyright (c) 2026, Finbyz pvt. ltd. and contributors
# For license information, please see license.txt
"""One-time: fill Total Invoice Qty and Total (INR) as per Invoice Qty on
submitted Delivery Notes made before these fields existed (team, 14 Nov 2026).

Same rule as set_invoice_qty_totals(): Invoice Qty on Deliver-as-Qty rows,
otherwise Qty, times the row's rate in INR (base_rate). Only blank notes are
filled; no other field changes.
"""

import frappe


def execute():
	if not frappe.db.has_column("Delivery Note", "total_invoice_qty"):
		# On a first deploy the fixtures (custom fields) are synced after the
		# patches; create the two fields now.
		from frappe.utils.fixtures import sync_fixtures

		sync_fixtures("madhav")
		frappe.clear_cache(doctype="Delivery Note")
	if not (
		frappe.db.has_column("Delivery Note", "total_invoice_qty")
		and frappe.db.has_column("Delivery Note", "total_as_per_invoice_qty")
	):
		return

	basis = """(case when ifnull(dni.custom_deliver_as_qty, 0) = 1 and ifnull(dni.invoice_qty, 0) > 0
		then dni.invoice_qty else dni.qty end)"""
	count = frappe.db.sql(
		"""select count(*) from `tabDelivery Note`
		where docstatus = 1 and ifnull(total_invoice_qty, 0) = 0 and ifnull(total_as_per_invoice_qty, 0) = 0"""
	)[0][0]
	frappe.db.sql(
		f"""
		update `tabDelivery Note` dn
		join (
			select dni.parent,
				round(sum({basis}), 3) as total_qty,
				round(sum({basis} * ifnull(dni.base_rate, 0)), 2) as total_amount
			from `tabDelivery Note Item` dni
			where dni.parenttype = 'Delivery Note' and dni.docstatus = 1
			group by dni.parent
		) t on t.parent = dn.name
		set dn.total_invoice_qty = t.total_qty, dn.total_as_per_invoice_qty = t.total_amount
		where dn.docstatus = 1 and ifnull(dn.total_invoice_qty, 0) = 0
			and ifnull(dn.total_as_per_invoice_qty, 0) = 0
		"""
	)
	print(f"delivery_note_invoice_qty_totals: {count} submitted Delivery Notes filled")
