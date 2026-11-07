import frappe
from frappe.utils import cint, flt
from erpnext.stock.get_item_details import get_conversion_factor
from erpnext.controllers.selling_controller import get_serial_and_batch_bundle
from erpnext.controllers.selling_controller import SellingController
from frappe import _

def update_stock_ledger(self, allow_negative_stock=False):
            self.update_reserved_qty()
                    
            sl_entries = []
            # Loop over items and packed items table
            for d in self.get_item_list():
                if frappe.get_cached_value("Item", d.item_code, "is_stock_item") == 1 and flt(d.qty):
                    if flt(d.conversion_factor) == 0.0:
                        d.conversion_factor = (
                            get_conversion_factor(d.item_code, d.uom).get("conversion_factor") or 1.0
                        )
                    
                    # On cancellation or return entry submission, make stock ledger entry for
                    # target warehouse first, to update serial no values properly
                    
                    if d.warehouse and (
                        (not cint(self.is_return) and self.docstatus == 1)
                        or (cint(self.is_return) and self.docstatus == 2)
                    ):
                        sl_entries.append(self.get_sle_for_source_warehouse(d))
                    
                    if d.target_warehouse:
                        sl_entries.append(self.get_sle_for_target_warehouse(d))

                    if d.warehouse and (
                        (not cint(self.is_return) and self.docstatus == 2)
                        or (cint(self.is_return) and self.docstatus == 1)
                    ):
                        sl_entries.append(self.get_sle_for_source_warehouse(d))

            self.make_sl_entries(sl_entries, allow_negative_stock=allow_negative_stock)

def get_sle_for_source_warehouse(self, item_row):
    
    serial_and_batch_bundle = (
        item_row.serial_and_batch_bundle
        if not self.is_internal_transfer() or self.docstatus == 1
        else None
    )

    if self.is_internal_transfer():
        if serial_and_batch_bundle and self.docstatus == 1 and self.is_return:
            serial_and_batch_bundle = self.make_package_for_transfer(
                serial_and_batch_bundle, item_row.warehouse, type_of_transaction="Inward"
            )
        elif not serial_and_batch_bundle:
            serial_and_batch_bundle = frappe.db.get_value(
                "Stock Ledger Entry",
                {"voucher_detail_no": item_row.name, "warehouse": item_row.warehouse},
                "serial_and_batch_bundle",
            )

    sle = self.get_sl_entries(
        item_row,
        {
            "actual_qty": -1 * flt(item_row.qty),
            "incoming_rate": item_row.incoming_rate,
            "recalculate_rate": cint(self.is_return),
            "serial_and_batch_bundle": serial_and_batch_bundle,
            "pieces_qty": -1 * flt(item_row.pieces),  # ← your custom field
        },
    )

    if item_row.target_warehouse and not cint(self.is_return):
        sle.dependant_sle_voucher_detail_no = item_row.name

    return sle

def get_sle_for_target_warehouse(self, item_row):
    sle = self.get_sl_entries(
        item_row,
        {
            "actual_qty": flt(item_row.qty),
            "warehouse": item_row.target_warehouse,
            "pieces_qty": flt(item_row.pieces),  # ← your custom field
        },
    )

    if self.docstatus == 1:
        if not cint(self.is_return):
            sle.update({"incoming_rate": item_row.incoming_rate, "recalculate_rate": 1})
        else:
            sle.update({"outgoing_rate": item_row.incoming_rate})
            if item_row.warehouse:
                sle.dependant_sle_voucher_detail_no = item_row.name

        if item_row.serial_and_batch_bundle and not cint(self.is_return):
            type_of_transaction = "Inward"
            if cint(self.is_return):
                type_of_transaction = "Outward"

            sle["serial_and_batch_bundle"] = self.make_package_for_transfer(
                item_row.serial_and_batch_bundle,
                item_row.target_warehouse,
                type_of_transaction=type_of_transaction,
            )

    return sle

