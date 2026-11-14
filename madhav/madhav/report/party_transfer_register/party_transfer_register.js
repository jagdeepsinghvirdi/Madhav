// Copyright (c) 2026, Finbyz pvt. ltd. and contributors
// For license information, please see license.txt

frappe.query_reports["Party Transfer Register"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{ fieldname: "customer", label: __("Customer (either side)"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "sales_order", label: __("Sales Order (either side)"), fieldtype: "Link", options: "Sales Order" },
		{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" },
		{ fieldname: "party_transfer", label: __("Party Transfer"), fieldtype: "Link", options: "Party Transfer" },
		{ fieldname: "include_cancelled", label: __("Include Cancelled"), fieldtype: "Check", default: 0 },
	],
};
