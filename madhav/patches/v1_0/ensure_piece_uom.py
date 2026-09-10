import frappe


def execute():
	if frappe.db.exists("UOM", "Piece"):
		return

	frappe.get_doc(
		{
			"doctype": "UOM",
			"uom_name": "Piece",
			"must_be_whole_number": 1,
		}
	).insert(ignore_permissions=True)
