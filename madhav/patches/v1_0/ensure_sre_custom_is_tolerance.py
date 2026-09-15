import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	"""Ensure Stock Reservation Entry.custom_is_tolerance exists in DB.

	Earlier sites can miss this column when fixtures/custom fields were not
	synced, which breaks BWRT get_reserved_batches and tolerance SQL.
	"""
	if frappe.db.has_column("Stock Reservation Entry", "custom_is_tolerance"):
		return

	create_custom_fields(
		{
			"Stock Reservation Entry": [
				{
					"fieldname": "custom_is_tolerance",
					"fieldtype": "Check",
					"label": "Is Tolerance Reservation",
					"insert_after": "reserved_qty",
					"read_only": 1,
					"no_copy": 1,
					"print_hide": 1,
					"default": "0",
				}
			]
		},
		update=True,
	)
