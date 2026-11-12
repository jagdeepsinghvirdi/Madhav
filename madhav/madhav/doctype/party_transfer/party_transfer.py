# Copyright (c) 2026, Finbyz pvt. ltd. and contributors
# For license information, please see license.txt
"""Party Transfer: move reserved finished goods from one order to another.

One document replaces the old three steps (unreserve on A, move the batch
to For Mill, reserve it for B with the Batch Wise Reservation Tool):

* the source reservation on A is reduced (or cancelled when fully moved),
* a reservation for the same batch, warehouse, pieces and length is made
  for B, and
* both happen in one transaction, so a failure leaves nothing half done.

The stock stays where it is. When the moved stock was production made for
A, A's planned quantity no longer covers it (see
``madhav.madhav.utils.production_demand``), so A can be planned again on a
new Production Plan. B's reservation counts as free stock for B, exactly
like a Batch Wise Reservation Tool reservation.
"""

import frappe
from erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry import (
	get_available_qty_to_reserve,
)
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from madhav.madhav.utils.production_demand import is_free_stock_source

QTY_EPSILON = 0.0005
CLOSED_SO_STATUSES = ("Closed", "Completed", "On Hold", "Cancelled")


class PartyTransfer(Document):
	def validate(self):
		self.set_destination_details()
		if self.docstatus == 0 or self.is_new():
			# Every open reservation is listed; keep only the rows given a qty.
			self.set("items", [row for row in self.items if flt(row.transfer_qty) > 0])
			for idx, row in enumerate(self.items, start=1):
				row.idx = idx
		if not self.items:
			frappe.throw(_("Enter the quantity to move on at least one reservation row."))
		self.validate_rows()
		self.validate_destination_allowance()
		self.set_totals()

	def on_submit(self):
		for row in self.items:
			self.reduce_source_reservation(row)
			self.create_destination_reservation(row)
		self.report_destination_excess()

	def before_cancel(self):
		# The reservations this transfer made are cancelled in on_cancel;
		# don't let the link check (or the form's Cancel All) do it first.
		self.ignore_linked_doctypes = ["Stock Reservation Entry"]

	def on_cancel(self):
		for row in self.items:
			self.cancel_destination_reservation(row)
			self.restore_source_reservation(row)

	# ------------------------------------------------------------------
	# Validation
	# ------------------------------------------------------------------

	def set_destination_details(self):
		so = frappe.db.get_value(
			"Sales Order",
			self.to_sales_order,
			["docstatus", "status", "customer", "customer_name", "company"],
			as_dict=True,
		)
		if not so or cint(so.docstatus) != 1:
			frappe.throw(_("Sales Order {0} must be submitted.").format(frappe.bold(self.to_sales_order)))
		if so.status in CLOSED_SO_STATUSES:
			frappe.throw(
				_("Sales Order {0} is {1}; stock cannot be moved to it.").format(
					frappe.bold(self.to_sales_order), so.status
				)
			)
		line = frappe.db.get_value(
			"Sales Order Item",
			self.to_sales_order_item,
			["parent", "item_code", "item_name"],
			as_dict=True,
		)
		if not line or line.parent != self.to_sales_order:
			frappe.throw(_("Pick the order line that needs the stock."))
		self.to_customer = so.customer
		self.to_customer_name = so.customer_name
		self.company = so.company
		self.item_code = line.item_code
		self.item_name = line.item_name

	def validate_rows(self):
		seen = set()
		for row in self.items:
			key = (row.stock_reservation_entry, row.batch_no or "")
			if key in seen:
				frappe.throw(
					_("Row #{0}: the same reservation and batch is listed twice.").format(row.idx)
				)
			seen.add(key)

			source = get_open_reservation(row.stock_reservation_entry, row.batch_no)
			if not source:
				frappe.throw(
					_("Row #{0}: reservation {1} is no longer open.").format(
						row.idx, frappe.bold(row.stock_reservation_entry)
					)
				)
			if source.item_code != self.item_code:
				frappe.throw(
					_("Row #{0}: reservation {1} is for item {2}, not {3}.").format(
						row.idx, row.stock_reservation_entry, source.item_code, self.item_code
					)
				)
			if source.sales_order == self.to_sales_order:
				frappe.throw(
					_("Row #{0}: the stock is already reserved for {1}.").format(
						row.idx, frappe.bold(self.to_sales_order)
					)
				)
			if source.company != self.company:
				frappe.throw(_("Row #{0}: the reservation belongs to another company.").format(row.idx))

			# Refresh the read-only details from the live reservation.
			row.from_sales_order = source.sales_order
			row.from_sales_order_item = source.sales_order_item
			row.from_customer = source.customer
			row.from_customer_name = source.customer_name
			row.warehouse = source.warehouse
			row.length = source.length
			row.section_weight = source.section_weight
			row.available_qty = source.open_qty
			row.available_pieces = source.open_pieces
			row.source_type = "Spare" if is_free_stock_source(source.from_voucher_type) else "Production"

			row.transfer_qty = flt(row.transfer_qty, 3)
			if row.transfer_qty <= 0:
				frappe.throw(_("Row #{0}: Qty to Move must be more than zero.").format(row.idx))
			if row.transfer_qty > flt(source.open_qty) + QTY_EPSILON:
				frappe.throw(
					_("Row #{0}: only {1} is still reserved on {2} for batch {3}.").format(
						row.idx, source.open_qty, row.from_sales_order, row.batch_no or "-"
					)
				)
			if flt(row.transfer_pieces) <= 0 and flt(source.open_pieces) > 0:
				row.transfer_pieces = proportional_pieces(
					row.transfer_qty, source.open_qty, source.open_pieces
				)
			row.transfer_pieces = cint(row.transfer_pieces)
			if flt(source.open_pieces) > 0 and row.transfer_pieces > flt(source.open_pieces):
				frappe.throw(
					_("Row #{0}: only {1} pieces are reserved on {2} for batch {3}.").format(
						row.idx, cint(source.open_pieces), row.from_sales_order, row.batch_no or "-"
					)
				)

			from madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool import (
				validate_length_for_so_reservation,
			)

			validate_length_for_so_reservation(
				self.to_sales_order_item,
				row.length,
				sales_order=self.to_sales_order,
				item_code=self.item_code,
				batch_no=row.batch_no,
			)

	def validate_destination_allowance(self):
		from madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool import (
			get_so_line_allowance,
		)

		total = sum(flt(row.transfer_qty) for row in self.items)
		allowance = get_so_line_allowance(self.to_sales_order, self.to_sales_order_item, is_tolerance=0)
		if total > flt(allowance) + QTY_EPSILON:
			frappe.throw(
				_(
					"{0} can take at most {1} more (order balance plus the allowed tolerance). "
					"You are moving {2}."
				).format(frappe.bold(self.to_sales_order), flt(allowance, 3), flt(total, 3))
			)

	def set_totals(self):
		self.total_qty = flt(sum(flt(row.transfer_qty) for row in self.items), 3)
		self.total_pieces = sum(cint(row.transfer_pieces) for row in self.items)

	# ------------------------------------------------------------------
	# Submit
	# ------------------------------------------------------------------

	def reduce_source_reservation(self, row):
		sre = frappe.get_doc("Stock Reservation Entry", row.stock_reservation_entry)
		open_total = flt(sre.reserved_qty) - flt(sre.delivered_qty)
		if open_total < flt(row.transfer_qty) - QTY_EPSILON:
			frappe.throw(
				_("Row #{0}: reservation {1} changed while you were working; reload and try again.").format(
					row.idx, sre.name
				)
			)

		fully_moved = flt(sre.delivered_qty) <= QTY_EPSILON and abs(open_total - flt(row.transfer_qty)) <= QTY_EPSILON
		if fully_moved:
			sre.flags.ignore_permissions = True
			# This transfer's own row links to the reservation; that link is
			# the record of the move, not a reason to block it.
			sre.flags.ignore_links = True
			sre.ignore_linked_doctypes = ["Party Transfer"]
			sre.cancel()
			row.db_set("source_cancelled", 1)
		else:
			trim_reservation(sre, row.batch_no, row.transfer_qty, row.transfer_pieces)

		_comment(
			"Stock Reservation Entry",
			sre.name,
			_("{0} {1} moved to {2} ({3}) by Party Transfer {4}.").format(
				flt(row.transfer_qty, 3), sre.stock_uom, self.to_sales_order, self.to_customer_name, self.name
			),
		)

	def create_destination_reservation(self, row):
		so_line = frappe.db.get_value(
			"Sales Order Item", self.to_sales_order_item, ["stock_qty", "qty", "conversion_factor"], as_dict=True
		)
		source = frappe.get_doc("Stock Reservation Entry", row.stock_reservation_entry)

		sre = frappe.new_doc("Stock Reservation Entry")
		sre.item_code = self.item_code
		sre.warehouse = row.warehouse
		sre.company = self.company
		sre.stock_uom = source.stock_uom
		sre.voucher_type = "Sales Order"
		sre.voucher_no = self.to_sales_order
		sre.voucher_detail_no = self.to_sales_order_item
		sre.voucher_qty = flt(so_line.stock_qty) or flt(so_line.qty) * (flt(so_line.conversion_factor) or 1)
		sre.from_voucher_type = "Party Transfer"
		sre.from_voucher_no = self.name
		sre.from_voucher_detail_no = row.name
		if frappe.db.has_column("Stock Reservation Entry", "custom_is_tolerance"):
			sre.custom_is_tolerance = 0
		sre.reserved_qty = flt(row.transfer_qty, 3)
		sre.available_qty_to_reserve = flt(row.transfer_qty, 3)
		# Mandatory on Stock Reservation Entry: free stock of the item in the
		# warehouse, which now includes what the source reservation let go.
		sre.available_qty = flt(get_available_qty_to_reserve(self.item_code, row.warehouse), 3)

		if row.batch_no:
			sre.has_batch_no = 1
			sre.has_serial_no = 0
			sre.reservation_based_on = "Serial and Batch"
			sre.use_serial_batch_fields = 1
			sre.append(
				"sb_entries",
				{
					"batch_no": row.batch_no,
					"qty": flt(row.transfer_qty, 3),
					"warehouse": row.warehouse,
					"pieces": cint(row.transfer_pieces),
					"length": flt(row.length),
					"section_weight": flt(row.section_weight),
				},
			)
			# The batch is chosen here; ERPNext must not pick another one.
			sre.auto_reserve_serial_and_batch = lambda *args, **kwargs: None
		else:
			sre.reservation_based_on = "Qty"

		sre.flags.ignore_permissions = True
		sre.insert()
		sre.submit()

		# With partial reservation allowed, ERPNext may quietly reserve less
		# than asked. Never take more from A than B actually received.
		reserved = flt(frappe.db.get_value("Stock Reservation Entry", sre.name, "reserved_qty"), 3)
		if reserved < flt(row.transfer_qty, 3) - QTY_EPSILON:
			frappe.throw(
				_(
					"Row #{0}: only {1} of {2} could be reserved for {3} from batch {4}. "
					"Nothing was moved."
				).format(row.idx, reserved, flt(row.transfer_qty, 3), self.to_sales_order, row.batch_no or "-")
			)
		row.db_set("new_stock_reservation_entry", sre.name)

	def report_destination_excess(self):
		info = get_destination_excess(self.to_sales_order_item)
		if info.excess <= QTY_EPSILON or not info.work_orders:
			return
		lines = "".join(
			"<li>{0} - {1} ({2} still to produce)</li>".format(
				frappe.utils.get_link_to_form("Work Order", wo.name), wo.status, flt(wo.unproduced, 3)
			)
			for wo in info.work_orders
		)
		frappe.msgprint(
			_(
				"{0} now has {1} more than it needs, because production is already planned for it:"
				"<ul>{2}</ul>Use <b>Reduce Work Orders</b> on this transfer to cut Not Started Work Orders. "
				"Close In Process Work Orders once they have produced what the order still needs."
			).format(frappe.bold(self.to_sales_order), flt(info.excess, 3), lines),
			title=_("Production already planned for {0}").format(self.to_customer_name),
			indicator="orange",
		)

	# ------------------------------------------------------------------
	# Cancel
	# ------------------------------------------------------------------

	def cancel_destination_reservation(self, row):
		names = frappe.get_all(
			"Stock Reservation Entry",
			filters={"from_voucher_type": "Party Transfer", "from_voucher_detail_no": row.name, "docstatus": 1},
			fields=["name", "delivered_qty"],
		)
		for sre in names:
			if flt(sre.delivered_qty) > QTY_EPSILON:
				frappe.throw(
					_(
						"Row #{0}: {1} has already delivered part of this stock (reservation {2}). "
						"This transfer cannot be cancelled."
					).format(row.idx, self.to_sales_order, sre.name)
				)
		for sre in names:
			doc = frappe.get_doc("Stock Reservation Entry", sre.name)
			doc.flags.ignore_permissions = True
			doc.cancel()

	def restore_source_reservation(self, row):
		from madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool import (
			get_batch_available_qty,
		)

		source = frappe.get_doc("Stock Reservation Entry", row.stock_reservation_entry)
		if row.batch_no:
			free = get_batch_available_qty(self.item_code, row.warehouse, row.batch_no)
			if free < flt(row.transfer_qty) - QTY_EPSILON:
				frappe.throw(
					_(
						"Row #{0}: batch {1} no longer has {2} free in {3}, so the reservation of {4} "
						"cannot be given back. This transfer cannot be cancelled."
					).format(row.idx, row.batch_no, flt(row.transfer_qty, 3), row.warehouse, row.from_sales_order)
				)

		if source.docstatus == 1:
			trim_reservation(source, row.batch_no, -flt(row.transfer_qty), -cint(row.transfer_pieces))
		else:
			recreate_reservation(source, row)

		_comment(
			"Stock Reservation Entry",
			source.name,
			_("{0} given back from {1} - Party Transfer {2} cancelled.").format(
				flt(row.transfer_qty, 3), self.to_sales_order, self.name
			),
		)

	# ------------------------------------------------------------------
	# Reduce B's Work Orders
	# ------------------------------------------------------------------

	@frappe.whitelist()
	def reduce_work_orders(self):
		"""Cut Not Started Work Orders of the receiving line by its excess."""
		if self.docstatus != 1:
			frappe.throw(_("Submit the transfer first."))
		if not frappe.has_permission("Work Order", "cancel"):
			frappe.throw(_("You need permission to cancel Work Orders."), frappe.PermissionError)

		info = get_destination_excess(self.to_sales_order_item)
		remaining = flt(info.excess, 3)
		if remaining <= QTY_EPSILON:
			frappe.msgprint(_("{0} has no excess production planned.").format(self.to_sales_order))
			return []

		done, skipped = [], []
		for wo in info.work_orders:
			if remaining <= QTY_EPSILON:
				break
			if wo.status == "In Process":
				# Its quantity can't be changed once started. Leave a note on
				# the Work Order saying when to close it instead.
				cut = min(remaining, flt(wo.unproduced))
				if cut > QTY_EPSILON:
					stop_at = flt(flt(wo.qty) - cut, 3)
					_comment(
						"Work Order",
						wo.name,
						_(
							"{0} now needs {1} less because Party Transfer {2} gave it stock. "
							"Close this Work Order once it has produced {3}."
						).format(self.to_sales_order, flt(cut, 3), self.name, stop_at),
					)
					wo.blocked_reason = _("In Process - note added: close it at {0} produced").format(stop_at)
					remaining = flt(remaining - cut, 3)
				skipped.append(wo)
				continue
			if wo.blocked_reason:
				skipped.append(wo)
				continue
			cut = min(remaining, flt(wo.qty))
			new_name = replace_work_order(wo.name, flt(wo.qty) - cut)
			remaining = flt(remaining - cut, 3)
			done.append(frappe._dict(name=wo.name, cut=cut, replacement=new_name))

		messages = [
			_("{0}: reduced by {1}{2}").format(
				d.name, flt(d.cut, 3), _(" - replaced by {0}").format(d.replacement) if d.replacement else ""
			)
			for d in done
		]
		for wo in skipped:
			messages.append(
				_("{0}: not changed ({1})").format(wo.name, wo.blocked_reason or _("{0} - close it once enough is produced").format(wo.status))
			)
		if remaining > QTY_EPSILON:
			messages.append(_("{0} still planned beyond what the order needs.").format(flt(remaining, 3)))
		self.add_comment("Info", _("Reduce Work Orders:") + "<br>" + "<br>".join(messages))
		frappe.msgprint("<br>".join(messages), title=_("Reduce Work Orders"))
		return done


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def proportional_pieces(qty, basis_qty, basis_pieces):
	if flt(basis_qty) <= 0 or flt(basis_pieces) <= 0:
		return 0
	if flt(qty) + QTY_EPSILON >= flt(basis_qty):
		return cint(basis_pieces)
	return max(0, min(int(round(flt(basis_pieces) * flt(qty) / flt(basis_qty))), cint(basis_pieces)))


