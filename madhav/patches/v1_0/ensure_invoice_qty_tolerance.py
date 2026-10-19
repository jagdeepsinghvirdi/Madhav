import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import flt


def execute():
	"""Ensure Stock Settings.madhav_invoice_qty_tolerance exists and defaults to 3."""
	create_custom_fields(
		{
			"Stock Settings": [
				{
					"fieldname": "madhav_invoice_qty_tolerance",
					"label": "Invoice Qty Tolerance %",
					"fieldtype": "Float",
					"insert_after": "batch_reservation_tolerance_warehouse",
					"default": "3",
					"non_negative": 1,
					"precision": "2",
					"description": (
						"Delivery Note Deliver-as-Qty: stop Submit when "
						"|Invoice Qty - Qty| / Qty x 100 is above this percent. Blank uses 3."
					),
					"module": "Madhav",
				}
			]
		},
		update=True,
	)
	current = frappe.db.get_single_value("Stock Settings", "madhav_invoice_qty_tolerance")
	if current in (None, "") or flt(current) == 0:
		frappe.db.set_single_value("Stock Settings", "madhav_invoice_qty_tolerance", 3)
