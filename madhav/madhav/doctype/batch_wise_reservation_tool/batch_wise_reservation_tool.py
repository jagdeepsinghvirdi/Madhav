# Copyright (c) 2026, Finbyz pvt. ltd. and contributors
# For license information, please see license.txt

import frappe
import json
import math
from frappe.model.document import Document
from frappe.utils import flt, cint, nowdate


def floor_qty(value, precision=3):
	factor = 10 ** precision
	return math.floor((flt(value) * factor) + 1e-6) / factor


# ---------------------------------------------------------------
# Shared calculation core (Phase 1). Every entry point - fetch,
# stage, submit - goes through get_reservation_ceiling() so the
# same numbers are used everywhere. is_tolerance is an EXPLICIT
# flag passed in, never inferred from warehouse (Phase 2).
# ---------------------------------------------------------------

def get_tolerance_warehouse(throw=True):
	warehouse = frappe.db.get_single_value("Stock Settings", "batch_reservation_tolerance_warehouse")
	if not warehouse and throw:
		frappe.throw(
			frappe._(
				"Please configure the Batch Reservation Tolerance Warehouse in Stock Settings "
				"before reserving tolerance quantity."
			)
		)
	return warehouse


def get_so_total_qty(sales_order):
	"""SO-wide qty in STOCK UOM (stock_qty already = qty * conversion_factor)."""
	return flt(
		frappe.db.sql(
			"select sum(stock_qty) from `tabSales Order Item` where parent=%s and docstatus=1",
			sales_order,
		)[0][0]
		or 0
	)


def get_tolerance_pool(sales_order):
	total_so_qty = get_so_total_qty(sales_order)
	tolerance_pct = flt(frappe.db.get_single_value("Stock Settings", "over_reservation_allowance") or 0)
	return flt(total_so_qty * tolerance_pct / 100)


def get_used_tolerance_qty(sales_order, exclude_sre=None):
	"""SO-wide tolerance already used, identified by custom_is_tolerance flag
	(NOT warehouse). Delivered tolerance SREs still count as used - the
	tolerance was already consumed, so we do NOT net delivered_qty here."""
	return flt(
		frappe.db.sql(
			"""
			select sum(reserved_qty) from `tabStock Reservation Entry`
			where voucher_type='Sales Order' and voucher_no=%(so)s
				and docstatus=1
				and ifnull(custom_is_tolerance,0)=1
				and (%(exclude)s is null or name != %(exclude)s)
			""",
			{"so": sales_order, "exclude": exclude_sre},
		)[0][0]
		or 0
	)


def get_base_reserved_qty(sales_order_item, exclude_sre=None):
	"""Base (non-tolerance) qty already reserved against this SO line,
	from ANY source, netted against each SRE's own delivered_qty."""
	return flt(
		frappe.db.sql(
			"""
			select sum(reserved_qty - delivered_qty) from `tabStock Reservation Entry`
			where voucher_type='Sales Order' and voucher_detail_no=%(sod)s
				and docstatus=1
				and ifnull(custom_is_tolerance,0)=0
				and (%(exclude)s is null or name != %(exclude)s)
			""",
			{"sod": sales_order_item, "exclude": exclude_sre},
		)[0][0]
		or 0
	)


def get_batch_available_qty(item_code, warehouse, batch_no, exclude_sre=None):
	"""Batch stock, filtered by warehouse on BOTH the actual-qty side and
	the reserved-qty side (Phase 1, point 4 - previously only the actual
	side was warehouse-filtered in some callers)."""
	actual_qty = flt(
		frappe.db.sql(
			"""
			select sum(sle.actual_qty)
			from `tabStock Ledger Entry` sle
			inner join `tabSerial and Batch Entry` sbe on sbe.parent = sle.serial_and_batch_bundle
			where sle.item_code=%(item_code)s and sle.warehouse=%(warehouse)s
				and sle.is_cancelled=0 and sbe.batch_no=%(batch_no)s
			""",
			{"item_code": item_code, "warehouse": warehouse, "batch_no": batch_no},
		)[0][0]
		or 0
	)

	reserved_qty = flt(
		frappe.db.sql(
			"""
			select sum(sbe.qty - ifnull(sbe.delivered_qty, 0))
			from `tabStock Reservation Entry` sre
			inner join `tabSerial and Batch Entry` sbe on sbe.parent = sre.name
			where sre.docstatus=1
				and sre.status in ('Reserved','Partially Reserved','Partially Delivered')
				and sre.item_code=%(item_code)s and sre.warehouse=%(warehouse)s
				and sbe.batch_no=%(batch_no)s
				and (%(exclude)s is null or sre.name != %(exclude)s)
			""",
			{"item_code": item_code, "warehouse": warehouse, "batch_no": batch_no, "exclude": exclude_sre},
		)[0][0]
		or 0
	)

	return max(0, floor_qty(actual_qty - reserved_qty, 3))


