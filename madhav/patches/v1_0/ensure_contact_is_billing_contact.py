import frappe


def execute():
	if frappe.db.has_column("Contact", "is_billing_contact"):
		return

	if frappe.db.exists("Custom Field", "Contact-is_billing_contact"):
		return

	frappe.get_doc(
		{
			"doctype": "Custom Field",
			"dt": "Contact",
			"fieldname": "is_billing_contact",
			"label": "Is Billing Contact",
			"fieldtype": "Check",
			"insert_after": "is_primary_contact",
			"module": "Madhav",
		}
	).insert(ignore_permissions=True)