def get_item_list(self):
	il = []
	for d in self.get("items"):
		if d.qty is None:
			frappe.throw(_("Row {0}: Qty is mandatory").format(d.idx))

		if self.has_product_bundle(d.item_code):
			for p in self.get("packed_items"):
				if p.parent_detail_docname == d.name and p.parent_item == d.item_code:
					il.append(
						frappe._dict(
							{
								"warehouse": p.warehouse or d.warehouse,
								"item_code": p.item_code,
								"qty": flt(p.qty),
								"serial_no": p.serial_no if self.docstatus == 2 else None,
								"batch_no": p.batch_no if self.docstatus == 2 else None,
								"uom": p.uom,
								"serial_and_batch_bundle": p.serial_and_batch_bundle
								or get_serial_and_batch_bundle(p, self, d),
								"name": d.name,
								"target_warehouse": p.target_warehouse,
								"company": self.company,
								"voucher_type": self.doctype,
								"allow_zero_valuation": d.allow_zero_valuation_rate,
								"sales_invoice_item": d.get("sales_invoice_item"),
								"dn_detail": d.get("dn_detail"),
								"incoming_rate": p.get("incoming_rate"),
								"item_row": p,
								"pieces": p.get("pieces"),  # ✅ Add this
							}
						)
					)
		else:
			il.append(
				frappe._dict(
					{
						"warehouse": d.warehouse,
						"item_code": d.item_code,
						"qty": d.stock_qty,
						"serial_no": d.serial_no if self.docstatus == 2 else None,
						"batch_no": d.batch_no if self.docstatus == 2 else None,
						"uom": d.uom,
						"stock_uom": d.stock_uom,
						"conversion_factor": d.conversion_factor,
						"serial_and_batch_bundle": d.serial_and_batch_bundle,
						"name": d.name,
						"target_warehouse": d.target_warehouse,
						"company": self.company,
						"voucher_type": self.doctype,
						"allow_zero_valuation": d.allow_zero_valuation_rate,
						"sales_invoice_item": d.get("sales_invoice_item"),
						"dn_detail": d.get("dn_detail"),
						"incoming_rate": d.get("incoming_rate"),
						"item_row": d,
						"pieces": d.get("pieces"),  # ✅ Add this
					}
				)
			)

	return il


def _dn_item_delivered_batches(item):
	"""Batch → qty this DN/SI row is shipping (from SABB and/or batch_no)."""
	delivered = {}
	if item.serial_and_batch_bundle:
		sbb = frappe.get_doc("Serial and Batch Bundle", item.serial_and_batch_bundle)
		for d in sbb.entries:
			if not d.batch_no:
				continue
			delivered[d.batch_no] = delivered.get(d.batch_no, 0) + abs(flt(d.qty))
	elif item.get("batch_no"):
		delivered[item.batch_no] = abs(flt(item.stock_qty) or flt(item.qty))
	return delivered


SRE_DELIVERED_COMMENT_PREFIX = "MADHAV_DN_SRE_DELIVERED::"


def _store_dn_sre_delivered_log(dn_name, log):
	"""Record exactly what this Delivery Note delivered on each SRE.

	Written on every submit (an empty list too) so cancel can tell a DN that
	delivered nothing apart from a DN submitted before this record existed.
	"""
	import json

	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": "Delivery Note",
			"reference_name": dn_name,
			"content": SRE_DELIVERED_COMMENT_PREFIX + json.dumps(log),
		}
	).insert(ignore_permissions=True)


def _load_dn_sre_delivered_log(dn_name):
	"""Return (log, comment_name), or (None, None) when the DN has no record."""
	import json

	rows = frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": "Delivery Note",
			"reference_name": dn_name,
			"comment_type": "Info",
			"content": ["like", SRE_DELIVERED_COMMENT_PREFIX + "%"],
		},
		fields=["name", "content"],
		order_by="creation desc",
		limit=1,
	)
	if not rows:
		return None, None
	try:
		log = json.loads(rows[0].content[len(SRE_DELIVERED_COMMENT_PREFIX):])
	except Exception:
		return None, None
	return (log if isinstance(log, list) else None), rows[0].name


