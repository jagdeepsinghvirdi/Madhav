import frappe
from frappe import _
from erpnext.manufacturing.doctype.production_plan.production_plan import ProductionPlan as ERPNextProductionPlan
from pypika.terms import ExistsCriterion
from frappe.utils import (
	add_days,
	ceil,
	cint,
	comma_and,
	flt,
	get_link_to_form,
	getdate,
	now_datetime,
	nowdate,
)


INCHES_PER_METER = 39.37

def meters_to_inches(value):
	try:
		return float(value) * INCHES_PER_METER if value not in (None, "") else 0.0
	except Exception:
		return 0.0


def get_already_reserved_qty(sales_order_item):
	from madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool import (
		get_base_reserved_qty,
	)

	return flt(get_base_reserved_qty(sales_order_item))


def get_already_reserved_pieces(sales_order_item):
	rows = frappe.db.sql(
		"""
		select sbr.name, sbr.reserved_qty as staged_qty, sbr.reserved_pieces as staged_pieces,
			sre.reserved_qty as actual_qty
		from `tabStaged Batch Reservations Verification` sbr
		inner join `tabBatch Wise Reservation Tool` bwrt on bwrt.name = sbr.parent
		left join `tabStock Reservation Entry` sre
			on sre.from_voucher_type = 'Batch Wise Reservation Tool'
			and sre.from_voucher_detail_no = sbr.name
			and sre.docstatus = 1
			and ifnull(sre.custom_is_tolerance, 0) = 0
		where sbr.sales_order_item = %s and bwrt.docstatus = 1
			and ifnull(sbr.is_tolerance, 0) = 0
		""",
		sales_order_item,
		as_dict=True,
	)

	total_pieces = 0.0
	for r in rows:
		staged_qty = flt(r.staged_qty)
		actual_qty = flt(r.actual_qty)
		if staged_qty <= 0 or not actual_qty:
			continue
		total_pieces += flt(r.staged_pieces) * min(1, actual_qty / staged_qty)
	return total_pieces


class CustomProductionPlan(ERPNextProductionPlan):

	def validate(self):
		super().validate()
		self.validate_production_plan()

	def validate_production_plan(self):
		for row in self.get("po_items"):  # check correct child table name
			if isinstance(row.length, str):
				row.length = row.length.strip()
			if row.pieces:
				row.pieces = int(row.pieces)

	@frappe.whitelist()
	def get_items(self):
		self.set("po_items", [])
		if self.get_items_from == "Sales Order":
			self.custom_get_so_items()
		elif self.get_items_from == "Material Request":
			self.get_mr_items()

	@frappe.whitelist()
	def get_open_sales_orders(self):
		"""Pull sales orders which are pending to deliver based on criteria selected"""
		open_so = custom_get_sales_orders(self)
		if open_so:
			self.add_so_in_table(open_so)
		else:
			frappe.msgprint(_("Sales orders are not available for production"))

	def custom_get_so_items(self):
	# Check for empty table or empty rows
		if not self.get("sales_orders") or not self.get_so_mr_list(
			"sales_order", "sales_orders"
		):
			frappe.throw(
				_("Please fill the Sales Orders table"),
				title=_("Sales Orders Required"),
			)

		so_list = self.get_so_mr_list("sales_order", "sales_orders")

		bom = frappe.qb.DocType("BOM")
		so_item = frappe.qb.DocType("Sales Order Item")

		items_subquery = (
			frappe.qb.from_(bom)
			.select(bom.name)
			.where(bom.is_active == 1)
		)

		item = frappe.qb.DocType("Item")

		items_query = (
			frappe.qb.from_(so_item)
			.select(
				so_item.parent,
				so_item.item_code,
				so_item.warehouse,
				so_item.remarks,
				so_item.assorted_length,
				so_item.qty,
				so_item.production_plan_pieces,
				so_item.work_order_qty,
				so_item.delivered_qty,
				so_item.conversion_factor,
				so_item.description,
				so_item.name,
				so_item.bom_no,
			)
			.distinct()
			.where(
				(so_item.parent.isin(so_list))
				& (so_item.docstatus == 1)
				& (so_item.is_manufacture == 1)
				& (so_item.qty > so_item.work_order_qty)
			)
		)

		if self.item_name:
			items_query = items_query.where(
				so_item.item_name.like(f"%{self.item_name}%")
			)

		if self.item_code and frappe.db.exists("Item", self.item_code):
			items_query = items_query.where(
				so_item.item_code == self.item_code
			)
			items_subquery = items_subquery.where(
				self.get_bom_item_condition()
				or bom.item == so_item.item_code
			)

		items_query = items_query.where(
			ExistsCriterion(items_subquery)
		)

		items = items_query.run(as_dict=True)

		for item in items:
			item.pending_qty = (
				flt(item.qty)
				- max(item.work_order_qty, item.delivered_qty, 0)
			) * item.conversion_factor

		pi = frappe.qb.DocType("Packed Item")

		pending_qty = (
			frappe.qb.terms.Case()
			.when(
				(so_item.work_order_qty > so_item.delivered_qty),
				(
					(
						(so_item.qty - so_item.work_order_qty) * pi.qty
					)
					/ so_item.qty
				),
			)
			.else_(
				(
					(
						(so_item.qty - so_item.delivered_qty) * pi.qty
					)
					/ so_item.qty
				)
			)
		)

		packed_items_query = (
			frappe.qb.from_(so_item)
			.from_(pi)
			.select(
				pi.parent,
				pi.item_code,
				pi.warehouse.as_("warehouse"),
				pending_qty.as_("pending_qty"),
				pi.parent_item,
				pi.description,
				so_item.name,
			)
			.distinct()
			.where(
				(so_item.parent == pi.parent)
				& (so_item.docstatus == 1)
				& (pi.parent_item == so_item.item_code)
				& (so_item.parent.isin(so_list))
				& (
					(
						(so_item.work_order_qty > so_item.delivered_qty)
						& (so_item.qty > so_item.work_order_qty)
					)
					| (
						(so_item.work_order_qty <= so_item.delivered_qty)
						& (so_item.qty > so_item.delivered_qty)
					)
				)
				& (
					ExistsCriterion(
						frappe.qb.from_(bom)
						.select(bom.name)
						.where(
							(bom.item == pi.item_code)
							& (bom.is_active == 1)
						)
					)
				)
			)
		)

		if self.item_code:
			packed_items_query = packed_items_query.where(
				so_item.item_code == self.item_code
			)

		packed_items = packed_items_query.run(as_dict=True)
		self.add_items(items + packed_items)

		for row in self.po_items:
			if row.sales_order_item:
				so_item_row = frappe.db.get_value(
					"Sales Order Item",
					row.sales_order_item,
					[
						"pieces",
						"remarks",
						"production_plan_pieces",
						"length_size",
						"assorted_length",
					],
					as_dict=True,
				)

				if so_item_row:
					row.pending_pieces = so_item_row.production_plan_pieces or 0

					already_reserved_pieces = get_already_reserved_pieces(row.sales_order_item)
					row.pieces = max(
						0,
						flt(so_item_row.pieces or 0)
						- flt(so_item_row.production_plan_pieces or 0)
						- already_reserved_pieces,
					)

					row.assorted_length = so_item_row.assorted_length
					row.remark = so_item_row.remarks

					row.section_weight = frappe.db.get_value(
						"Item", row.item_code, "weight_per_meter"
					)
					if so_item_row.length_size:
						row.length = meters_to_inches(so_item_row.length_size)
						row.length_size_m = so_item_row.length_size or 0.0

					already_reserved_qty = get_already_reserved_qty(row.sales_order_item)
					row.planned_qty = max(0, flt(row.planned_qty or 0) - already_reserved_qty)

			if row.sales_order:
				so_details = frappe.db.get_value(
					"Sales Order",
					row.sales_order,
					["customer", "customer_name"],
					as_dict=True,
				)
				if so_details:
					row.customer = so_details.customer or ""
					row.customer_name = so_details.customer_name or ""

		# Drop lines fully covered by existing reservations (both qty
		# and pieces) instead of leaving a 0-value row in the plan.
		# Filter by name (unique per row, even before save) and rebuild
		# via self.set() rather than list.remove() in a loop, which is
		# safer given how many rows can share identical field values
		# (e.g. many unlinked rows with item_code/qty/None sales_order
		# all equal) - self.set() also re-indexes idx correctly.
		fully_reserved_names = {
			row.name for row in self.po_items
			if flt(row.planned_qty or 0) <= 0 and flt(row.pieces or 0) <= 0
		}
		if fully_reserved_names:
			self.set("po_items", [
				row for row in self.po_items if row.name not in fully_reserved_names
			])

		self.calculate_total_planned_qty()

