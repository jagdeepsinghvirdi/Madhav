import json

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import now


def execute():
	"""Show Sales Order Customer PO on the Production Plan Sales Orders grid."""
	create_custom_fields(
		{
			"Production Plan Sales Order": [
				{
					"fieldname": "po_no",
					"fieldtype": "Data",
					"label": "Customer PO",
					"insert_after": "customer_name",
					"fetch_from": "sales_order.po_no",
					"fetch_if_empty": 1,
					"read_only": 1,
					"in_list_view": 1,
					"columns": 2,
					"translatable": 0,
					"module": "Madhav",
				}
			]
		},
		update=True,
	)
	# Browser doctype cache is keyed on DocType.modified. A new child field
	# does not change that stamp, so the column picker keeps the old field list.
	for doctype in ("Production Plan", "Production Plan Sales Order"):
		frappe.db.set_value("DocType", doctype, "modified", now(), update_modified=False)

	_add_po_column_to_saved_grids()
	frappe.clear_cache(doctype="Production Plan")
	frappe.clear_cache(doctype="Production Plan Sales Order")


def _add_po_column_to_saved_grids():
	"""Saved grid layouts replace in_list_view, so add the column there too."""
	rows = frappe.db.sql(
		"""select user, data from `__UserSettings` where doctype=%s""",
		"Production Plan",
		as_dict=True,
	)
	column = {"fieldname": "po_no", "columns": 2}
	for row in rows:
		try:
			data = json.loads(row.data or "{}")
		except Exception:
			continue
		grid = (data.get("GridView") or {}).get("Production Plan Sales Order")
		if not grid or any(col.get("fieldname") == "po_no" for col in grid):
			continue
		insert_at = next(
			(i + 1 for i, col in enumerate(grid) if col.get("fieldname") == "customer_name"),
			len(grid),
		)
		grid.insert(insert_at, column)
		frappe.db.sql(
			"""update `__UserSettings` set data=%s where user=%s and doctype=%s""",
			(json.dumps(data), row.user, "Production Plan"),
		)
