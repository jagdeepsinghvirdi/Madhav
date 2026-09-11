# Copyright (c) 2026, Finbyz pvt. ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now, nowdate, nowtime, flt, cint
from erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry import get_available_qty_to_reserve

class FinishWorkOrder(Document):
    def on_cancel(self):
        for row in self.pending_work_orders:
            sre_names = frappe.get_all(
                "Stock Reservation Entry",
                filters={
                    "from_voucher_type": self.doctype,
                    "from_voucher_no": self.name,
                    "from_voucher_detail_no": row.name,
                    "docstatus": 1,
                },
                pluck="name",
            )
            for sre_name in sre_names:
                sre = frappe.get_doc("Stock Reservation Entry", sre_name)
                sre.cancel()
                sre.db_set("from_voucher_no", "")

            if row.stock_entry_reference:
                se = frappe.get_doc("Stock Entry", row.stock_entry_reference)
            if se.docstatus == 1:
                se.flags.ignore_links = True
                se.cancel()
            row.db_set("stock_entry_reference", "")

        if row.make_it_unplanned == 1:
            if frappe.db.exists("Work Order", row.work_order):
                wo = frappe.get_doc("Work Order", row.work_order)
                if wo.docstatus == 1:
                    wo.flags.ignore_links = True
                    wo.cancel()
            row.db_set("work_order", "")


    def on_update(self):
        if self.docstatus == 0:
            self.create_unplanned_work_orders()
        
    def after_insert(self):
        for row in self.pending_work_orders:
            if row.sales_order:
                doc = frappe.get_doc("Sales Order",row.sales_order)
                if doc.quality_required and not row.make_it_unplanned:
                    row.target_warehouse = frappe.db.get_value("Company",self.company,"default_quality_inspection_warehouse")

    def validate(self):
        self.update_totals()
        for row in self.raw_materials:
            has_batch_no = frappe.get_cached_value("Item", row.item_code, "has_batch_no")
            if has_batch_no and not row.batch_no:
                frappe.throw(
                    frappe._("Row #{0}: Batch No is required for item {1} (batch-tracked item).").format(
                        row.idx, row.item_code
                    )
                )
        for row in self.pending_work_orders:
            if row.ready_qty and row.ready_pieces and row.length_size:
                row.calculated_section_weight = (flt(row.ready_qty) * 1000)/(flt(row.ready_pieces) * flt(row.length_size))
            row.calculated_qty = (flt(row.ready_pieces) * flt(row.standard_weight) * flt(row.length_size)) /1000
            if not row.sales_order:
                row.deliver_as_qty = 1
            if row.make_it_unplanned:
                row.qty = row.ready_qty
                row.pieces = row.ready_pieces

    def update_totals(self):
        total_wo_qty = 0
        total_wo_pieces = 0
        total_rm_qty = 0
        total_rm_pieces = 0
        total_scrap_qty = 0
        
        for row in self.pending_work_orders:
            total_wo_qty += row.ready_qty or 0
            total_wo_pieces += row.ready_pieces or 0
            
        for row in self.raw_materials:
            total_rm_qty += row.qty or 0
            total_rm_pieces += row.pieces or 0
            
        for row in self.scrap_items:
            total_scrap_qty += row.qty or 0
            
        self.total_work_order_qty = total_wo_qty
        self.total_work_order_pieces = total_wo_pieces
        self.total_raw_material_qty = total_rm_qty
        self.total_raw_material_pieces = total_rm_pieces
        self.scrap_qty = total_scrap_qty
        
        # Prevent division by zero
        if total_wo_qty:
            self.scrap_ratio = total_scrap_qty / total_wo_qty
        else:
            self.scrap_ratio = 0

        if (total_wo_qty ):
            self.consumption_ratio = total_rm_qty / (total_wo_qty)
        else:
            self.consumption_ratio = 0

        # Update child table fields
        for row in self.pending_work_orders:
            ready_qty = row.ready_qty or 0

            row.consumption = ready_qty * self.consumption_ratio
            row.scrap_qty = ready_qty * self.scrap_ratio

    def create_unplanned_work_orders(self):

        for row in self.pending_work_orders:

            if row.make_it_unplanned and not row.work_order:

                new_wo = self.create_new_work_order(row)
                row.db_set("work_order", new_wo)

    def create_new_work_order(self, row):

        wo = frappe.new_doc("Work Order")

        # ---- Core Fields ----
        wo.production_item = row.item
        wo.qty = row.ready_qty
        wo.stock_uom = row.stock_uom
        wo.company = self.company
        wo.fg_warehouse = row.target_warehouse
        bom = frappe.get_value("BOM", {"item": row.item, "is_default": 1}, "name")
        wo.bom_no = bom

        # ---- Custom / Extended Fields ----
        wo.length = row.length_size
        wo.pieces = row.ready_pieces

        # ---- Optional Clean Defaults ----
        wo.skip_transfer = 1

        # ---- Reference Tracking ----
        wo.custom_reference_wo = row.old_work_order
        wo.custom_finish_doc = self.name

        wo.insert(ignore_permissions=True)
        wo.submit()

        return wo.name

    def create_fg_stock_reservation(
        self,
        item_code,
        warehouse,
        qty,
        so_qty,
        name,
        stock_uom,
        work_order,
        sales_order=None,
        sales_order_item=None,
        batch_no=None,
        quality_required=False,
        from_voucher_type=None,
        from_voucher_no=None,
        from_voucher_detail_no=None,
    ):
        from madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool import (
            get_reservation_ceiling,
            floor_qty,
        )

        if not sales_order:
            return
        if quality_required:
            frappe.log_error(
                title="Quality Inspection Required - Skipping Stock Reservation",
                message=f"Skipping stock reservation for {item_code} in WO {work_order} linked to SO {sales_order} because quality inspection is required."
            )
            return
        if frappe.db.get_value("Work Order", work_order, "fg_warehouse") != warehouse:
            return

        remaining_qty = floor_qty(flt(qty), 3)
        created_any = False
        limited_by_physical_stock = False

        # Pass 1: BASE (is_tolerance=0) — capped at the SO line's own
        # remaining pending qty, same as before.
        # Pass 2: TOLERANCE (is_tolerance=1) — only runs on whatever base
        # couldn't absorb, capped by the SHARED SO-level tolerance pool
        # (get_used_tolerance_qty / get_tolerance_pool inside
        # get_reservation_ceiling). This is what PDF Section 7/8 requires:
        # produced qty that exceeds the line's own order can still be
        # reserved, as long as the SO-wide 20% pool has room.
        for is_tolerance in (0, 1):
            if remaining_qty <= 0:
                break

            limits = get_reservation_ceiling(
                item_code=item_code,
                warehouse=warehouse,
                sales_order=sales_order,
                sales_order_item=sales_order_item,
                is_tolerance=is_tolerance,
                batch_no=batch_no,
            )

            reserve_qty = min(remaining_qty, limits.allowed_qty)
            if reserve_qty <= 0:
                if limits.item_level_available_qty <= 0:
                    limited_by_physical_stock = True
                continue

            self._create_single_fg_sre(
                item_code=item_code,
                warehouse=warehouse,
                so_qty=so_qty,
                stock_uom=stock_uom,
                sales_order=sales_order,
                so_detail=limits.so_item.name,
                batch_no=batch_no,
                is_tolerance=is_tolerance,
                reserve_qty=reserve_qty,
                available_qty=limits.allowed_qty,
                from_voucher_type=from_voucher_type,
                from_voucher_no=from_voucher_no,
                from_voucher_detail_no=from_voucher_detail_no,
            )
            created_any = True
            remaining_qty = floor_qty(remaining_qty - reserve_qty, 3)

        if remaining_qty > 0:
            if limited_by_physical_stock and not created_any:
                frappe.throw(
                    f"Item {item_code} has no available stock to reserve in warehouse {warehouse}"
                    + (f" for batch {batch_no}" if batch_no else "")
                )
            # Normal case: base + tolerance pool both exhausted. Excess
            # stays physical-only, same as the pre-fix behavior for the
            # base-only case — just logged, not an error.
            frappe.log_error(
                title="FWO Reservation - Excess Unreserved",
                message=(
                    f"SO: {sales_order}, Item: {item_code}, Work Order: {work_order}. "
                    f"Produced {qty}, only {flt(qty) - remaining_qty} could be reserved "
                    f"(base + tolerance exhausted). {remaining_qty} remains as unreserved physical stock."
                ),
            )

    def _create_single_fg_sre(
        self, item_code, warehouse, so_qty, stock_uom, sales_order, so_detail,
        batch_no, is_tolerance, reserve_qty, available_qty,
        from_voucher_type, from_voucher_no, from_voucher_detail_no,
    ):
        from madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool import (
            calc_proportional_pieces,
        )

        has_batch_no = frappe.get_cached_value("Item", item_code, "has_batch_no")

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
        sre.custom_is_tolerance = cint(is_tolerance)
        sre.reserved_qty = flt(reserve_qty, 3)
        sre.voucher_qty = flt(so_qty)
        sre.available_qty = flt(available_qty, 3)
        sre.available_qty_to_reserve = flt(reserve_qty, 3)

        if batch_no and has_batch_no and reserve_qty > 0:
            sre.has_batch_no = 1
            sre.has_serial_no = 0
            sre.reservation_based_on = "Serial and Batch"
            sre.use_serial_batch_fields = 1
            sre.append("sb_entries", {
                "batch_no": batch_no,
                "qty": reserve_qty,
                "warehouse": warehouse,
                "pieces": calc_proportional_pieces(reserve_qty, batch_no),
                "length": frappe.db.get_value("Batch", batch_no, "average_length") or 0,
                "section_weight": frappe.db.get_value("Batch", batch_no, "section_weight") or 0,
            })
            sre.auto_reserve_serial_and_batch = lambda *args, **kwargs: None
        else:
            sre.reservation_based_on = "Qty"

        sre.flags.ignore_permissions = True
        sre.insert()
        sre.submit()

        frappe.log_error(
            title="Stock Reserved",
            message=f"Reserved {reserve_qty} of {item_code} in {warehouse} for SO {sales_order} "
                    f"(Batch: {batch_no}, tolerance: {cint(is_tolerance)}, source: FWO)"
        )

    def update_remarks_from_so(self):
        for row in self.pending_work_orders:
            if row.sales_order and row.work_order:
                soi = frappe.db.get_value("Work Order", row.work_order, "sales_order_item")
                frappe.db.set_value("Sales Order Item", soi, "remarks", row.remarks or "")

    def before_submit(self):
        self.update_remarks_from_so()

        # Holds data collected while creating Stock Entries so on_submit
        # can create the Stock Reservation Entries afterwards.
        self._fg_reservation_data = []

        # ==============================
        # BUILD FIFO RAW MATERIAL POOL
        # ==============================
        rm_pool = []
        for rm in self.raw_materials:
            if rm.qty and rm.qty > 0:
                rm_pool.append({
                    "item_code": rm.item_code,
                    "warehouse": rm.source_warehouse,
                    "remaining_qty": rm.qty,
                    "batch_no": rm.batch_no,
                    "pieces": rm.pieces,
                    "length": rm.length,
                    "section_weight": rm.section_weight,
                    "cost_center": self.cost_center,  # ✅ FIXED: Removed extra colon
                    "branch": self.branch
                })

        rm_index = 0

        # ==============================
        # BUILD FIFO SCRAP POOL
        # ==============================
        scrap_pool = []
        for sc in self.scrap_items:
            if sc.qty and sc.qty > 0:
                scrap_pool.append({
                    "item_code": sc.item,
                    "remaining_qty": sc.qty,
                    "warehouse": sc.warehouse
                })

        scrap_index = 0

        precision = frappe.get_precision("Stock Entry Detail", "qty")

        # ==============================
        # ITERATE PENDING WORK ORDERS
        # ==============================
        for pwo in self.pending_work_orders:

            fg_qty = flt(pwo.consumption or 0, precision)
            scrap_required = flt(pwo.scrap_qty or 0, precision)

            if fg_qty <= 0:
                continue

            if not pwo.work_order:
                frappe.throw(
                    f"Row {pwo.idx}: Work Order is not set for item <b>{pwo.item}</b>."
                )

            try:
                # ==============================
                # CREATE STOCK ENTRY
                # ==============================
                se = frappe.new_doc("Stock Entry")
                se.stock_entry_type = "Manufacture"
                se.company = self.company
                se.work_order = pwo.work_order
                se.from_bom = 1
                se.finished_good = pwo.item
                se.fg_completed_qty = pwo.ready_qty if pwo.deliver_as_qty else pwo.calculated_qty
                se.finished_good_quantity = pwo.ready_qty if pwo.deliver_as_qty else pwo.calculated_qty

                # ✅ FIXED: Set BOTH posting_date AND posting_time
                se.set_posting_time = 1
                se.posting_date = self.posting_date or nowdate()
                # se.posting_time = self.posting_time or nowtime()  # ✅ UNCOMMENTED

                se.flags.ignore_permissions = True
                se.flags.ignore_mandatory = True
                se.flags.ignore_bom_validation = True
                se.flags.ignore_work_order_validation = True

                remaining_fg_qty = fg_qty

                # ==============================
                # FIFO RAW MATERIAL CONSUMPTION
                # ==============================
                while remaining_fg_qty > 0.0001 and rm_index < len(rm_pool):
                    rm_row = rm_pool[rm_index]

                    if rm_row["remaining_qty"] <= 0:
                        rm_index += 1
                        continue

                    consume = flt(min(remaining_fg_qty, rm_row["remaining_qty"]), precision)

                    is_last = consume >= rm_row["remaining_qty"] - 0.0001

                    se.append("items", {
                        "item_code": rm_row["item_code"],
                        "s_warehouse": rm_row["warehouse"],
                        "qty": consume,
                        "pieces": rm_row["pieces"] if is_last else 0,
                        "average_length": rm_row["length"] if is_last else 0,
                        "section_weight": rm_row["section_weight"],
                        "batch_no": rm_row["batch_no"],
                        "required_stock_in_pieces": 0,
                        "use_serial_batch_fields": 1,
                        "cost_center": rm_row["cost_center"],  # ✅ FIXED: Removed extra colon
                        "branch": rm_row["branch"]
                    })

                    rm_row["remaining_qty"] -= consume
                    remaining_fg_qty -= consume

                    if rm_row["remaining_qty"] <= 0.0001:
                        rm_row["remaining_qty"] = 0
                        rm_index += 1

                if round(remaining_fg_qty, 2) > 0:
                    frappe.throw(
                        f"Row {pwo.idx}: Raw material pool exhausted. Need {remaining_fg_qty}"
                    )

                # ==============================
                # FIFO SCRAP
                # ==============================
                remaining_scrap = scrap_required

                while remaining_scrap > 0.0001 and scrap_index < len(scrap_pool):
                    sc_row = scrap_pool[scrap_index]

                    if sc_row["remaining_qty"] <= 0:
                        scrap_index += 1
                        continue

                    consume_scrap = flt(min(remaining_scrap, sc_row["remaining_qty"]), precision)

                    se.append("items", {
                        "item_code": sc_row["item_code"],
                        "t_warehouse": sc_row["warehouse"],
                        "qty": consume_scrap,
                        "is_scrap_item": 1,
                        "required_stock_in_pieces": 1,
                        "use_serial_batch_fields": 1,
                        "cost_center": self.cost_center,
                        "branch": self.branch
                    })

                    sc_row["remaining_qty"] -= consume_scrap
                    remaining_scrap -= consume_scrap

                    if sc_row["remaining_qty"] <= 0.0001:
                        sc_row["remaining_qty"] = 0
                        scrap_index += 1

                # ==============================
                # FINISHED GOOD
                # ==============================
                fg_final_qty = round(
                    pwo.ready_qty if pwo.deliver_as_qty == 1 else pwo.calculated_qty, 3
                )

                se.append("items", {
                    "item_code": pwo.item,
                    "t_warehouse": pwo.target_warehouse,
                    "qty": fg_final_qty,
                    "pieces": pwo.ready_pieces,
                    "average_length": pwo.length_size,
                    "section_weight": pwo.standard_weight,
                    "assorted_length": pwo.assorted_length,
                    "is_finished_item": 1,
                    "required_stock_in_pieces": 1,
                    "cost_center": self.cost_center,
                    "branch": self.branch
                })

                se.fg_completed_qty = fg_final_qty

                # Submit Stock Entry
                se.insert()
                se.submit()
                pwo.db_set("stock_entry_reference", se.name)
                
                # ==============================
                # GET FG BATCH FROM STOCK ENTRY
                # ==============================
                fg_batch_no = None
                fg_bundle_name = None
                
                # Reload the submitted Stock Entry to get all details
                submitted_se = frappe.get_doc("Stock Entry", se.name)
                
                for item in submitted_se.items:
                    if item.is_finished_item:
                        # Check if serial_and_batch_bundle exists
                        if item.serial_and_batch_bundle:
                            fg_bundle_name = item.serial_and_batch_bundle
                            
                            try:
                                # Get the bundle document
                                bundle = frappe.get_doc("Serial and Batch Bundle", fg_bundle_name)
                                
                                # Extract batch from bundle entries
                                if bundle.entries and len(bundle.entries) > 0:
                                    for entry in bundle.entries:
                                        if entry.batch_no:
                                            fg_batch_no = entry.batch_no
                                            break
                                    
                            except Exception as bundle_error:
                                frappe.log_error(
                                    title=f"Error getting batch from bundle {fg_bundle_name}",
                                    message=str(bundle_error)
                                )
                        
                        break  # Stop after finding the finished item

                # ==============================
                # QUEUE STOCK RESERVATION DATA
                # ==============================
                self._fg_reservation_data.append({
                    "item_code": pwo.item,
                    "warehouse": pwo.target_warehouse,
                    "qty": fg_final_qty,
                    "so_qty": pwo.sales_order_qty,
                    "name": pwo.name,
                    "stock_uom": pwo.stock_uom,
                    "work_order": pwo.work_order,
                    "sales_order": pwo.sales_order,
                    "sales_order_item": frappe.db.get_value("Work Order", pwo.work_order, "sales_order_item"),
                    "batch_no": fg_batch_no,
                    "quality_required": pwo.quality_required,
                    "from_voucher_type": self.doctype,
                    "from_voucher_no": self.name,
                    "from_voucher_detail_no": pwo.name
                })

            except Exception as e:
                frappe.log_error(
                    title=f"Stock Entry Failed for WO {pwo.work_order}",
                    message=frappe.get_traceback()
                )
                # A submitted FWO without its corresponding manufacture
                # stock/SRE leaves the SO reservation state unknowable.
                # Do not log and continue with a partially completed FWO.
                frappe.throw(
                    frappe._("Could not create finished stock for Work Order {0}: {1}").format(
                        pwo.work_order, str(e)
                    )
                )

            # ==============================
            # UPDATE WORK ORDER PCS
            # ==============================
            doc = frappe.get_doc("Work Order", pwo.work_order)
            completed = flt(doc.completed_pcs or 0)
            total = flt(doc.pieces or 0)
            pcs = flt(pwo.ready_pieces or 0)
            doc.db_set("completed_pcs", completed + pcs)
            doc.db_set("pending_pcs", total - (completed + pcs))
            ready_qty  = fg_final_qty
            doc.db_set("pending_qty", flt(doc.qty or 0) -  flt(ready_qty or 0))
        # ==============================
        # EXCESS RM
        # ==============================
        excess = sum([rm["remaining_qty"] for rm in rm_pool if rm["remaining_qty"] > 0])
        self.db_set("excess_rm_qty", flt(excess, precision))

    def on_submit(self):
        # By this point every Stock Entry (Manufacture) has already been
        # created and submitted in before_submit. Now that this document
        # itself is submitted, create the Stock Reservation Entries.
        for data in getattr(self, "_fg_reservation_data", []):
            self.create_fg_stock_reservation(
                item_code=data["item_code"],
                warehouse=data["warehouse"],
                qty=data["qty"],
                so_qty=data["so_qty"],
                name=data["name"],
                stock_uom=data["stock_uom"],
                work_order=data["work_order"],
                sales_order=data["sales_order"],
                sales_order_item=data["sales_order_item"],
                batch_no=data["batch_no"],
                quality_required=data["quality_required"],
                from_voucher_type=data["from_voucher_type"],
                from_voucher_no=data["from_voucher_no"],
                from_voucher_detail_no=data["from_voucher_detail_no"]
            )