def get_open_reservation(sre_name, batch_no=None):
	"""Open (undelivered) part of one reservation, for one batch if given."""
	if not sre_name:
		return None
	sre = frappe.db.get_value(
		"Stock Reservation Entry",
		sre_name,
		[
			"name", "docstatus", "status", "voucher_type", "voucher_no", "voucher_detail_no",
			"item_code", "warehouse", "company", "reserved_qty", "delivered_qty",
			"reservation_based_on", "from_voucher_type",
		],
		as_dict=True,
	)
	if not sre or cint(sre.docstatus) != 1 or sre.voucher_type != "Sales Order":
		return None
	if sre.status in ("Delivered", "Cancelled"):
		return None

	out = frappe._dict(
		item_code=sre.item_code,
		warehouse=sre.warehouse,
		company=sre.company,
		sales_order=sre.voucher_no,
		sales_order_item=sre.voucher_detail_no,
		from_voucher_type=sre.from_voucher_type,
		length=0.0,
		section_weight=0.0,
		open_pieces=0.0,
	)
	if batch_no:
		entry = frappe.db.get_value(
			"Serial and Batch Entry",
			{"parent": sre.name, "parenttype": "Stock Reservation Entry", "batch_no": batch_no},
			["qty", "delivered_qty", "pieces", "length", "section_weight"],
			as_dict=True,
		)
		if not entry:
			return None
		out.open_qty = flt(flt(entry.qty) - flt(entry.delivered_qty), 3)
		out.open_pieces = proportional_pieces(out.open_qty, entry.qty, entry.pieces)
		out.length = flt(entry.length) or flt(frappe.db.get_value("Batch", batch_no, "average_length"))
		out.section_weight = flt(entry.section_weight) or flt(frappe.db.get_value("Batch", batch_no, "section_weight"))
	else:
		out.open_qty = flt(flt(sre.reserved_qty) - flt(sre.delivered_qty), 3)
	if out.open_qty <= QTY_EPSILON:
		return None

	customer = frappe.db.get_value("Sales Order", sre.voucher_no, ["customer", "customer_name"], as_dict=True) or {}
	out.customer = customer.get("customer")
	out.customer_name = customer.get("customer_name")
	return out


