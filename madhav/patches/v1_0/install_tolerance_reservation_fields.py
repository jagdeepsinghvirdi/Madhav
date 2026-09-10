import frappe


def execute():
	_clear_sbe_pieces_fetch_from()
	frappe.reload_doc(
		"madhav", "doctype", "staged_batch_reservations_verification", force=True
	)
	frappe.reload_doc("madhav", "doctype", "batch_wise_reservation_tool", force=True)

	if not frappe.db.has_column("Stock Reservation Entry", "custom_is_tolerance"):
		return

	tolerance_warehouse = frappe.db.get_single_value(
		"Stock Settings", "batch_reservation_tolerance_warehouse"
	)
	if not tolerance_warehouse:
		return

	frappe.db.sql(
		"""
		update `tabStock Reservation Entry`
		set custom_is_tolerance = 1
		where docstatus = 1
			and ifnull(custom_is_tolerance, 0) = 0
			and warehouse = %s
			and from_voucher_type = 'Batch Wise Reservation Tool'
		""",
		tolerance_warehouse,
	)


def _clear_sbe_pieces_fetch_from():
	name = "Serial and Batch Entry-pieces"
	if frappe.db.exists("Custom Field", name):
		frappe.db.set_value("Custom Field", name, "fetch_from", None)