@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_available_batches(doctype, txt, searchfield, start, page_len, filters):

    item_code = filters.get("item_code")
    warehouse = filters.get("warehouse")
    supplier = filters.get("supplier")

    if not item_code or not warehouse:
        return []

    supplier_condition = ""
    if supplier:
        supplier_condition = " AND pr.supplier = %(supplier)s "

    return frappe.db.sql(f"""
        SELECT
            sbe.batch_no,
            CONCAT(
                ROUND(SUM(sbe.qty - IFNULL(sbe.delivered_qty, 0)), 3),
                ', ',
                sabb.posting_date,
                ', ',
                sabb.voucher_no
            ) as description

        FROM
            `tabSerial and Batch Entry` sbe

        INNER JOIN
            `tabSerial and Batch Bundle` sabb
            ON sabb.name = sbe.parent

        INNER JOIN
            `tabBatch` b
            ON b.name = sbe.batch_no

        LEFT JOIN
            `tabPurchase Receipt` pr
            ON pr.name = b.reference_name
            AND b.reference_doctype = 'Purchase Receipt'

        WHERE
            sabb.item_code = %(item_code)s
            AND sbe.warehouse = %(warehouse)s
            AND sabb.is_cancelled = 0
            AND sbe.batch_no LIKE %(txt)s
            {supplier_condition}

        GROUP BY
            sbe.batch_no

        HAVING
            SUM(sbe.qty - IFNULL(sbe.delivered_qty, 0)) > 0

        ORDER BY
            sabb.posting_date ASC

        LIMIT %(start)s, %(page_len)s
    """, {
        "item_code": item_code,
        "warehouse": warehouse,
        "supplier": supplier,
        "txt": f"%{txt}%",
        "start": start,
        "page_len": page_len
    })


