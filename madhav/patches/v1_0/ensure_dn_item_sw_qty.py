from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	"""Ensure Delivery Note Item.sw_qty (SW Qty) exists.

	SW Qty = Pcs × Length Size × Section Weight / 1000, display only. The
	Delivery Note Qty went back to the reserved weight (client Nov 2026), and
	the formula value moved to this column. Kept in fixtures too.
	"""
	create_custom_fields(
		{
			"Delivery Note Item": [
				{
					"fieldname": "sw_qty",
					"label": "SW Qty",
					"fieldtype": "Float",
					"insert_after": "invoice_qty",
					"read_only": 1,
					"in_list_view": 1,
					"precision": "3",
					"description": (
						"Pcs × Length Size × Section Weight / 1000. "
						"Display only - Qty stays the reserved weight."
					),
					"module": "Madhav",
				}
			]
		},
		update=True,
	)
