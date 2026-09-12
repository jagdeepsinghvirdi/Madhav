import frappe
from erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry import StockReservationEntry as _StockReservationEntry,get_available_qty_to_reserve,get_sre_reserved_qty_for_voucher_detail_no,get_available_serial_nos_to_reserve
from frappe import _
from frappe.utils import cint, flt
from erpnext.stock.utils import get_stock_balance

class StockReservationEntry(_StockReservationEntry):
    def update_status(self, status: str | None = None, update_modified: bool = True) -> None:
            """Updates status based on Voucher Qty, Reserved Qty and Delivered Qty."""
            over_reservation_allowance = flt(
                frappe.db.get_single_value(
                    "Stock Settings",
                    "over_reservation_allowance"
                ) or 0
            )
    
            max_voucher_qty = self.voucher_qty * (
                1 + over_reservation_allowance / 100
            )
            if not status:
                if self.docstatus == 2:
                    status = "Cancelled"
                elif self.docstatus == 1:
                    if self.reserved_qty == self.delivered_qty:
                        status = "Delivered"
                    elif self.delivered_qty and self.delivered_qty < self.reserved_qty:
                        status = "Partially Delivered"
                    elif self.reserved_qty >= max_voucher_qty:
                        status = "Reserved"
                    else:
                        status = "Partially Reserved"
                else:
                    status = "Draft"
    
            frappe.db.set_value(self.doctype, self.name, "status", status, update_modified=update_modified)
    def validate_with_allowed_qty(self, qty_to_be_reserved: float) -> None:
            """Validates `Reserved Qty` with `Max Reserved Qty`."""
    
            self.db_set(
                "available_qty",
                get_available_qty_to_reserve(self.item_code, self.warehouse, ignore_sre=self.name),
            )
    
            voucher_delivered_qty = 0
            if self.voucher_type == "Sales Order":
                delivered_qty, conversion_factor = frappe.db.get_value(
                    "Sales Order Item",
                    self.voucher_detail_no,
                    ["delivered_qty", "conversion_factor"],
                )
                voucher_delivered_qty = flt(delivered_qty) * flt(conversion_factor)
    
            # BWRT tolerance is validated by its shared SO-level pool.
            # It is intentionally not subject to ERPNext's per-line 20%
            # cap.  Every base SO reservation uses the same active
            # definition as BWRT/FWO: SUM(reserved_qty - delivered_qty),
            # excluding tolerance rows and this SRE.  The previous generic
            # formula summed gross reservations and also subtracted the SO
            # delivered qty, so a finished FWO changed BWRT's answer.
            if self.from_voucher_type == "Batch Wise Reservation Tool" and cint(self.get("custom_is_tolerance")):
                allowed_qty = self.available_qty
                total_reserved_qty = 0
            else:
                total_reserved_qty = get_sre_reserved_qty_for_voucher_detail_no(
                    self.voucher_type, self.voucher_no, self.voucher_detail_no,
                    ignore_sre=self.name,
                )
                if self.voucher_type == "Sales Order":
                    # Same shared SO-level allowance BWRT/FWO/Stock Transfer
                    # use: the line's own remaining base qty plus whatever is
                    # left of the SO-wide tolerance pool, capped by
                    # (pending SO qty + tolerance - already reserved).  A
                    # submitted FWO consumes the line's base qty only, so it
                    # must not make the leftover tolerance unreservable here.
                    from madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool import (
                        get_so_line_allowance,
                    )
                    so_available_qty = get_so_line_allowance(
                        self.voucher_no,
                        self.voucher_detail_no,
                        is_tolerance=cint(self.get("custom_is_tolerance")),
                        exclude_sre=self.name,
                    )
                    allowed_qty = min(self.available_qty, so_available_qty)
                else:
                    over_reservation_allowance = flt(
                        frappe.db.get_single_value("Stock Settings", "over_reservation_allowance") or 0
                    )
                    max_voucher_qty = self.voucher_qty * (1 + over_reservation_allowance / 100)
                    allowed_qty = min(
                        self.available_qty,
                        max_voucher_qty - voucher_delivered_qty - total_reserved_qty,
                    )
            allowed_qty = flt(allowed_qty, self.precision("reserved_qty"))
            qty_to_be_reserved = flt(qty_to_be_reserved, self.precision("reserved_qty"))
    
            if self.get("_action") != "submit" and self.voucher_type == "Sales Order" and allowed_qty <= 0:
                msg = _("Item {0} is already reserved/delivered against Sales Order {1}.").format(
                    frappe.bold(self.item_code), frappe.bold(self.voucher_no)
                )
    
                if self.docstatus == 1:
                    self.cancel()
                    return frappe.msgprint(msg)
                else:
                    frappe.throw(msg)
    
            if qty_to_be_reserved > allowed_qty:
                actual_qty = get_stock_balance(self.item_code, self.warehouse)
                msg = """
                    Cannot reserve more than Allowed Qty {} {} for Item {} against {} {}.<br /><br />
                    The <b>Allowed Qty</b> is calculated as follows:<br />
                    <ul>
                        <li>Actual Qty [Available Qty at Warehouse] = {}</li>
                        <li>Reserved Stock [Ignore current SRE] = {}</li>
                        <li>Available Qty To Reserve [Actual Qty - Reserved Stock] = {}</li>
                        <li>Voucher Qty [Voucher Item Qty] = {}</li>
                        <li>Delivered Qty [Qty delivered against the Voucher Item] = {}</li>
                        <li>Total Reserved Qty [Qty reserved against the Voucher Item] = {}</li>
                        <li>Allowed Qty [Minimum of (Available Qty To Reserve, (Voucher Qty - Delivered Qty - Total Reserved Qty))] = {}</li>
                    </ul>
                """.format(
                    frappe.bold(allowed_qty),
                    self.stock_uom,
                    frappe.bold(self.item_code),
                    self.voucher_type,
                    frappe.bold(self.voucher_no),
                    actual_qty,
                    actual_qty - self.available_qty,
                    self.available_qty,
                    self.voucher_qty,
                    voucher_delivered_qty,
                    total_reserved_qty,
                    allowed_qty,
                )
                frappe.throw(msg)
    
            if qty_to_be_reserved <= self.delivered_qty:
                msg = _("Reserved Qty should be greater than Delivered Qty.")
                frappe.throw(msg)
    
    def validate_reservation_based_on_serial_and_batch(self) -> None:
            """Validates `Reserved Qty`, `Serial and Batch Nos` when `Reservation Based On` is `Serial and Batch`."""
    
            if self.reservation_based_on == "Serial and Batch":
                allow_partial_reservation = frappe.db.get_single_value(
                    "Stock Settings", "allow_partial_reservation"
                )
    
                available_serial_nos = []
                if self.has_serial_no:
                    available_serial_nos = get_available_serial_nos_to_reserve(
                        self.item_code, self.warehouse, self.has_batch_no, ignore_sre=self.name
                    )
    
                    if not available_serial_nos:
                        msg = _("Stock not available for Item {0} in Warehouse {1}.").format(
                            frappe.bold(self.item_code), frappe.bold(self.warehouse)
                        )
                        frappe.throw(msg)
    
                qty_to_be_reserved = 0
                selected_batch_nos, selected_serial_nos = [], []
                for entry in self.sb_entries:
                    entry.warehouse = self.warehouse
    
                    if self.has_serial_no:
                        entry.qty = 1
    
                        key = (
                            (entry.serial_no, self.warehouse, entry.batch_no)
                            if self.has_batch_no
                            else (entry.serial_no, self.warehouse)
                        )
                        if key not in available_serial_nos:
                            msg = _(
                                "Row #{0}: Serial No {1} for Item {2} is not available in {3} {4} or might be reserved in another {5}."
                            ).format(
                                entry.idx,
                                frappe.bold(entry.serial_no),
                                frappe.bold(self.item_code),
                                _("Batch {0} and Warehouse").format(frappe.bold(entry.batch_no))
                                if self.has_batch_no
                                else _("Warehouse"),
                                frappe.bold(self.warehouse),
                                frappe.bold(_("Stock Reservation Entry")),
                            )
    
                            frappe.throw(msg)
    
                        if entry.serial_no in selected_serial_nos:
                            msg = _("Row #{0}: Serial No {1} is already selected.").format(
                                entry.idx, frappe.bold(entry.serial_no)
                            )
                            frappe.throw(msg)
                        else:
                            selected_serial_nos.append(entry.serial_no)
    
                    elif self.has_batch_no:
                        if cint(frappe.db.get_value("Batch", entry.batch_no, "disabled")):
                            msg = _(
                                "Row #{0}: Stock cannot be reserved for Item {1} against a disabled Batch {2}."
                            ).format(entry.idx, frappe.bold(self.item_code), frappe.bold(entry.batch_no))
                            frappe.throw(msg)
    
                        available_qty_to_reserve = get_available_qty_to_reserve(
                            self.item_code, self.warehouse, entry.batch_no, ignore_sre=self.name
                        )
    
                        if available_qty_to_reserve <= 0:
                            msg = _(
                                "Row #{0}: Stock not available to reserve for Item {1} against Batch {2} in Warehouse {3}."
                            ).format(
                                entry.idx,
                                frappe.bold(self.item_code),
                                frappe.bold(entry.batch_no),
                                frappe.bold(self.warehouse),
                            )
                            frappe.throw(msg)
    
                        if entry.qty > available_qty_to_reserve:
                            if allow_partial_reservation:
                                entry.qty = available_qty_to_reserve
                                if self.get("_action") == "update_after_submit":
                                    entry.db_update()
                            else:
                                msg = _(
                                    "Row #{0}: Qty should be less than or equal to Available Qty to Reserve (Actual Qty - Reserved Qty) {1} for Iem {2} against Batch {3} in Warehouse {4}."
                                ).format(
                                    entry.idx,
                                    frappe.bold(available_qty_to_reserve),
                                    frappe.bold(self.item_code),
                                    frappe.bold(entry.batch_no),
                                    frappe.bold(self.warehouse),
                                )
                                frappe.throw(msg)
    
                        if entry.batch_no in selected_batch_nos:
                            msg = _("Row #{0}: Batch No {1} is already selected.").format(
                                entry.idx, frappe.bold(entry.batch_no)
                            )
                            frappe.throw(msg)
                        else:
                            selected_batch_nos.append(entry.batch_no)
    
                    qty_to_be_reserved += entry.qty
    
                if not qty_to_be_reserved:
                    msg = _("Please select Serial/Batch Nos to reserve or change Reservation Based On to Qty.")
                    frappe.throw(msg)
    
                # Should be called after validating Serial and Batch Nos.
                self.validate_with_allowed_qty(qty_to_be_reserved)
                self.db_set("reserved_qty", qty_to_be_reserved)
