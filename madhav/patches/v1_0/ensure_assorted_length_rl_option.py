import frappe


def execute():
	"""Add the R/L option to Assorted Length on Delivery Note Item and Sales
	Invoice Item.

	The Delivery Note now takes Assorted Length from the reserved batch, and
	batches can be R/L (Stock Reconciliation Item already allows it). Without
	the option, a DN or the Sales Invoice made from it fails Select validation.
	The fields were created on site (not in fixtures), so patch them in place.
	Only appends the missing option - existing options and values are untouched.
	"""
	for dt in ("Delivery Note Item", "Sales Invoice Item"):
		name = frappe.db.get_value("Custom Field", {"dt": dt, "fieldname": "assorted_length"})
		if not name:
			continue
		cf = frappe.get_doc("Custom Field", name)
		if cf.fieldtype != "Select":
			continue
		options = (cf.options or "").split("\n")
		if "R/L" in options:
			continue
		options.append("R/L")
		cf.options = "\n".join(options)
		cf.save(ignore_permissions=True)
		frappe.clear_cache(doctype=dt)