def trim_reservation(sre, batch_no, qty, pieces=0):
	"""Change a submitted reservation by ``-qty`` (negative qty adds back).

	ERPNext does not allow editing a partly delivered reservation through
	the form, so the batch row and header are updated directly and the
	Sales Order, bin and status totals refreshed - the same way the
	Delivery Note code releases undelivered reservation.
	"""
	qty = flt(qty, 3)
	if sre.reservation_based_on == "Serial and Batch" and batch_no:
		entry = next((e for e in sre.get("sb_entries") or [] if e.batch_no == batch_no), None)
		if not entry:
			frappe.throw(_("Batch {0} is not on reservation {1}.").format(batch_no, sre.name))
		new_qty = flt(flt(entry.qty) - qty, 3)
		if new_qty < flt(entry.get("delivered_qty")) - QTY_EPSILON:
			frappe.throw(_("Reservation {0} cannot go below what it has delivered.").format(sre.name))
		entry.db_set("qty", new_qty, update_modified=False)
		if frappe.db.has_column("Serial and Batch Entry", "pieces"):
			entry.db_set("pieces", max(0, cint(entry.get("pieces")) - cint(pieces)), update_modified=False)
		entry.qty = new_qty
		new_reserved = flt(sum(flt(e.qty) for e in sre.sb_entries), 3)
	else:
		new_reserved = flt(flt(sre.reserved_qty) - qty, 3)
	if new_reserved < flt(sre.delivered_qty) - QTY_EPSILON:
		frappe.throw(_("Reservation {0} cannot go below what it has delivered.").format(sre.name))
	sre.db_set("reserved_qty", new_reserved)

	sre = frappe.get_doc("Stock Reservation Entry", sre.name)
	sre.update_status()
	sre.update_reserved_stock_in_bin()
	sre.update_reserved_qty_in_voucher()