def custom_get_sales_orders(self):
	bom = frappe.qb.DocType("BOM")
	pi = frappe.qb.DocType("Packed Item")
	so = frappe.qb.DocType("Sales Order")
	so_item = frappe.qb.DocType("Sales Order Item")

	open_so_subquery1 = frappe.qb.from_(bom).select(bom.name).where(bom.is_active == 1)

	open_so_subquery2 = (
		frappe.qb.from_(pi)
		.select(pi.name)
		.where(
			(pi.parent == so.name)
			& (pi.parent_item == so_item.item_code)
			& (
				ExistsCriterion(
					frappe.qb.from_(bom)
					.select(bom.name)
					.where((bom.item == pi.item_code) & (bom.is_active == 1))
				)
			)
		)
	)

	item = frappe.qb.DocType("Item")

	open_so_query = (
		frappe.qb.from_(so)
		.join(so_item).on(so_item.parent == so.name)
		.select(
			so.name,
			so.transaction_date,
			so.customer,
			so.base_grand_total
		)
		.distinct()
		.where(
			(so.docstatus == 1)
			& (so.status.notin(["Stopped", "Closed"]))
			& (so.company == self.company)
			& (so_item.qty > so_item.production_plan_qty)
		)
	)

	if self.item_name:
		open_so_query = open_so_query.where(
			so_item.item_name.like(f"%{self.item_name}%")
		)

	date_field_mapper = {
		"from_date": so.transaction_date >= self.from_date,
		"to_date": so.transaction_date <= self.to_date,
		"from_delivery_date": so_item.delivery_date >= self.from_delivery_date,
		"to_delivery_date": so_item.delivery_date <= self.to_delivery_date,
	}

	for field, value in date_field_mapper.items():
		if self.get(field):
			open_so_query = open_so_query.where(value)

	for field in ("customer", "project", "sales_order_status"):
		if self.get(field):
			so_field = "status" if field == "sales_order_status" else field
			open_so_query = open_so_query.where(so[so_field] == self.get(field))

	if self.item_code and frappe.db.exists("Item", self.item_code):
		open_so_query = open_so_query.where(so_item.item_code == self.item_code)
		open_so_subquery1 = open_so_subquery1.where(
			self.get_bom_item_condition() or bom.item == so_item.item_code
		)

	open_so_query = open_so_query.where(
		ExistsCriterion(open_so_subquery1) | ExistsCriterion(open_so_subquery2)
	)

	open_so = open_so_query.run(as_dict=True)

	return open_so