def calc_proportional_pieces(reserve_qty, batch_no):
	"""Whole pieces for the quantity actually reserved.

	``Batch.pieces`` and ``Batch.batch_qty`` are mutable master values and
	cannot be used as a ratio for a partial reservation.  The reservation
	row must instead use the physical batch dimensions.
	"""
	from madhav.madhav.utils.stock_piece_utils import int_pieces_from_qty

	batch = frappe.db.get_value(
		"Batch", batch_no, ["average_length", "section_weight"], as_dict=True
	)
	if not batch:
		return 0
	return int_pieces_from_qty(
		reserve_qty, batch.average_length, batch.section_weight
	)


def get_reservation_ceiling(
	item_code, warehouse, sales_order, sales_order_item, is_tolerance,
	batch_no=None, exclude_sre=None, already_staged_qty=0, already_staged_tolerance_qty=0,
):
	"""Single source of truth: min(SO-line availability, batch+warehouse
	availability, item+warehouse availability). Used by fetch, stage,
	and submit alike (Phase 1)."""
	from erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry import (
		get_available_qty_to_reserve,
	)

	so_item = frappe.db.get_value(
		"Sales Order Item", sales_order_item,
		["name", "qty", "delivered_qty", "pieces", "conversion_factor", "stock_qty"],
		as_dict=True,
	)
	if not so_item:
		frappe.throw(frappe._("Sales Order Item {0} not found.").format(sales_order_item))

	conversion_factor = flt(so_item.conversion_factor) or 1
	delivered_stock_qty = flt(so_item.delivered_qty) * conversion_factor
	pending_stock_qty = flt(so_item.stock_qty) - delivered_stock_qty

	is_tolerance = cint(is_tolerance)

	if is_tolerance:
		pool = get_tolerance_pool(sales_order)
		used = get_used_tolerance_qty(sales_order, exclude_sre=exclude_sre)
		so_available_qty = max(0, floor_qty(pool - used - flt(already_staged_tolerance_qty), 3))
	else:
		base_reserved = get_base_reserved_qty(sales_order_item, exclude_sre=exclude_sre)
		so_available_qty = max(0, floor_qty(pending_stock_qty - base_reserved - flt(already_staged_qty), 3))

	batch_available_qty = None
	if batch_no:
		batch_available_qty = get_batch_available_qty(item_code, warehouse, batch_no, exclude_sre=exclude_sre)

	item_level_available_qty = floor_qty(
		get_available_qty_to_reserve(item_code, warehouse, ignore_sre=exclude_sre), 3
	)

	candidates = [so_available_qty, item_level_available_qty]
	if batch_available_qty is not None:
		candidates.append(batch_available_qty)

	allowed_qty = max(0, floor_qty(min(candidates), 3))

	return frappe._dict({
		"so_item": so_item,
		"pending_stock_qty": pending_stock_qty,
		"so_available_qty": so_available_qty,
		"batch_available_qty": batch_available_qty,
		"item_level_available_qty": item_level_available_qty,
		"allowed_qty": allowed_qty,
	})


