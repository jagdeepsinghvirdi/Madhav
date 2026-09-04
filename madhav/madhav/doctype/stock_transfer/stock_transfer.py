# Copyright (c) 2026, Finbyz pvt. ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt
from frappe.model.document import Document


def resolve_sre_sb_dimensions(pieces=None, length=None, section_weight=None, batch_vals=None):
	"""Pick Pcs/Length/section_weight for SRE sb_entries from transfer row, else Batch.

	Transfer tonne qty is reserved separately — never derive reserved_qty from
	Item.weight_per_meter × SO pieces (that produced ~0.788 for 1T transfers).
	"""
	batch_vals = batch_vals or {}
	entry_pieces = flt(pieces)
	entry_length = flt(length)
	entry_section_weight = flt(section_weight)

	if not entry_pieces:
		entry_pieces = flt(batch_vals.get("pieces") or 0)
	if not entry_length:
		entry_length = flt(batch_vals.get("average_length") or batch_vals.get("length") or 0)
	if not entry_section_weight:
		entry_section_weight = flt(batch_vals.get("section_weight") or 0)

	return entry_pieces, entry_length, entry_section_weight


def _cancel_psles_for_voucher(voucher_no):
	"""Cancel Piece Stock Ledger Entries for a Stock Entry so warehouse pieces roll back."""
	if not voucher_no:
		return
	psles = frappe.get_all(
		"Piece Stock Ledger Entry",
		filters={"voucher_no": voucher_no, "docstatus": 1},
		pluck="name",
	)
	for psle_name in psles:
		psle = frappe.get_doc("Piece Stock Ledger Entry", psle_name)
		psle.flags.ignore_permissions = True
		psle.flags.ignore_links = True
		psle.cancel()