def _undeliver_from_log(log):
	"""Reverse exactly the deliveries this DN recorded on submit."""
	by_sre = {}
	for rec in log or []:
		by_sre.setdefault(rec.get("sre"), []).append(rec)

	for sre_name, recs in by_sre.items():
		if not sre_name or cint(
			frappe.db.get_value("Stock Reservation Entry", sre_name, "docstatus")
		) != 1:
			continue
		sre_doc = frappe.get_doc("Stock Reservation Entry", sre_name)
		entries = {e.name: e for e in (sre_doc.get("sb_entries") or [])}
		total = 0.0
		for rec in recs:
			qty = flt(rec.get("qty"))
			if qty <= 0:
				continue
			entry = entries.get(rec.get("entry")) if rec.get("entry") else None
			if entry is not None:
				undo = min(flt(entry.delivered_qty), qty)
				if undo <= 0:
					continue
				entry.delivered_qty = flt(entry.delivered_qty) - undo
				entry.db_update()
				total += undo
			elif not rec.get("entry"):
				total += qty
		total = min(total, flt(sre_doc.delivered_qty))
		if total <= 0:
			continue
		sre_doc.delivered_qty = flt(sre_doc.delivered_qty) - total
		sre_doc.db_update()
		sre_doc.update_status()
		sre_doc.update_reserved_stock_in_bin()


def _reversible_line_delivery(so_detail, dn_name):
	"""Old DNs (no delivered record): most this DN can have delivered on the
	SO line's reservations = SRE delivered on the line - other DNs' delivery.
	Stops a cancel from wiping an earlier Delivery Note's delivery."""
	from madhav.doc_events.delivery_note import _delivered_qty_excluding_dn

	line_delivered = flt(
		frappe.db.sql(
			"""
			select coalesce(sum(delivered_qty), 0)
			from `tabStock Reservation Entry`
			where docstatus = 1 and voucher_type = 'Sales Order'
			  and voucher_detail_no = %s
			""",
			so_detail,
		)[0][0]
	)
	return max(line_delivered - flt(_delivered_qty_excluding_dn(so_detail, dn_name)), 0)


def _sre_batch_nos(sre_doc):
	if getattr(sre_doc, "reservation_based_on", None) != "Serial and Batch":
		return set()
	return {
		e.batch_no
		for e in (sre_doc.sb_entries or [])
		if e.batch_no
	}