class BatchWiseReservationTool(Document):
	def on_submit(self):
		self.create_stock_reservationentries()

	def on_cancel(self):
		self.cancel_stock_reservation_entries()

	def create_stock_reservationentries(self):
		if not self.reservation_batches:
			frappe.throw(frappe._("No reservation batches found. Please add batch reservations before submitting."))

		self._reservation_warnings = []
		self._tolerance_pool_used = get_used_tolerance_qty(self.sales_order) if self.get("sales_order") else 0

		for row in self.reservation_batches:
			self.create_fg_stock_reservation(
				item_code=row.item_code,
				warehouse=row.source_warehouse,
				qty=round(row.reserved_qty, 3),
				so_qty=row.sales_order_item_qty,
				stock_uom=frappe.db.get_value("Item", row.item_code, "stock_uom"),
				sales_order=row.sales_order,
				sales_order_item=row.sales_order_item,
				batch_no=row.batch_no,
				is_tolerance=cint(row.get("is_tolerance")),
				from_voucher_type=self.doctype,
				from_voucher_no=self.name,
				from_voucher_detail_no=row.name,
			)

		if self._reservation_warnings:
			lines = "<br>".join(self._reservation_warnings)
			frappe.msgprint(
				frappe._(
					"Some rows could not be fully reserved due to limited "
					"available stock:<br><br>{0}"
				).format(lines),
				title=frappe._("Partial / Skipped Reservations"),
				indicator="orange",
			)

	def cancel_stock_reservation_entries(self):
		sre_names = frappe.get_all(
			"Stock Reservation Entry",
			filters={"from_voucher_type": self.doctype, "from_voucher_no": self.name, "docstatus": 1},
			pluck="name",
		)
		for sre_name in sre_names:
			frappe.get_doc("Stock Reservation Entry", sre_name).cancel()

	def create_fg_stock_reservation(
		self, item_code, warehouse, qty, so_qty, stock_uom,
		sales_order=None, sales_order_item=None, batch_no=None, is_tolerance=0,
		from_voucher_type=None, from_voucher_no=None, from_voucher_detail_no=None,
	):
		if not sales_order:
			return

		qty = floor_qty(qty, 3)
		if qty <= 0:
			frappe.throw(frappe._("Reservation quantity must be greater than zero."))

		if batch_no and not frappe.get_cached_value("Item", item_code, "has_batch_no"):
			frappe.throw(
				frappe._(
					"Batch {0} was selected for Item {1}, but this Item does not have "
					"'Has Batch No' enabled."
				).format(frappe.bold(batch_no), frappe.bold(item_code))
			)

		is_tolerance = cint(is_tolerance)

		limits = get_reservation_ceiling(
			item_code=item_code, warehouse=warehouse, sales_order=sales_order,
			sales_order_item=sales_order_item, is_tolerance=is_tolerance, batch_no=batch_no,
		)
		so_detail = limits.so_item.name

		frappe.log_error(
			title="Stock Reservation Debug",
			message=frappe.as_json({
				"sales_order": sales_order, "sales_order_item": so_detail, "item_code": item_code,
				"warehouse": warehouse, "batch_no": batch_no, "is_tolerance": is_tolerance,
				"requested_qty": qty, "so_available_qty": limits.so_available_qty,
				"batch_available_qty": limits.batch_available_qty,
				"item_level_available_qty": limits.item_level_available_qty,
				"allowed_qty": limits.allowed_qty,
			}, indent=2),
		)

		if not hasattr(self, "_reservation_warnings"):
			self._reservation_warnings = []

		usable_qty_to_reserve = min(qty, limits.allowed_qty)

		if usable_qty_to_reserve <= 0:
			self._reservation_warnings.append(
				frappe._(
					"Row skipped - Item {0} from Batch {1} against Sales Order {2}: "
					"requested {3} {4}, but nothing is currently available to reserve."
				).format(frappe.bold(item_code), frappe.bold(batch_no or "-"), frappe.bold(sales_order), qty, stock_uom)
			)
			return

		reserve_qty = usable_qty_to_reserve
		if reserve_qty < qty:
			self._reservation_warnings.append(
				frappe._(
					"Row capped - Item {0} from Batch {1} against Sales Order {2}: "
					"requested {3} {4}, only {5} {4} was available and has been reserved instead."
				).format(frappe.bold(item_code), frappe.bold(batch_no or "-"), frappe.bold(sales_order), qty, stock_uom, reserve_qty)
			)

		final_pieces = calc_proportional_pieces(reserve_qty, batch_no) if batch_no else 0

		sre = frappe.new_doc("Stock Reservation Entry")
		sre.item_code = item_code
		sre.warehouse = warehouse
		sre.company = self.company
		sre.stock_uom = stock_uom
		sre.voucher_type = "Sales Order"
		sre.voucher_no = sales_order
		sre.voucher_detail_no = so_detail
		sre.from_voucher_type = from_voucher_type
		sre.from_voucher_no = from_voucher_no
		sre.from_voucher_detail_no = from_voucher_detail_no
		sre.custom_is_tolerance = is_tolerance
		sre.reserved_qty = flt(reserve_qty, 3)
		sre.voucher_qty = flt(so_qty, 3)
		sre.available_qty = flt(limits.allowed_qty, 3)
		sre.available_qty_to_reserve = flt(reserve_qty, 3)

		has_batch_no = frappe.get_cached_value("Item", item_code, "has_batch_no")
		if batch_no and has_batch_no and reserve_qty > 0:
			sre.has_batch_no = 1
			sre.has_serial_no = 0
			sre.reservation_based_on = "Serial and Batch"
			sre.use_serial_batch_fields = 1
			sre.append("sb_entries", {
				"batch_no": batch_no,
				"qty": reserve_qty,
				"warehouse": warehouse,
				"pieces": final_pieces,
				"length": frappe.db.get_value("Batch", batch_no, "average_length") or 0,
				"section_weight": frappe.db.get_value("Batch", batch_no, "section_weight") or 0,
			})
			sre.auto_reserve_serial_and_batch = lambda *args, **kwargs: None
		else:
			sre.reservation_based_on = "Qty"

		sre.flags.ignore_permissions = True
		sre.insert()
		sre.submit()

		if is_tolerance:
			self._tolerance_pool_used = flt(getattr(self, "_tolerance_pool_used", 0)) + flt(reserve_qty)

		frappe.log_error(
			title="Stock Reserved",
			message=f"Reserved {reserve_qty} of {item_code} in {warehouse} for SO {sales_order} "
					f"(Batch: {batch_no}, tolerance: {is_tolerance})",
		)