def recreate_reservation(cancelled_sre, row):
	"""Give a fully moved reservation back to the giving order on cancel."""
	new = frappe.copy_doc(cancelled_sre)
	new.amended_from = None
	new.status = "Draft"
	new.delivered_qty = 0
	new.reserved_qty = flt(row.transfer_qty, 3)
	new.available_qty_to_reserve = flt(row.transfer_qty, 3)
	new.available_qty = flt(get_available_qty_to_reserve(new.item_code, new.warehouse), 3)
	if new.reservation_based_on == "Serial and Batch":
		new.set("sb_entries", [])
		new.append(
			"sb_entries",
			{
				"batch_no": row.batch_no,
				"qty": flt(row.transfer_qty, 3),
				"warehouse": row.warehouse,
				"pieces": cint(row.transfer_pieces),
				"length": flt(row.length),
				"section_weight": flt(row.section_weight),
			},
		)
		new.auto_reserve_serial_and_batch = lambda *args, **kwargs: None
	new.flags.ignore_permissions = True
	new.insert()
	new.submit()
	return new.name


def get_destination_excess(sales_order_item):
	"""How far the receiving line is now over-covered, and its open Work Orders."""
	from madhav.madhav.override.production_plan import (
		get_already_reserved_qty,
		get_bwrt_delivered_qty,
	)
	from madhav.madhav.utils.production_demand import get_effective_planned

	out = frappe._dict(excess=0.0, work_orders=[])
	so_item = frappe.db.get_value(
		"Sales Order Item",
		sales_order_item,
		["stock_qty", "qty", "conversion_factor", "delivered_qty", "production_plan_qty"],
		as_dict=True,
	)
	if not so_item:
		return out
	cf = flt(so_item.conversion_factor) or 1
	ordered = flt(so_item.stock_qty) or flt(so_item.qty) * cf
	free_open = get_already_reserved_qty(sales_order_item)
	free_delivered = get_bwrt_delivered_qty(sales_order_item)
	delivered_outside_free = max(0, flt(so_item.delivered_qty) * cf - free_delivered)
	planned = get_effective_planned(sales_order_item, so_item.production_plan_qty).qty
	covered = max(planned, delivered_outside_free) + free_open + free_delivered
	excess = max(0.0, covered - ordered)

	work_orders = frappe.db.sql(
		"""
		select name, status, qty, produced_qty, ifnull(cutting_plan_reference, '') as cutting_plan_reference
		from `tabWork Order`
		where sales_order_item = %s and docstatus = 1
			and status in ('Not Started', 'In Process')
			and ifnull(production_plan, '') != ''
		order by field(status, 'Not Started', 'In Process'), creation desc
		""",
		sales_order_item,
		as_dict=True,
	)
	unproduced_total = 0.0
	for wo in work_orders:
		wo.unproduced = max(0.0, flt(wo.qty) - flt(wo.produced_qty))
		unproduced_total += wo.unproduced
		wo.blocked_reason = ""
		if wo.status == "Not Started":
			if wo.cutting_plan_reference:
				wo.blocked_reason = _("in Cutting Plan {0}").format(wo.cutting_plan_reference)
			elif frappe.db.exists("Stock Entry", {"work_order": wo.name, "docstatus": ["<", 2]}):
				wo.blocked_reason = _("has stock entries")
			elif frappe.db.exists("Job Card", {"work_order": wo.name, "docstatus": ["<", 2]}):
				wo.blocked_reason = _("has job cards")
	out.excess = min(excess, unproduced_total)
	out.work_orders = work_orders
	return out


