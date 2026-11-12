# Copyright (c) 2026, Finbyz pvt. ltd. and contributors
# For license information, please see license.txt
"""Every reservation moved between orders, row by row."""

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Party Transfer"), "fieldname": "party_transfer", "fieldtype": "Link", "options": "Party Transfer", "width": 130},
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 95},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 85},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 100},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 160},
		{"label": _("Batch"), "fieldname": "batch_no", "fieldtype": "Link", "options": "Batch", "width": 110},
		# Data, not Float: a length must not be added up in the Total row.
		{"label": _("Length"), "fieldname": "length", "fieldtype": "Data", "width": 70},
		{"label": _("Qty"), "fieldname": "transfer_qty", "fieldtype": "Float", "precision": 3, "width": 85},
		{"label": _("Pieces"), "fieldname": "transfer_pieces", "fieldtype": "Int", "width": 70},
		{"label": _("From Order"), "fieldname": "from_sales_order", "fieldtype": "Link", "options": "Sales Order", "width": 125},
		{"label": _("From Customer"), "fieldname": "from_customer_name", "fieldtype": "Data", "width": 170},
		{"label": _("To Order"), "fieldname": "to_sales_order", "fieldtype": "Link", "options": "Sales Order", "width": 125},
		{"label": _("To Customer"), "fieldname": "to_customer_name", "fieldtype": "Data", "width": 170},
		{"label": _("Source"), "fieldname": "source_type", "fieldtype": "Data", "width": 85},
		{"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 150},
		{"label": _("New Reservation"), "fieldname": "new_stock_reservation_entry", "fieldtype": "Link", "options": "Stock Reservation Entry", "width": 150},
		{"label": _("Done By"), "fieldname": "owner", "fieldtype": "Link", "options": "User", "width": 160},
		{"label": _("Remarks"), "fieldname": "remarks", "fieldtype": "Data", "width": 160},
	]


def get_data(filters):
	conditions = ["pt.docstatus in (1, 2)" if filters.include_cancelled else "pt.docstatus = 1"]
	values = {}
	if filters.from_date:
		conditions.append("pt.posting_date >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.to_date:
		conditions.append("pt.posting_date <= %(to_date)s")
		values["to_date"] = filters.to_date
	if filters.customer:
		conditions.append("(pt.to_customer = %(customer)s or pti.from_customer = %(customer)s)")
		values["customer"] = filters.customer
	if filters.sales_order:
		conditions.append("(pt.to_sales_order = %(sales_order)s or pti.from_sales_order = %(sales_order)s)")
		values["sales_order"] = filters.sales_order
	if filters.item_code:
		conditions.append("pt.item_code = %(item_code)s")
		values["item_code"] = filters.item_code
	if filters.party_transfer:
		conditions.append("pt.name = %(party_transfer)s")
		values["party_transfer"] = filters.party_transfer

	return frappe.db.sql(
		f"""
		select pt.name as party_transfer, pt.posting_date,
			if(pt.docstatus = 2, 'Cancelled', 'Submitted') as status,
			pt.item_code, pt.item_name, pti.batch_no, cast(round(pti.length, 2) as char) as length,
			pti.transfer_qty, pti.transfer_pieces,
			pti.from_sales_order, pti.from_customer_name,
			pt.to_sales_order, pt.to_customer_name,
			pti.source_type, pti.warehouse, pti.new_stock_reservation_entry,
			pt.owner, pt.remarks
		from `tabParty Transfer` pt
		inner join `tabParty Transfer Item` pti on pti.parent = pt.name
		where {" and ".join(conditions)}
		order by pt.posting_date desc, pt.name desc, pti.idx asc
		""",
		values,
		as_dict=True,
	)