@frappe.whitelist()
def fetch_sales_order_items(filters):
	if isinstance(filters, str):
		filters = json.loads(filters)
	filters = filters or {}

	so_filters = [["docstatus", "=", 1]]
	if filters.get("customer"):
		so_filters.append(["customer", "=", filters.get("customer")])
	if filters.get("sales_order"):
		so_filters.append(["name", "=", filters.get("sales_order")])
	if filters.get("customer_po_no"):
		so_filters.append(["po_no", "like", f"%{filters.get('customer_po_no')}%"])
	if filters.get("sales_order_date"):
		so_filters.append(["transaction_date", "=", filters.get("sales_order_date")])

	sales_orders = frappe.get_all(
		"Sales Order", filters=so_filters,
		fields=["name", "customer", "transaction_date", "po_no", "company"],
		order_by="transaction_date desc",
	)
	if not sales_orders:
		return []

	so_names = [d.name for d in sales_orders]

	item_filters = {"parent": ["in", so_names]}
	if filters.get("item_name"):
		item_filters["item_name"] = ["like", f"%{filters.get('item_name')}%"]

	items = frappe.get_all(
		"Sales Order Item", filters=item_filters,
		fields=[
			"name", "parent", "item_code", "item_name", "qty", "delivered_qty", "pieces",
			"length_size", "description", "assorted_length", "warehouse", "uom", "stock_uom",
			"rate", "amount", "conversion_factor", "stock_qty",
		],
		order_by="parent asc, idx asc",
	)
	if not items:
		return []

	item_codes = list({d.item_code for d in items if d.item_code})
	item_details = frappe.get_all(
		"Item", filters={"name": ["in", item_codes]}, fields=["name", "weight_per_meter"],
	)
	weight_map = {d.name: flt(d.weight_per_meter) for d in item_details}

	rows = []
	for row in items:
		conversion_factor = flt(row.conversion_factor) or 1
		delivered_stock_qty = flt(row.delivered_qty) * conversion_factor
		pending_stock_qty = flt(row.stock_qty) - delivered_stock_qty

		if pending_stock_qty <= 0:
			continue

		# Uses the SAME base-reserved calc as staging/submit, so the
		# suggested reserve_qty here won't disagree with what BWRT will
		# actually allow later (Phase 1).
		base_reserved = get_base_reserved_qty(row.name)
		remaining = max(0, flt(pending_stock_qty) - flt(base_reserved))

		rows.append({
			"sales_order": row.parent,
			"sales_order_item": row.name,
			"item_code": row.item_code,
			"item_name": row.item_name,
			"qty": flt(row.qty),
			"conversion_factor": conversion_factor,
			"pending_qty": pending_stock_qty,
			"reserve_qty": remaining,
			"pieces": row.pieces,
			"length": row.length_size,
			"section_weight": weight_map.get(row.item_code, 0),
		})

	return rows