class StockTransfer(Document):

    def on_cancel(self):
        """Unreserve stock, then reverse Material Transfer back to source warehouse.

        Works for any source → target warehouse pair (not tied to specific WH names).

        Critical: linked ``stock_entry`` must be cancelled. Historically
        ``db.set_value`` during ``before_submit`` was overwritten on submit save,
        leaving ``stock_entry`` blank — cancel then skipped the SE and stock
        stayed in the target warehouse.
        """
        errors = []

        # 1) Cancel every active SRE created from this Stock Transfer
        sre_names = frappe.get_all(
            "Stock Reservation Entry",
            filters={
                "from_voucher_type": self.doctype,
                "from_voucher_no": self.name,
                "docstatus": 1,
            },
            pluck="name",
        )
        for sre_name in sre_names:
            try:
                sre = frappe.get_doc("Stock Reservation Entry", sre_name)
                sre.flags.ignore_permissions = True
                sre.cancel()
                # Break circular link so SE/ST cancel is not blocked
                sre.db_set("from_voucher_no", "", update_modified=False)
                sre.db_set("from_voucher_detail_no", "", update_modified=False)
            except Exception:
                frappe.log_error(
                    title="Stock Transfer Cancel - SRE Error",
                    message=f"{self.name} / {sre_name}\n{frappe.get_traceback()}",
                )
                errors.append(f"SRE {sre_name}")

        # 2) Resolve linked Material Transfer (field may be blank on older docs)
        se_name = self._resolve_linked_stock_entry()
        if se_name:
            try:
                se = frappe.get_doc("Stock Entry", se_name)
                if se.docstatus == 1:
                    # Cancel piece ledgers before SE so links do not block
                    _cancel_psles_for_voucher(se_name)
                    se.flags.ignore_permissions = True
                    se.flags.ignore_links = True
                    se.cancel()
            except Exception:
                frappe.log_error(
                    title="Stock Transfer Cancel - Stock Entry Error",
                    message=f"{self.name} / {se_name}\n{frappe.get_traceback()}",
                )
                errors.append(f"Stock Entry {se_name}")
        elif self.transfer_item:
            # Submitted transfers always create an SE — missing link means
            # stock would stay in the target warehouse if we continue.
            errors.append("linked Stock Entry not found (cannot rollback warehouse qty)")

        if self.stock_entry or se_name:
            self.db_set("stock_entry", "", update_modified=False)

        if errors:
            frappe.throw(
                _(
                    "Stock Transfer {0} cancel incomplete — stock may still be in "
                    "the target warehouse. Failed: {1}"
                ).format(frappe.bold(self.name), ", ".join(errors)),
                title=_("Cancel Rollback Failed"),
            )

    def _resolve_linked_stock_entry(self):
        """Find Material Transfer for this ST even if stock_entry field is empty.

        Matching uses this document's source/target warehouses (any WH names).
        """
        if self.stock_entry and frappe.db.exists("Stock Entry", self.stock_entry):
            return self.stock_entry

        # Preferred: reverse link on Stock Entry
        if frappe.get_meta("Stock Entry").has_field("stock_transfer"):
            se_name = frappe.db.get_value(
                "Stock Entry",
                {"stock_transfer": self.name, "docstatus": ["<", 2]},
                "name",
                order_by="docstatus desc, creation desc",
            )
            if se_name:
                return se_name

        # Fallback for legacy docs (blank stock_entry / stock_transfer link):
        # match Material Transfer by this ST's warehouses + posting date + batch.
        if not self.transfer_item:
            return None

        for row in self.transfer_item:
            batch_no = row.batch
            if not batch_no:
                continue

            from_wh = row.source_warehouse or self.source_warehouse
            to_wh = row.target_warehouse or self.target_warehouse
            if not from_wh or not to_wh:
                continue

            rows = frappe.db.sql(
                """
                SELECT se.name
                FROM `tabStock Entry` se
                INNER JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
                WHERE se.docstatus = 1
                  AND se.stock_entry_type = 'Material Transfer'
                  AND se.from_warehouse = %s
                  AND se.to_warehouse = %s
                  AND se.posting_date = %s
                  AND sed.batch_no = %s
                  AND sed.item_code = %s
                ORDER BY se.creation DESC
                LIMIT 1
                """,
                (
                    from_wh,
                    to_wh,
                    self.posting_date,
                    batch_no,
                    row.item_code,
                ),
            )
            if rows:
                return rows[0][0]

        return None

    def validate(self):
        self.align_transfer_row_dimensions()
        self.validate_transfer_item_limits()
        self.add_customer_and_po_no()

    def align_transfer_row_dimensions(self):
        """Keep transferred Tonne qty authoritative when Pcs + Length are set.

        Aligns section_weight so qty ≈ pcs × length × sw / 1000, instead of
        silently rewriting qty from a mismatched Batch/Item section_weight.
        """
        for item in self.transfer_item or []:
            pieces = flt(item.pieces)
            length = flt(item.length)
            qty = flt(item.qty)
            if pieces > 0 and length > 0 and qty > 0:
                item.section_weight = flt((qty * 1000) / (pieces * length), 6)
        
    def add_customer_and_po_no(self):
        for row in self.transfer_item:
            if row.source_document_type == "Stock Entry":
                work_order = frappe.db.get_value(
                    "Stock Entry",
                    row.source_document_name,
                    "work_order"
                )

                if work_order:
                    wo = frappe.get_doc("Work Order", work_order)
                    row.customer = wo.customer
                    row.customer_name = frappe.db.get_value(
                        "Customer", wo.customer, "customer_name"
                    )
                    row.customer_po_no = wo.po_no

            elif row.source_document_type == "Purchase Receipt":
                # Fetch Sales Order from Purchase Receipt Item
                pr_item = frappe.db.get_value(
                    "Purchase Receipt Item",
                    {
                        "parent": row.source_document_name,
                        "item_code": row.item_code, 
                        "batch_no": row.batch
                    },
                    ["sales_order", "sales_order_item"],
                    as_dict=True,
                )

                if pr_item and pr_item.sales_order:
                    so = frappe.db.get_value(
                        "Sales Order",
                        pr_item.sales_order,
                        ["customer", "po_no"],
                        as_dict=True,
                    )

                    if so:
                        row.customer = so.customer
                        row.customer_name = frappe.db.get_value(
                            "Customer",
                            so.customer,
                            "customer_name",
                        )
                        row.customer_po_no = so.po_no

    def before_submit(self):
        # Holds data collected while creating the Stock Entry so on_submit
        # can create the Stock Reservation Entries afterwards.
        self._fg_reservation_data = []

        se = self.create_stock_entry()

        for row in self.transfer_item:
            if not row.source_document_type:
                continue

            # Get work order name from the source document
            if row.source_document_type == "Stock Entry":
                wo_name = frappe.db.get_value(
                    row.source_document_type,
                    row.source_document_name,
                    "work_order"
                )
                if not wo_name:
                    continue

                # Fetch the Work Order doc to get sales_order and sales_order_item
                wor = frappe.get_doc("Work Order", wo_name)

                if not wor.sales_order or not wor.sales_order_item:
                    continue

                # Get the qty from the specific Sales Order Item linked to this WO
                so_qty = frappe.db.get_value(
                    "Sales Order Item",
                    wor.sales_order_item,   # this is the SO Item row name stored on WO
                    "qty"
                )

                if not so_qty:
                    continue
                if wor.fg_warehouse == self.target_warehouse:
                    self._fg_reservation_data.append(
                        self._build_fg_reservation_payload(
                            row,
                            so_qty=so_qty,
                            work_order=wo_name,
                            sales_order=wor.sales_order,
                            sales_order_item=wor.sales_order_item,
                        )
                    )
            elif row.source_document_type == "Purchase Receipt":
                pr_item = frappe.db.get_value(
                    "Purchase Receipt Item",
                    {
                        "parent": row.source_document_name,
                        "item_code": row.item_code,
                    },
                    ["sales_order", "sales_order_item"],
                    as_dict=True,
                )

                if not pr_item or not pr_item.sales_order or not pr_item.sales_order_item:
                    continue

                so_qty = frappe.db.get_value(
                    "Sales Order Item",
                    pr_item.sales_order_item,
                    "qty"
                )

                if not so_qty:
                    continue

                self._fg_reservation_data.append(
                    self._build_fg_reservation_payload(
                        row,
                        so_qty=so_qty,
                        work_order=None,
                        sales_order=pr_item.sales_order,
                        sales_order_item=pr_item.sales_order_item,
                    )
                )

    def _build_fg_reservation_payload(
        self, row, so_qty, work_order, sales_order, sales_order_item
    ):
        """Collect transfer-row qty/pcs/length for FG reservation (not SO line)."""
        return {
            "item_code": row.item_code,
            "warehouse": self.target_warehouse,
            "qty": flt(row.qty),
            "pieces": flt(row.pieces),
            "length": flt(row.length),
            "section_weight": flt(row.section_weight),
            "so_qty": so_qty,
            "name": self.name,
            "stock_uom": frappe.db.get_value("Item", row.item_code, "stock_uom"),
            "work_order": work_order,
            "sales_order": sales_order,
            "sales_order_item": sales_order_item,
            "batch_no": row.batch,
            "quality_required": 0,
            "from_voucher_type": self.doctype,
            "from_voucher_no": self.name,
            "from_voucher_detail_no": row.name,
        }

    def on_submit(self):
        # Re-persist SE link after submit save (guards against field wipe)
        if self.stock_entry:
            frappe.db.set_value(
                "Stock Transfer",
                self.name,
                "stock_entry",
                self.stock_entry,
                update_modified=False,
            )

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
                from_voucher_detail_no=data["from_voucher_detail_no"],
                pieces=data.get("pieces"),
                length=data.get("length"),
                section_weight=data.get("section_weight"),
            )

    def validate_transfer_item_limits(self):
        for item in self.transfer_item:
            if not item.batch:
                continue

            batch_values = frappe.db.get_value(
                "Batch", item.batch, ["pieces", "batch_qty"], as_dict=True
            )

            if not batch_values:
                continue

            batch_pieces = flt(batch_values.pieces)
            batch_qty = flt(batch_values.batch_qty)
            item_pieces = flt(item.pieces)
            item_qty = flt(item.qty)

            if item_pieces > batch_pieces:
                frappe.throw(
                    f"Row #{item.idx}: Pieces {item_pieces} cannot exceed Batch Pieces {batch_pieces} for Batch {item.batch}."
                )

            if item_qty > batch_qty:
                frappe.throw(
                    f"Row #{item.idx}: Qty {item_qty} cannot exceed Batch Qty {batch_qty} for Batch {item.batch}."
                )

    def create_stock_entry(self):
        if not self.transfer_item:
            frappe.throw(
                "No items in the Transfer Item table. Please fetch details before submitting."
            )

        se = frappe.new_doc("Stock Entry")
        se.stock_entry_type = "Material Transfer"
        if se.meta.has_field("stock_transfer"):
            se.stock_transfer = self.name
        se.set_posting_time = 1
        se.posting_date = self.posting_date
        se.company = self.company
        se.from_warehouse = self.source_warehouse
        se.to_warehouse = self.target_warehouse

        if self.sales_order:
            se.sales_order_no = self.sales_order

        for item in self.transfer_item:
            se.append(
                "items",
                {
                    "item_code": item.item_code,
                    "qty": item.qty,
                    "s_warehouse": item.source_warehouse or self.source_warehouse,
                    "t_warehouse": item.target_warehouse or self.target_warehouse,
                    "batch_no": item.batch,
                    "use_serial_batch_fields": 1,
                    "pieces": item.pieces,
                    "average_length": item.length,
                    "section_weight": item.section_weight,
                    "cost_center": self.cost_center,
                    "branch": self.branch,
                },
            )

        se.insert(ignore_permissions=True)
        se.submit()

        # Set on the in-memory doc so submit save persists the link.
        # db.set_value alone was wiped when Stock Transfer finished submitting.
        self.stock_entry = se.name
        frappe.db.set_value(
            "Stock Transfer", self.name, "stock_entry", se.name, update_modified=False
        )

        frappe.msgprint(
            f"Stock Entry <b><a href='/app/stock-entry/{se.name}'>{se.name}</a></b> created successfully.",
            alert=True,
        )

        return se
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
        pieces=None,
        length=None,
        section_weight=None,
    ):
        if not sales_order:
            return
        if quality_required:
            frappe.log_error(
                title="Quality Inspection Required - Skipping Stock Reservation",
                message=(
                    f"Skipping stock reservation for {item_code} in WO {work_order} "
                    f"linked to SO {sales_order} because quality inspection is required."
                ),
            )
            return

        so_items = frappe.get_all(
            "Sales Order Item",
            filters={
                "parent": sales_order,
                "item_code": item_code,
                "name": sales_order_item,
                "docstatus": 1,
            },
            fields=["name", "qty", "stock_reserved_qty", "warehouse"],
        )
        if (
            frappe.db.get_value(
                "Sales Order Item",
                {
                    "parent": sales_order,
                    "item_code": item_code,
                    "name": sales_order_item,
                },
                "warehouse",
            )
            != warehouse
        ):
            return
        if not so_items:
            frappe.throw(f"❌ SO Item not found for {item_code} in {sales_order}")

        item = so_items[0]
        so_detail = item.name
        available_qty = max(0, flt(item.qty) - flt(item.stock_reserved_qty or 0))

        over_reservation_allowance = flt(
            frappe.db.get_single_value("Stock Settings", "over_reservation_allowance") or 0
        )

        already_reserved_qty = (
            frappe.db.sql(
                """
                SELECT COALESCE(SUM(reserved_qty), 0)
                FROM `tabStock Reservation Entry`
                WHERE
                    voucher_type = 'Sales Order'
                    AND voucher_no = %s
                    AND voucher_detail_no = %s
                    AND docstatus = 1
                    AND item_code = %s
                """,
                (sales_order, so_detail, item_code),
            )[0][0]
            or 0
        )

        allowed_qty = flt(so_qty) * (1 + over_reservation_allowance / 100)
        available_qty_to_reserve = max(0, flt(allowed_qty) - flt(already_reserved_qty))

        if available_qty_to_reserve <= 0:
            return

        # Always reserve transferred tonne qty (not pcs×length×item weight).
        reserve_qty = flt(min(flt(qty), available_qty_to_reserve), 3)
        if reserve_qty <= 0:
            return

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
        sre.reserved_qty = reserve_qty
        sre.voucher_qty = flt(so_qty, 3)
        sre.available_qty = flt(available_qty, 3)
        sre.available_qty_to_reserve = reserve_qty

        has_batch_no = frappe.get_cached_value("Item", item_code, "has_batch_no")

        if batch_no and has_batch_no and reserve_qty > 0:
            sre.has_batch_no = 1
            sre.has_serial_no = 0
            sre.reservation_based_on = "Serial and Batch"
            sre.use_serial_batch_fields = 1

            # Prefer Stock Transfer row dimensions (what was actually moved).
            # Never overwrite from SO line or Item.weight_per_meter — that used
            # to store pcs×length×kg/m/1000 into section_weight (~0.788) and
            # show wrong Pcs/Length on the SRE.
            batch_vals = frappe.db.get_value(
                "Batch",
                batch_no,
                ["pieces", "average_length", "section_weight"],
                as_dict=True,
            ) or frappe._dict()

            entry_pieces, entry_length, entry_section_weight = resolve_sre_sb_dimensions(
                pieces=pieces,
                length=length,
                section_weight=section_weight,
                batch_vals=batch_vals,
            )

            sre.append(
                "sb_entries",
                {
                    "batch_no": batch_no,
                    "qty": reserve_qty,
                    "warehouse": warehouse,
                    "pieces": entry_pieces,
                    "length": entry_length,
                    "section_weight": entry_section_weight,
                },
            )

            # Keep explicitly selected batch (do not length-window re-pick)
            sre.auto_reserve_serial_and_batch = lambda *args, **kwargs: None
        else:
            sre.reservation_based_on = "Qty"

        sre.flags.ignore_permissions = True
        sre.insert()
        sre.submit()