def replace_work_order(work_order, new_qty):
	"""Cancel a Not Started Work Order and, if anything is left, remake it smaller.

	The replacement keeps the Production Plan link, so the plan row's
	ordered quantity drops by the cut and that cut counts as released.
	"""
	wo = frappe.get_doc("Work Order", work_order)
	if wo.status != "Not Started":
		frappe.throw(_("Work Order {0} has started and cannot be reduced.").format(wo.name))
	new_qty = flt(new_qty, 3)
	ratio = new_qty / flt(wo.qty) if flt(wo.qty) else 0

	wo.flags.ignore_permissions = True
	wo.cancel()
	if new_qty <= QTY_EPSILON:
		return None

	new = frappe.copy_doc(wo)
	new.amended_from = None
	new.qty = new_qty
	if new.meta.has_field("pieces") and flt(wo.get("pieces")):
		new.pieces = int(round(flt(wo.pieces) * ratio))
	for field in ("pending_qty", "pending_pcs", "completed_pcs"):
		if new.meta.has_field(field):
			new.set(field, 0)
	if new.meta.has_field("pending_qty"):
		new.pending_qty = new_qty
	if new.meta.has_field("pending_pcs") and new.meta.has_field("pieces"):
		new.pending_pcs = new.get("pieces") or 0
	new.flags.ignore_permissions = True
	new.insert()
	new.submit()
	return new.name


