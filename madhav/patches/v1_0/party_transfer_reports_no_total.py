# Copyright (c) 2026, Finbyz pvt. ltd. and contributors
# For license information, please see license.txt
"""Party Transfer Register / Work Orders to Review: no Total row.

Frappe keeps a standard report's add_total_row when the app is updated, so
sites that installed the reports with a Total row are set here directly.
"""

import frappe


def execute():
	for report in ("Party Transfer Register", "Work Orders to Review"):
		if frappe.db.exists("Report", report):
			frappe.db.set_value("Report", report, "add_total_row", 0, update_modified=False)