@frappe.whitelist()
def get_batch_stock(
    source_warehouse=None,
    from_date=None,
    to_date=None,
    item_name=None
):
    conditions = [
    "sabb.warehouse = %(source_warehouse)s",
    "sbe.warehouse = %(source_warehouse)s"
    ]

    if from_date and to_date:
        conditions.append(
            "sabb.posting_date BETWEEN %(from_date)s AND %(to_date)s"
        )

    if item_name:
        conditions.append(
            "i.item_name LIKE %(item_name)s"
        )

    where_clause = " AND ".join(conditions)

    data = frappe.db.sql(
        f"""
        SELECT
            sbe.batch_no,
            MAX(sabb.item_code) AS item_code,
            MAX(i.item_name) AS item_name,

            SUM(
                sbe.qty - IFNULL(sbe.delivered_qty, 0)
            ) AS qty,

            IFNULL(MAX(p.pieces), 0) AS pieces,

            MAX(b.average_length) AS average_length,
            MAX(b.section_weight) AS section_weight,
            MAX(b.reference_doctype) AS reference_doctype,
            MAX(b.reference_name) AS reference_name

        FROM `tabSerial and Batch Entry` sbe

        INNER JOIN `tabSerial and Batch Bundle` sabb
            ON sabb.name = sbe.parent

        LEFT JOIN `tabBatch` b
            ON b.name = sbe.batch_no

        LEFT JOIN `tabItem` i
            ON i.name = sabb.item_code

        LEFT JOIN (
            SELECT
                sbe2.batch_no,
                psle.warehouse,
                SUM(psle.actual_qty) AS pieces
            FROM `tabPiece Stock Ledger Entry` psle
            INNER JOIN `tabSerial and Batch Entry` sbe2
                ON sbe2.parent = psle.serial_and_batch_bundle
            WHERE IFNULL(psle.is_cancelled, 0) = 0
            GROUP BY
                sbe2.batch_no,
                psle.warehouse
        ) p
            ON p.batch_no = sbe.batch_no
            AND p.warehouse = sbe.warehouse

        WHERE
            {where_clause}
            AND sabb.is_cancelled = 0
            And sabb.docstatus = 1

        GROUP BY
            sbe.batch_no

        HAVING
            SUM(
                sbe.qty - IFNULL(sbe.delivered_qty, 0)
            ) > 0

        ORDER BY
            MAX(sabb.posting_date) ASC
        """,
        {
            "source_warehouse": source_warehouse,
            "from_date": from_date,
            "to_date": to_date,
            "item_name": f"%{item_name}%" if item_name else None,
        },
        as_dict=1,
    )
    return data