def _comment(doctype, name, text):
	try:
		frappe.get_doc(doctype, name).add_comment("Info", text)
	except Exception:
		frappe.log_error(title="Party Transfer comment failed", message=frappe.get_traceback())


@frappe.whitelist()
def get_order_lines(sales_order):
	"""Lines of the receiving order, for the To Order Line picker."""
	frappe.has_permission("Sales Order", "read", sales_order, throw=True)
	return frappe.get_all(
		"Sales Order Item",
		filters={"parent": sales_order, "docstatus": 1},
		fields=["name", "idx", "item_code", "item_name", "stock_qty", "delivered_qty",
			"conversion_factor", "stock_reserved_qty", "length_size", "pieces"],
		order_by="idx asc",
	)


@frappe.whitelist()
def get_available_reservations(to_sales_order, to_sales_order_item):
	"""Every open reservation of the same item on other orders, batch by batch."""
	frappe.has_permission("Party Transfer", "create", throw=True)
	from madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool import (
		get_so_length_window,
	)

	line = frappe.db.get_value(
		"Sales Order Item", to_sales_order_item, ["parent", "item_code"], as_dict=True
	)
	if not line or line.parent != to_sales_order:
		frappe.throw(_("Pick a line of {0}.").format(to_sales_order))
	company = frappe.db.get_value("Sales Order", to_sales_order, "company")
	min_len, max_len, _so_len = get_so_length_window(to_sales_order_item)

	rows = frappe.db.sql(
		"""
		select sre.name as sre, sre.voucher_no as sales_order, sre.voucher_detail_no as so_item,
			sre.warehouse, sre.from_voucher_type, sre.reservation_based_on,
			sre.reserved_qty, sre.delivered_qty,
			sbe.batch_no, sbe.qty as sb_qty, ifnull(sbe.delivered_qty, 0) as sb_delivered,
			ifnull(sbe.pieces, 0) as sb_pieces, ifnull(sbe.length, 0) as sb_length,
			ifnull(sbe.section_weight, 0) as sb_section_weight,
			so.customer, so.customer_name, so.delivery_date
		from `tabStock Reservation Entry` sre
		inner join `tabSales Order` so on so.name = sre.voucher_no
		left join `tabSerial and Batch Entry` sbe
			on sbe.parent = sre.name and sbe.parenttype = 'Stock Reservation Entry'
		where sre.docstatus = 1
			and sre.voucher_type = 'Sales Order'
			and sre.voucher_no != %(so)s
			and sre.item_code = %(item)s
			and sre.company = %(company)s
			and sre.status in ('Reserved', 'Partially Reserved', 'Partially Delivered')
		order by so.delivery_date desc, sre.creation desc
		""",
		{"so": to_sales_order, "item": line.item_code, "company": company},
		as_dict=True,
	)
	out = []
	for r in rows:
		if r.batch_no:
			open_qty = flt(flt(r.sb_qty) - flt(r.sb_delivered), 3)
			open_pieces = proportional_pieces(open_qty, r.sb_qty, r.sb_pieces)
			length = flt(r.sb_length) or flt(frappe.db.get_value("Batch", r.batch_no, "average_length"))
			section_weight = flt(r.sb_section_weight)
		else:
			if r.reservation_based_on == "Serial and Batch":
				continue
			open_qty = flt(flt(r.reserved_qty) - flt(r.delivered_qty), 3)
			open_pieces, length, section_weight = 0, 0, 0
		if open_qty <= QTY_EPSILON:
			continue
		length_ok = min_len is None or (length and min_len <= length <= max_len)
		out.append(
			{
				"stock_reservation_entry": r.sre,
				"from_sales_order": r.sales_order,
				"from_sales_order_item": r.so_item,
				"from_customer": r.customer,
				"from_customer_name": r.customer_name,
				"batch_no": r.batch_no,
				"warehouse": r.warehouse,
				"length": length,
				"section_weight": section_weight,
				"available_qty": open_qty,
				"available_pieces": open_pieces,
				"source_type": "Spare" if is_free_stock_source(r.from_voucher_type) else "Production",
				"length_ok": 1 if length_ok else 0,
			}
		)
	# Lengths that fit the receiving order first.
	out.sort(key=lambda x: (0 if x["length_ok"] else 1))
	return out
