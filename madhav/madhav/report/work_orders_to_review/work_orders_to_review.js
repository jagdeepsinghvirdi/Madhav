// Copyright (c) 2026, Finbyz pvt. ltd. and contributors
// For license information, please see license.txt

frappe.query_reports["Work Orders to Review"] = {
	filters: [
		{
			fieldname: "idle_days",
			label: __("No production for at least (days)"),
			fieldtype: "Int",
			default: 30,
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\nNot Started\nIn Process",
		},
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "sales_order", label: __("Sales Order"), fieldtype: "Link", options: "Sales Order" },
		{ fieldname: "production_item", label: __("Item"), fieldtype: "Link", options: "Item" },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "suggestion" && data && data.suggestion_level === "close") {
			value = `<span style="color: var(--red-600)">${value}</span>`;
		}
		return value;
	},
};