@frappe.whitelist()
def add_to_reservation_batches(
	docname, sales_order, sales_order_item, item_code, item_name,
	batch_no, reserved_qty, is_tolerance=0,
	sales_order_item_qty=0, length=0, pieces=0, section_weight=0,
	warehouse=None, posting_date=None,
):
	doc = frappe.get_doc("Batch Wise Reservation Tool", docname)
	if doc.docstatus != 0:
		frappe.throw(frappe._("Cannot modify a submitted or cancelled document."))

	for row in doc.get("reservation_batches"):
		if row.batch_no == batch_no and row.sales_order_item == sales_order_item:
			frappe.throw(
				frappe._("Batch {0} is already reserved for Sales Order Item {1}.").format(batch_no, sales_order_item)
			)

	is_tolerance = cint(is_tolerance)
	target_warehouse = warehouse or doc.warehouse

	already_staged_qty = flt(sum(
		flt(r.reserved_qty) for r in doc.get("reservation_batches")
		if r.sales_order_item == sales_order_item and not cint(r.get("is_tolerance"))
	))
	already_staged_tolerance_qty = flt(sum(
		flt(r.reserved_qty) for r in doc.get("reservation_batches")
		if cint(r.get("is_tolerance"))
	))

	limits = get_reservation_ceiling(
		item_code=item_code, warehouse=target_warehouse, sales_order=sales_order,
		sales_order_item=sales_order_item, is_tolerance=is_tolerance, batch_no=batch_no,
		already_staged_qty=already_staged_qty, already_staged_tolerance_qty=already_staged_tolerance_qty,
	)

	if flt(reserved_qty) > limits.allowed_qty:
		frappe.throw(
			frappe._(
				"Cannot reserve {0} from Batch {1} for Sales Order Item {2}: only {3} qty is "
				"currently available ({4})."
			).format(
				reserved_qty, batch_no, sales_order_item, limits.allowed_qty,
				frappe._("tolerance pool") if is_tolerance else frappe._("SO line / batch / warehouse"),
			)
		)

	final_pieces = calc_proportional_pieces(reserved_qty, batch_no) if batch_no else cint(pieces)

	doc.append("reservation_batches", {
		"sales_order": sales_order,
		"sales_order_item": sales_order_item,
		"sales_order_item_qty": sales_order_item_qty,
		"posting_date": posting_date or doc.posting_date,
		"item_code": item_code,
		"item_name": item_name,
		"batch_no": batch_no,
		"source_warehouse": target_warehouse,
		"reserved_qty": flt(reserved_qty),
		"reserved_pieces": final_pieces,
		"length": flt(length),
		"section_weight": flt(section_weight),
		"is_tolerance": is_tolerance,
	})

	doc.save(ignore_permissions=True)
	new_row = doc.reservation_batches[-1]
	return {
		"sales_order": new_row.sales_order,
		"sales_order_item": new_row.sales_order_item,
		"sales_order_item_qty": new_row.sales_order_item_qty,
		"posting_date": str(new_row.posting_date) if new_row.posting_date else "",
		"item_code": new_row.item_code,
		"item_name": new_row.item_name,
		"batch_no": new_row.batch_no,
		"source_warehouse": new_row.source_warehouse,
		"reserved_qty": flt(new_row.reserved_qty),
		"reserved_pieces": flt(new_row.reserved_pieces),
		"length": flt(new_row.length),
		"section_weight": flt(new_row.section_weight),
		"is_tolerance": cint(new_row.is_tolerance),
	}