def update_stock_reservation_entries(self) -> None:
	"""Updates Delivered Qty in Stock Reservation Entries.

	Madhav override of ERPNext SellingController.update_stock_reservation_entries:

	1. Prefer Delivery Note Item.custom_sre when set so only the SRE the
	   user kept on the DN is delivered against.
	2. Never mark a sibling reservation Delivered just because it shares the
	   same Sales Order line — same batch OR different batch. Only SREs
	   whose batches appear on THIS DN row (or Qty-based when the DN row
	   has no batch) may receive delivered qty.
	3. Cap delivery with remaining qty_to_deliver.
	"""
	if not frappe.db.get_single_value("Stock Settings", "enable_stock_reservation"):
		return

	if self.is_return:
		return

	so_field = "sales_order" if self.doctype == "Sales Invoice" else "against_sales_order"

	is_dn = self.doctype == "Delivery Note"

	if self._action == "submit":
		# Exactly what this DN delivers on each SRE, so cancel can reverse
		# only that (and never an earlier Delivery Note's delivery).
		delivered_log = []
		for item in self.get("items"):
			if not item.get(so_field) or not item.so_detail:
				continue

			sre_filters = {
				"docstatus": 1,
				"voucher_type": "Sales Order",
				"voucher_no": item.get(so_field),
				"voucher_detail_no": item.so_detail,
				"warehouse": item.warehouse,
				"status": ["not in", ["Delivered", "Cancelled"]],
			}
			explicit_sre = item.get("custom_sre") if hasattr(item, "get") else None
			if explicit_sre:
				sre_filters["name"] = explicit_sre

			sre_list = frappe.db.get_all(
				"Stock Reservation Entry",
				sre_filters,
				order_by="creation",
			)
			if not sre_list:
				continue

			qty_to_deliver = flt(item.stock_qty)
			delivered_batch_qty = _dn_item_delivered_batches(item)
			dn_batches = set(delivered_batch_qty.keys())
			delivered_serial_nos = []
			if item.serial_and_batch_bundle:
				sbb = frappe.get_doc("Serial and Batch Bundle", item.serial_and_batch_bundle)
				delivered_serial_nos = [d.serial_no for d in sbb.entries if d.serial_no]

			for sre in sre_list:
				if qty_to_deliver <= 0:
					break

				sre_doc = frappe.get_doc("Stock Reservation Entry", sre)
				qty_can_be_deliver = 0
				log_len = len(delivered_log)

				if sre_doc.reservation_based_on == "Serial and Batch":
					sre_batches = _sre_batch_nos(sre_doc)
					# Same item, different batch: never touch a sibling SRE
					# whose batches are not on this DN row.
					if dn_batches and not (sre_batches & dn_batches):
						continue

					if sre_doc.has_serial_no and delivered_serial_nos:
						serials_left = list(delivered_serial_nos)
						for entry in sre_doc.sb_entries:
							if qty_to_deliver <= 0:
								break
							if entry.serial_no in serials_left:
								entry.delivered_qty = 1
								entry.db_update()
								delivered_log.append({"sre": sre_doc.name, "entry": entry.name, "qty": 1})
								qty_can_be_deliver += 1
								qty_to_deliver -= 1
								serials_left.remove(entry.serial_no)
								delivered_serial_nos.remove(entry.serial_no)
					elif delivered_batch_qty:
						for entry in sre_doc.sb_entries:
							if qty_to_deliver <= 0:
								break
							if entry.batch_no not in delivered_batch_qty:
								continue
							batch_left = flt(delivered_batch_qty.get(entry.batch_no))
							if batch_left <= 0:
								continue
							delivered_qty = min(
								(flt(entry.qty) - flt(entry.delivered_qty)),
								batch_left,
								qty_to_deliver,
							)
							if delivered_qty <= 0:
								continue
							entry.delivered_qty = flt(entry.delivered_qty) + delivered_qty
							entry.db_update()
							delivered_log.append(
								{"sre": sre_doc.name, "entry": entry.name, "qty": delivered_qty}
							)
							qty_can_be_deliver += delivered_qty
							qty_to_deliver -= delivered_qty
							delivered_batch_qty[entry.batch_no] = batch_left - delivered_qty
					elif not dn_batches:
						# DN row has no batch identity (legacy qty DN). Allow
						# qty fallthrough against Serial-and-Batch SREs.
						qty_can_be_deliver = min(
							(flt(sre_doc.reserved_qty) - flt(sre_doc.delivered_qty)),
							qty_to_deliver,
						)
						qty_to_deliver -= qty_can_be_deliver
					else:
						continue
				else:
					# Qty-based SRE — keep core ERPNext behaviour (deliver by
					# qty). Different-batch sibling protection only applies to
					# Serial-and-Batch reservations above.
					qty_can_be_deliver = min(
						(flt(sre_doc.reserved_qty) - flt(sre_doc.delivered_qty)),
						qty_to_deliver,
					)
					qty_to_deliver -= qty_can_be_deliver

				if qty_can_be_deliver <= 0:
					continue

				if len(delivered_log) == log_len:
					# Header-only delivery (Qty-based SRE / no batch on row).
					delivered_log.append(
						{"sre": sre_doc.name, "entry": None, "qty": qty_can_be_deliver}
					)

				sre_doc.delivered_qty = flt(sre_doc.delivered_qty) + qty_can_be_deliver
				sre_doc.db_update()
				sre_doc.update_status()
				sre_doc.update_reserved_stock_in_bin()

		if is_dn and self.name:
			_store_dn_sre_delivered_log(self.name, delivered_log)

	if self._action == "cancel":
		if is_dn:
			log, comment_name = _load_dn_sre_delivered_log(self.name)
			if log is not None:
				_undeliver_from_log(log)
				frappe.delete_doc("Comment", comment_name, ignore_permissions=True, force=True)
				# Madhav reverse_sre_delivery_for_dn must not undo it again.
				self.flags.madhav_sre_undelivered_from_log = True
				return

		# DN submitted before the delivered record existed: cap per SO line so
		# an earlier Delivery Note's delivery is never reversed.
		reversible_by_detail = {}
		for item in self.get("items"):
			if not item.get(so_field) or not item.so_detail:
				continue

			sre_filters = {
				"docstatus": 1,
				"voucher_type": "Sales Order",
				"voucher_no": item.get(so_field),
				"voucher_detail_no": item.so_detail,
				"warehouse": item.warehouse,
				"status": ["in", ["Partially Delivered", "Delivered"]],
			}
			explicit_sre = item.get("custom_sre") if hasattr(item, "get") else None
			if explicit_sre:
				sre_filters["name"] = explicit_sre

			sre_list = frappe.db.get_all(
				"Stock Reservation Entry",
				sre_filters,
				order_by="creation",
			)
			if not sre_list:
				continue

			qty_to_undelivered = flt(item.stock_qty)
			if is_dn:
				if item.so_detail not in reversible_by_detail:
					reversible_by_detail[item.so_detail] = _reversible_line_delivery(
						item.so_detail, self.name
					)
				qty_to_undelivered = min(qty_to_undelivered, reversible_by_detail[item.so_detail])
				qty_before = qty_to_undelivered
			batch_qty_to_undelivered = _dn_item_delivered_batches(item)
			dn_batches = set(batch_qty_to_undelivered.keys())
			serial_nos_to_undelivered = []
			if item.serial_and_batch_bundle:
				sbb = frappe.get_doc("Serial and Batch Bundle", item.serial_and_batch_bundle)
				serial_nos_to_undelivered = [d.serial_no for d in sbb.entries if d.serial_no]

			for sre in sre_list:
				if qty_to_undelivered <= 0:
					break

				sre_doc = frappe.get_doc("Stock Reservation Entry", sre)
				qty_can_be_undelivered = 0

				if sre_doc.reservation_based_on == "Serial and Batch":
					sre_batches = _sre_batch_nos(sre_doc)
					if dn_batches and not (sre_batches & dn_batches):
						continue

					if sre_doc.has_serial_no and serial_nos_to_undelivered:
						serials_left = list(serial_nos_to_undelivered)
						for entry in sre_doc.sb_entries:
							if qty_to_undelivered <= 0:
								break
							if entry.serial_no in serials_left:
								entry.delivered_qty = 0
								entry.db_update()
								qty_can_be_undelivered += 1
								qty_to_undelivered -= 1
								serials_left.remove(entry.serial_no)
								serial_nos_to_undelivered.remove(entry.serial_no)
					elif batch_qty_to_undelivered:
						for entry in sre_doc.sb_entries:
							if qty_to_undelivered <= 0:
								break
							if entry.batch_no not in batch_qty_to_undelivered:
								continue
							batch_left = flt(batch_qty_to_undelivered.get(entry.batch_no))
							if batch_left <= 0:
								continue
							undelivered_qty = min(
								flt(entry.delivered_qty),
								batch_left,
								qty_to_undelivered,
							)
							if undelivered_qty <= 0:
								continue
							entry.delivered_qty = flt(entry.delivered_qty) - undelivered_qty
							entry.db_update()
							qty_can_be_undelivered += undelivered_qty
							qty_to_undelivered -= undelivered_qty
							batch_qty_to_undelivered[entry.batch_no] = batch_left - undelivered_qty
					elif not dn_batches:
						qty_can_be_undelivered = min(
							flt(sre_doc.delivered_qty), qty_to_undelivered
						)
						qty_to_undelivered -= qty_can_be_undelivered
					else:
						continue
				else:
					# Qty-based — same as core ERPNext undeliver-by-qty.
					qty_can_be_undelivered = min(flt(sre_doc.delivered_qty), qty_to_undelivered)
					qty_to_undelivered -= qty_can_be_undelivered

				if qty_can_be_undelivered <= 0:
					continue

				sre_doc.delivered_qty = flt(sre_doc.delivered_qty) - qty_can_be_undelivered
				sre_doc.db_update()
				sre_doc.update_status()
				sre_doc.update_reserved_stock_in_bin()

			if is_dn:
				reversible_by_detail[item.so_detail] = max(
					reversible_by_detail[item.so_detail] - (qty_before - qty_to_undelivered), 0
				)