@frappe.whitelist()
def get_bom_raw_materials_for_items(items):
    """
    items: JSON list of {"item_code": <FG item>, "qty": <ready_qty>}

    For each FG item, pulls its default submitted BOM, scales BOM Item
    quantities to the given qty, and aggregates by raw material item_code
    across all items passed in (so two WOs needing the same RM produce
    one combined row, matching how the FIFO pool in before_submit expects it).
    """
    frappe.logger().info(f"RM FETCH CALLED WITH: {items}")
    import json
    from frappe.utils import flt

    if isinstance(items, str):
        items = json.loads(items)

    aggregated = {}

    for entry in items:
        item_code = entry.get("item_code")
        qty = flt(entry.get("qty"))
        if not item_code or qty <= 0:
            continue

        bom_name = frappe.db.get_value(
            "BOM",
            {"item": item_code, "is_default": 1, "docstatus": 1},
            "name"
        )
        if not bom_name:
            frappe.log_error(
                title="Missing Default BOM",
                message=f"No default submitted BOM for item {item_code} "
                        f"while auto-fetching raw materials for Finish Work Order."
            )
            continue

        bom = frappe.get_cached_doc("BOM", bom_name)
        bom_qty = flt(bom.quantity) or 1

        for bom_item in bom.items:
            required_qty = flt(bom_item.qty) * qty / bom_qty
            aggregated[bom_item.item_code] = aggregated.get(bom_item.item_code, 0) + required_qty

    return [
        {"item_code": item_code, "qty": flt(qty, 3)}
        for item_code, qty in aggregated.items()
    ]