@frappe.whitelist()
def fetch_available_batches(item_code, warehouse, pending_qty=0, reserve_qty=0):
	batch_stock = frappe.db.sql(
		"""
		SELECT sbe.batch_no AS batch, sle.item_code, SUM(sle.actual_qty) AS actual_qty
		FROM `tabStock Ledger Entry` sle
		INNER JOIN `tabSerial and Batch Entry` sbe ON sbe.parent = sle.serial_and_batch_bundle
		WHERE sle.item_code=%(item_code)s AND sle.warehouse=%(warehouse)s
			AND sle.is_cancelled=0 AND sbe.batch_no IS NOT NULL
		GROUP BY sbe.batch_no, sle.item_code
		HAVING SUM(sle.actual_qty) > 0
		""",
		{"item_code": item_code, "warehouse": warehouse}, as_dict=True,
	)
	if not batch_stock:
		return []

	batch_names = [d.batch for d in batch_stock]

	batch_details = frappe.db.get_all(
		"Batch", filters={"name": ["in", batch_names], "disabled": 0},
		fields=["name", "pieces", "average_length", "section_weight", "batch_qty"],
	)
	batch_map = {d.name: d for d in batch_details}

	# FIX (Phase 1, point 4): this query previously had no warehouse
	# filter, so a reservation against a DIFFERENT warehouse could wrongly
	# reduce a batch's apparent availability in THIS warehouse.
	reserved_qty_map = frappe.db.sql(
		"""
		SELECT sbe.batch_no, SUM(sbe.qty - IFNULL(sbe.delivered_qty, 0)) AS reserved_qty
		FROM `tabStock Reservation Entry` sre
		INNER JOIN `tabSerial and Batch Entry` sbe ON sbe.parent = sre.name
		WHERE sre.docstatus=1
			AND sre.status IN ('Reserved','Partially Reserved','Partially Delivered')
			AND sre.warehouse=%(warehouse)s
			AND sbe.batch_no IN %(batch_names)s
		GROUP BY sbe.batch_no
		""",
		{"warehouse": warehouse, "batch_names": tuple(batch_names)}, as_dict=True,
	)
	reserved_map = {d.batch_no: flt(d.reserved_qty) for d in reserved_qty_map}

	result = []
	for row in batch_stock:
		actual_qty = flt(row.actual_qty)
		reserved_qty = flt(reserved_map.get(row.batch, 0))
		available_qty = actual_qty - reserved_qty
		if available_qty <= 0:
			continue

		batch = batch_map.get(row.batch)
		# Pieces scaled to what's actually available, not the batch total (Phase 6)
		available_pieces = 0
		if batch and flt(batch.batch_qty):
			available_pieces = int(round(flt(batch.pieces) * (available_qty / flt(batch.batch_qty))))

		result.append({
			"batch": row.batch,
			"item_code": row.item_code,
			"item_name": frappe.db.get_value("Item", row.item_code, "item_name"),
			"pieces": available_pieces,
			"length": batch.average_length if batch else 0,
			"section_weight": batch.section_weight if batch else 0,
			"actual_qty": actual_qty,
			"reserved_qty": reserved_qty,
			"available_qty": available_qty,
		})

	result.sort(key=lambda d: d["available_qty"], reverse=True)
	return result


@frappe.whitelist()
def get_reserved_batches(docname):
	stock_reservation_entries = frappe.get_all(
		"Stock Reservation Entry",
		filters={"from_voucher_type": "Batch Wise Reservation Tool", "from_voucher_no": docname, "docstatus": 1},
		fields=["name", "item_code", "warehouse", "voucher_type", "voucher_no", "voucher_detail_no",
				"reserved_qty", "status", "custom_is_tolerance"],
		order_by="creation asc",
	)

	reserved_batches = []
	for sre in stock_reservation_entries:
		sb_entries = frappe.get_all(
			"Serial and Batch Entry", filters={"parent": sre.name, "parenttype": "Stock Reservation Entry"},
			fields=["batch_no", "qty", "warehouse"], order_by="idx asc",
		)
		for sb in sb_entries:
			reserved_batches.append({
				"sales_order": sre.voucher_no,
				"item_code": sre.item_code,
				"batch_no": sb.batch_no,
				"reserved_qty": sb.qty,
				"warehouse": sb.warehouse or sre.warehouse,
				"status": sre.status,
				"is_tolerance": cint(sre.custom_is_tolerance),
			})

	return reserved_batches


@frappe.whitelist()
def get_tolerance_warehouse_api():
	"""For JS to know which warehouse is the tolerance warehouse, purely
	as a UI default - the backend never trusts warehouse to decide
	is_tolerance anymore."""
	return get_tolerance_warehouse(throw=False)
