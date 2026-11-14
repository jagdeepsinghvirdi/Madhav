# Copyright (c) 2026, Finbyz pvt. ltd. and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.utils import cint, flt
from frappe.model.document import Document
from erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry import (
	get_available_qty_to_reserve,
)


RELEASED_SRE_PREFIX = "MADHAV_ST_RELEASED_SRE::"


def resolve_sre_sb_dimensions(qty=0, pieces=None, length=None, section_weight=None, batch_vals=None):
	"""Pick Pcs/Length/section_weight for SRE sb_entries from transfer row, else Batch.

	Transfer tonne qty is reserved separately — never derive reserved_qty from
	Item.weight_per_meter × SO pieces (that produced ~0.788 for 1T transfers).
	"""
	batch_vals = batch_vals or {}
	entry_pieces = flt(pieces)
	entry_length = flt(length)
	entry_section_weight = flt(section_weight)

	if not entry_length:
		entry_length = flt(batch_vals.get("average_length") or batch_vals.get("length") or 0)
	if not entry_section_weight:
		entry_section_weight = flt(batch_vals.get("section_weight") or 0)
	# The SRE must carry the pieces for this transferred quantity, never
	# the complete Batch.pieces value.
	if flt(qty) > 0 and entry_length and entry_section_weight:
		from madhav.madhav.utils.stock_piece_utils import int_pieces_from_qty
		entry_pieces = int_pieces_from_qty(qty, entry_length, entry_section_weight)
	elif not entry_pieces:
		entry_pieces = 0

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


def get_live_batch_qty(item_code, warehouse, batch_no):
	"""Physical batch quantity in one warehouse; never read Batch.batch_qty."""
	if not item_code or not warehouse or not batch_no:
		return 0
	return flt(frappe.db.sql(
		"""
		SELECT COALESCE(SUM(sle.actual_qty), 0)
		FROM `tabStock Ledger Entry` sle
		INNER JOIN `tabSerial and Batch Entry` sbe
			ON sbe.parent = sle.serial_and_batch_bundle
		WHERE sle.item_code = %s AND sle.warehouse = %s
		  AND sbe.batch_no = %s AND sle.is_cancelled = 0
		""",
		(item_code, warehouse, batch_no),
	)[0][0])


def get_live_batch_pieces(item_code, warehouse, batch_no):
	"""Physical pieces in one warehouse, when piece ledger history exists."""
	if not item_code or not warehouse or not batch_no:
		return None
	rows = frappe.db.sql(
		"""
		SELECT COUNT(psle.name), COALESCE(SUM(psle.actual_qty), 0)
		FROM `tabPiece Stock Ledger Entry` psle
		INNER JOIN `tabSerial and Batch Entry` sbe
			ON sbe.parent = psle.serial_and_batch_bundle
		WHERE psle.item_code = %s AND psle.warehouse = %s
		  AND sbe.batch_no = %s AND psle.is_cancelled = 0
		""",
		(item_code, warehouse, batch_no),
	)
	return flt(rows[0][1]) if rows and rows[0][0] else None


def get_reserved_batch_map(warehouse, batch_nos, own_lines=None):
	"""Open Sales Order reservations per batch in one warehouse.

	Returns ``{batch_no: {"qty": undelivered reserved qty, "pieces": open pieces}}``
	from submitted, not-yet-delivered batch-based Stock Reservation Entries
	(Madhav reserves by batch; qty-only reservations are not batch specific).

	``own_lines`` is ``{batch_no: Sales Order Item}``: the order line the
	batch was made / bought for. A reservation for that same line does not
	count (Harsh, 14 Nov 2026: stock reserved for the same Sales Order line
	stays available for that order; the transfer moves the reservation with
	the stock). Reservations for any other line do count: moving that stock
	would leave another order's reservation pointing at stock that is gone.
	"""
	batch_nos = [b for b in (batch_nos or []) if b]
	if not warehouse or not batch_nos:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT sbe.batch_no, sre.voucher_detail_no,
			sbe.qty, IFNULL(sbe.delivered_qty, 0) AS delivered_qty,
			IFNULL(sbe.pieces, 0) AS pieces
		FROM `tabStock Reservation Entry` sre
		INNER JOIN `tabSerial and Batch Entry` sbe
			ON sbe.parent = sre.name AND sbe.parenttype = 'Stock Reservation Entry'
		WHERE sre.docstatus = 1
		  AND sre.status IN ('Reserved', 'Partially Reserved', 'Partially Delivered')
		  AND sre.warehouse = %(warehouse)s
		  AND sbe.batch_no IN %(batch_nos)s
		""",
		{"warehouse": warehouse, "batch_nos": tuple(batch_nos)},
		as_dict=True,
	)
	own_lines = own_lines or {}
	rows = [
		r for r in rows
		if not (own_lines.get(r.get("batch_no")) and r.get("voucher_detail_no") == own_lines.get(r.get("batch_no")))
	]
	return summarize_reserved_batches(rows)


def source_sales_order_line(source_doctype, source_name, item_code=None, batch_no=None):
	"""Sales Order Item a transferred batch belongs to, from its source document.

	Stock Entry (Manufacture) → its Work Order's line; Purchase Receipt → the
	receipt row's line. None when the batch is not tied to an order line.
	"""
	if (not source_doctype or not source_name) and batch_no:
		# Row typed in by hand: use the document the batch came from.
		ref = frappe.db.get_value(
			"Batch", batch_no, ["reference_doctype", "reference_name"], as_dict=True
		)
		if isinstance(ref, dict):
			source_doctype, source_name = ref.get("reference_doctype"), ref.get("reference_name")
	if not source_doctype or not source_name:
		return None
	if source_doctype == "Stock Entry":
		work_order = frappe.db.get_value("Stock Entry", source_name, "work_order")
		return frappe.db.get_value("Work Order", work_order, "sales_order_item") if work_order else None
	if source_doctype == "Purchase Receipt":
		filters = {"parent": source_name}
		if item_code:
			filters["item_code"] = item_code
		if batch_no:
			line = frappe.db.get_value("Purchase Receipt Item", dict(filters, batch_no=batch_no), "sales_order_item")
			if line:
				return line
		return frappe.db.get_value("Purchase Receipt Item", filters, "sales_order_item")
	return None


def summarize_reserved_batches(rows):
	"""Sum open reserved qty / pieces per batch (pieces pro-rata to undelivered qty)."""
	out = {}
	for r in rows or []:
		qty = flt(r.get("qty"))
		open_qty = max(qty - flt(r.get("delivered_qty")), 0)
		if open_qty <= 0:
			continue
		pieces = flt(r.get("pieces"))
		open_pieces = pieces * open_qty / qty if qty > 0 else 0
		cur = out.setdefault(r.get("batch_no"), {"qty": 0.0, "pieces": 0.0})
		cur["qty"] += open_qty
		cur["pieces"] += open_pieces
	return out


def free_batch_limits(live_qty, live_pieces, reserved):
	"""Qty / pieces of a batch that a Stock Transfer may move (live minus reserved)."""
	reserved = reserved or {}
	free_qty = max(flt(flt(live_qty) - flt(reserved.get("qty")), 3), 0)
	free_pieces = None
	if live_pieces is not None:
		free_pieces = max(flt(live_pieces) - round(flt(reserved.get("pieces"))), 0)
	return free_qty, free_pieces


class StockTransfer(Document):

    def before_cancel(self):
        # UI "Cancel All" tries to cancel the Material Transfer *before* this
        # Stock Transfer is cancelled. That fails with LinkExistsError because
        # ST.stock_entry still points at the SE. Backend on_cancel cancels the
        # SE after ST is already docstatus=2 — so ignore SE in the link check
        # and in the form's cancel-all dialog (see stock_transfer.js).
        self.ignore_linked_doctypes = ["Stock Entry"]

    def on_cancel(self):
        """Unreserve stock, then reverse Material Transfer back to source warehouse.

        Works for any source → target warehouse pair (not tied to specific WH names).

        Critical: linked ``stock_entry`` must be cancelled. Historically
        ``db.set_value`` during ``before_submit`` was overwritten on submit save,
        leaving ``stock_entry`` blank — cancel then skipped the SE and stock
        stayed in the target warehouse.

        Order matters: this runs after ST is already cancelled in the DB, so
        cancelling the SE is not blocked by the ST → SE link.
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

        # Drop both sides of the link before SE cancel so neither document
        # blocks the other if ignore_links is not honoured somewhere.
        if self.stock_entry or se_name:
            self.db_set("stock_entry", "", update_modified=False)

        if se_name:
            try:
                se = frappe.get_doc("Stock Entry", se_name)
                if se.docstatus == 1:
                    if se.meta.has_field("stock_transfer"):
                        se.db_set("stock_transfer", "", update_modified=False)
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

        # Stock is back in the source warehouse: give back any reservation
        # of the batch's own order line that the transfer moved.
        if not errors:
            errors.extend(self.restore_released_reservations())

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
        #
        # Stock Entries owned by another Stock Transfer are excluded, otherwise
        # cancelling this transfer would reverse someone else's stock movement
        # when both moved the same batch between the same warehouses on the
        # same day. If more than one unowned candidate still matches, the match
        # is ambiguous and we return nothing so the caller fails loudly instead
        # of cancelling the wrong document.
        if not self.transfer_item:
            return None

        candidates = set()
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
                SELECT DISTINCT se.name
                FROM `tabStock Entry` se
                INNER JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
                WHERE se.docstatus = 1
                  AND se.stock_entry_type = 'Material Transfer'
                  AND se.from_warehouse = %(from_wh)s
                  AND se.to_warehouse = %(to_wh)s
                  AND se.posting_date = %(posting_date)s
                  AND sed.batch_no = %(batch_no)s
                  AND sed.item_code = %(item_code)s
                  AND IFNULL(se.stock_transfer, '') IN ('', %(name)s)
                  AND se.name NOT IN (
                      SELECT IFNULL(st.stock_entry, '')
                      FROM `tabStock Transfer` st
                      WHERE st.name != %(name)s
                        AND IFNULL(st.stock_entry, '') != ''
                  )
                """,
                {
                    "from_wh": from_wh,
                    "to_wh": to_wh,
                    "posting_date": self.posting_date,
                    "batch_no": batch_no,
                    "item_code": row.item_code,
                    "name": self.name,
                },
            )
            candidates.update(r[0] for r in rows)

        # One Stock Transfer always creates exactly one Material Transfer, so a
        # genuine match collapses to a single Stock Entry across all rows.
        if len(candidates) == 1:
            return candidates.pop()

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
        # Build + validate reservation candidates BEFORE creating the Stock
        # Entry so a length-tolerance failure cannot leave an orphaned SE.
        self._fg_reservation_data = []
        skipped_warehouse = []

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

                # Auto-reserve only when Target Warehouse matches the SO
                # item warehouse (Finished Goods / SO line warehouse).
                # Do not key off Work Order.fg_warehouse — QI → FG transfers
                # must still reserve when the target is the SO warehouse.
                target_wh = row.target_warehouse or self.target_warehouse
                so_wh = frappe.db.get_value(
                    "Sales Order Item", wor.sales_order_item, "warehouse"
                )
                if so_wh and target_wh and so_wh == target_wh:
                    payload = self._build_fg_reservation_payload(
                        row,
                        so_qty=so_qty,
                        work_order=wo_name,
                        sales_order=wor.sales_order,
                        sales_order_item=wor.sales_order_item,
                    )
                    self._validate_payload_length_for_reservation(payload)
                    self._fg_reservation_data.append(payload)
                elif wor.sales_order:
                    skipped_warehouse.append(
                        f"Row #{row.idx}: {row.item_code} "
                        f"(target {target_wh or '-'} ≠ SO warehouse {so_wh or '-'})"
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

                target_wh = row.target_warehouse or self.target_warehouse
                so_wh = frappe.db.get_value(
                    "Sales Order Item", pr_item.sales_order_item, "warehouse"
                )
                if so_wh and target_wh and so_wh == target_wh:
                    payload = self._build_fg_reservation_payload(
                        row,
                        so_qty=so_qty,
                        work_order=None,
                        sales_order=pr_item.sales_order,
                        sales_order_item=pr_item.sales_order_item,
                    )
                    self._validate_payload_length_for_reservation(payload)
                    self._fg_reservation_data.append(payload)
                else:
                    skipped_warehouse.append(
                        f"Row #{row.idx}: {row.item_code} "
                        f"(target {target_wh or '-'} ≠ SO warehouse {so_wh or '-'})"
                    )

        if skipped_warehouse:
            frappe.msgprint(
                _(
                    "Stock reservation was skipped because the Target Warehouse "
                    "does not match the Sales Order item warehouse:<br><ul><li>{0}</li></ul>"
                ).format("</li><li>".join(skipped_warehouse)),
                title=_("Reservation Skipped"),
                indicator="orange",
            )

        self.release_own_line_reservations()
        self.create_stock_entry()

    def release_own_line_reservations(self):
        """Take the transferred qty off reservations for the batch's own line.

        That stock stays available to its own order (Harsh, 14 Nov 2026), but
        a reservation is tied to its warehouse, so it is released here before
        the Material Transfer and, when the target is the Sales Order
        warehouse, reserved again there by on_submit. What was released is
        recorded so cancelling the transfer gives it back.
        """
        from madhav.madhav.doctype.party_transfer.party_transfer import (
            proportional_pieces,
            trim_reservation,
        )

        released = []
        for row in self.transfer_item or []:
            if not row.batch:
                continue
            warehouse = row.source_warehouse or self.source_warehouse
            line = source_sales_order_line(
                row.source_document_type, row.source_document_name, row.item_code, row.batch
            )
            if not line:
                continue
            remaining = flt(row.qty, 3)
            entries = frappe.db.sql(
                """
                SELECT sre.name, sbe.qty, IFNULL(sbe.delivered_qty, 0) AS delivered_qty,
                    IFNULL(sbe.pieces, 0) AS pieces, sbe.length, sbe.section_weight
                FROM `tabStock Reservation Entry` sre
                INNER JOIN `tabSerial and Batch Entry` sbe
                    ON sbe.parent = sre.name AND sbe.parenttype = 'Stock Reservation Entry'
                WHERE sre.docstatus = 1 AND sre.voucher_detail_no = %s AND sre.warehouse = %s
                  AND sre.status IN ('Reserved', 'Partially Reserved', 'Partially Delivered')
                  AND sbe.batch_no = %s
                ORDER BY sre.creation
                FOR UPDATE
                """,
                (line, warehouse, row.batch),
                as_dict=True,
            )
            for e in entries:
                if remaining <= 0.0005:
                    break
                open_qty = flt(flt(e.qty) - flt(e.delivered_qty), 3)
                take = flt(min(open_qty, remaining), 3)
                if take <= 0:
                    continue
                pieces = proportional_pieces(take, open_qty, flt(e.pieces) * open_qty / flt(e.qty) if flt(e.qty) else 0)
                sre = frappe.get_doc("Stock Reservation Entry", e.name)
                header_open = flt(sre.reserved_qty) - flt(sre.delivered_qty)
                cancelled = flt(sre.delivered_qty) <= 0.0005 and abs(header_open - take) <= 0.0005
                if cancelled:
                    sre.flags.ignore_permissions = True
                    sre.flags.ignore_links = True
                    sre.cancel()
                else:
                    trim_reservation(sre, row.batch, take, pieces)
                released.append({
                    "sre": e.name, "batch_no": row.batch, "qty": take, "pieces": pieces,
                    "warehouse": warehouse, "length": flt(e.length), "section_weight": flt(e.section_weight),
                    "cancelled": int(cancelled), "row": row.name,
                })
                remaining = flt(remaining - take, 3)

        self._released_reservations = released
        if released:
            frappe.get_doc({
                "doctype": "Comment", "comment_type": "Info",
                "reference_doctype": self.doctype, "reference_name": self.name,
                "content": RELEASED_SRE_PREFIX + json.dumps(released),
            }).insert(ignore_permissions=True)
            frappe.msgprint(
                _("Reservation of the batch's own Sales Order line moves with the stock: {0}").format(
                    ", ".join(f"{r['qty']} of {r['batch_no']} ({r['sre']})" for r in released)
                ),
                indicator="blue",
                alert=True,
            )
        return released

    def restore_released_reservations(self):
        """On cancel: give back what release_own_line_reservations took."""
        from madhav.madhav.doctype.party_transfer.party_transfer import (
            recreate_reservation,
            trim_reservation,
        )

        content = frappe.db.get_value(
            "Comment",
            {
                "reference_doctype": self.doctype, "reference_name": self.name,
                "content": ["like", RELEASED_SRE_PREFIX + "%"],
            },
            "content",
        )
        if not content:
            return []
        errors = []
        for r in json.loads(content[len(RELEASED_SRE_PREFIX):]):
            try:
                source = frappe.get_doc("Stock Reservation Entry", r["sre"])
                if source.docstatus == 1:
                    trim_reservation(source, r["batch_no"], -flt(r["qty"]), -cint(r["pieces"]))
                else:
                    recreate_reservation(source, frappe._dict(
                        transfer_qty=r["qty"], batch_no=r["batch_no"], warehouse=r["warehouse"],
                        transfer_pieces=r["pieces"], length=r["length"], section_weight=r["section_weight"],
                    ))
            except Exception:
                frappe.log_error(
                    title="Stock Transfer Cancel - restore reservation",
                    message=f"{self.name} / {r.get('sre')}\n{frappe.get_traceback()}",
                )
                errors.append(f"reservation {r.get('sre')}")
        return errors

    def _validate_payload_length_for_reservation(self, payload):
        """Fail early when length is outside SO ±2 (before Stock Entry submit)."""
        from madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool import (
            validate_length_for_so_reservation,
        )

        validate_length_for_so_reservation(
            payload.get("sales_order_item"),
            payload.get("length"),
            sales_order=payload.get("sales_order"),
            item_code=payload.get("item_code"),
            batch_no=payload.get("batch_no"),
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

        self.carry_released_reservations()

    def carry_released_reservations(self):
        """Re-reserve, in the target warehouse, own-line stock released on submit
        when the usual Sales Order warehouse rule did not reserve that row
        (e.g. Finished Goods → For Mill). The reservation moves with the stock
        and stays with its own order line; it is linked to this transfer, so
        cancelling the transfer cancels it and gives the source back.
        """
        reserved_rows = {d["from_voucher_detail_no"] for d in getattr(self, "_fg_reservation_data", [])}
        rows = {r.name: r for r in self.transfer_item or []}
        for rec in getattr(self, "_released_reservations", []) or []:
            row = rows.get(rec["row"])
            if not row or row.name in reserved_rows:
                continue
            source = frappe.get_doc("Stock Reservation Entry", rec["sre"])
            target = row.target_warehouse or self.target_warehouse
            new = frappe.copy_doc(source)
            new.amended_from = None
            new.warehouse = target
            new.from_voucher_type, new.from_voucher_no, new.from_voucher_detail_no = self.doctype, self.name, row.name
            new.status = "Draft"
            new.delivered_qty = 0
            new.reserved_qty = flt(rec["qty"], 3)
            new.available_qty_to_reserve = flt(rec["qty"], 3)
            new.available_qty = flt(get_available_qty_to_reserve(new.item_code, target), 3)
            if new.reservation_based_on == "Serial and Batch":
                new.set("sb_entries", [])
                new.append("sb_entries", {
                    "batch_no": rec["batch_no"], "qty": flt(rec["qty"], 3), "warehouse": target,
                    "pieces": cint(rec["pieces"]), "length": flt(rec["length"]),
                    "section_weight": flt(rec["section_weight"]),
                })
                new.auto_reserve_serial_and_batch = lambda *args, **kwargs: None
            new.flags.ignore_permissions = True
            new.insert()
            new.submit()

    def validate_transfer_item_limits(self):
        for item in self.transfer_item:
            if not item.batch:
                continue

            warehouse = item.source_warehouse or self.source_warehouse
            live_qty = get_live_batch_qty(item.item_code, warehouse, item.batch)
            live_pieces = get_live_batch_pieces(item.item_code, warehouse, item.batch)
            own_line = source_sales_order_line(
                item.source_document_type, item.source_document_name, item.item_code, item.batch
            )
            reserved = get_reserved_batch_map(
                warehouse, [item.batch], {item.batch: own_line} if own_line else None
            ).get(item.batch) or {}
            batch_qty, batch_pieces = free_batch_limits(live_qty, live_pieces, reserved)
            item_pieces = flt(item.pieces)
            item_qty = flt(item.qty)
            reserved_note = ""
            if flt(reserved.get("qty")) > 0:
                reserved_note = _(
                    " {0} of it is reserved against another Sales Order line; unreserve it first to move it."
                ).format(flt(reserved.get("qty"), 3))

            if batch_pieces is not None and item_pieces > batch_pieces:
                frappe.throw(
                    f"Row #{item.idx}: Pieces {item_pieces} cannot exceed available Batch Pieces "
                    f"{batch_pieces} for Batch {item.batch}.{reserved_note}"
                )

            if flt(item_qty, 3) > batch_qty:
                frappe.throw(
                    f"Row #{item.idx}: Qty {item_qty} cannot exceed available Batch Qty "
                    f"{batch_qty} for Batch {item.batch}.{reserved_note}"
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
        so_warehouse = frappe.db.get_value(
            "Sales Order Item",
            {
                "parent": sales_order,
                "item_code": item_code,
                "name": sales_order_item,
            },
            "warehouse",
        )
        # Point 4: auto-reserve only when Target Warehouse = SO item warehouse.
        if so_warehouse != warehouse:
            return
        if not so_items:
            frappe.throw(f"❌ SO Item not found for {item_code} in {sales_order}")

        item = so_items[0]
        so_detail = item.name

        from madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool import (
            get_auto_reserve_available_qty,
            floor_qty,
            validate_length_for_so_reservation,
        )

        # Point 1: length must be within SO length_size ± 2.
        validate_length_for_so_reservation(
            so_detail,
            length,
            sales_order=sales_order,
            item_code=item_code,
            batch_no=batch_no,
        )

        # Client-confirmed: Max Reserved = Total SO Qty + Over Reservation
        # Allowance % from Stock Settings (e.g. 20% → up to 120% of SO total).
        over_pct = flt(
            frappe.db.get_single_value("Stock Settings", "over_reservation_allowance") or 0
        )
        available_qty_to_reserve = get_auto_reserve_available_qty(sales_order)

        if available_qty_to_reserve <= 0:
            frappe.msgprint(
                _(
                    "Stock reservation skipped for {0}: Sales Order {1} has no "
                    "remaining quantity within the Over Reservation Allowance "
                    "(Stock Settings allowance: {2}%). "
                    "Max reserved = Total SO Qty + allowance %."
                ).format(
                    frappe.bold(item_code),
                    frappe.bold(sales_order),
                    frappe.bold(over_pct),
                ),
                title=_("Reservation Skipped"),
                indicator="orange",
                alert=True,
            )
            return

        # Always reserve transferred tonne qty (not pcs×length×item weight).
        requested_qty = flt(qty)
        reserve_qty = floor_qty(min(requested_qty, available_qty_to_reserve), 3)
        if reserve_qty <= 0:
            return

        has_batch_no = frappe.get_cached_value("Item", item_code, "has_batch_no")

        # ERPNext requires a non-zero `available_qty` ("Available Qty to
        # Reserve") and means warehouse stock that can still be reserved.
        # Deriving it from the SO line's leftover qty made it 0 as soon as an
        # earlier transfer reserved the whole line, so a later transfer was
        # rejected with "Available Qty to Reserve is required" even though the
        # over-reservation allowance still permitted the reservation.
        # Warehouse level (no batch) matches ERPNext and stays correct for
        # bundle-tracked batches, where batch-level lookups read 0.
        physical_available_qty = flt(get_available_qty_to_reserve(item_code, warehouse))
        if physical_available_qty <= 0:
            return

        reserve_qty = floor_qty(min(reserve_qty, physical_available_qty), 3)
        if reserve_qty <= 0:
            return

        if reserve_qty + 0.0005 < requested_qty:
            frappe.msgprint(
                _(
                    "Stock reservation for {0} capped at {1} (requested {2}) on "
                    "Sales Order {3}. Remaining room under Over Reservation "
                    "Allowance ({4}% in Stock Settings) was only {1}."
                ).format(
                    frappe.bold(item_code),
                    frappe.bold(reserve_qty),
                    frappe.bold(requested_qty),
                    frappe.bold(sales_order),
                    frappe.bold(over_pct),
                ),
                title=_("Partial Reservation"),
                indicator="orange",
                alert=True,
            )

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
        sre.available_qty = flt(physical_available_qty, 3)

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
                qty=reserve_qty,
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
	item_name=None,
	target_warehouse=None,
):
	"""Batches still physically in ``source_warehouse`` for Fetch Details.

	Qty is always the **live SLE balance** for that batch in the warehouse.
	That way a partial Stock Transfer leaves the remainder visible on the
	next Fetch Details — the old SABB ``SUM(qty - delivered_qty)`` path
	could drop remainders when date filters excluded the inward bundle or
	when bundle rows did not net cleanly after an outward Material Transfer.

	``from_date`` / ``to_date`` only restrict *which batches* appear (those
	with an inward posting in range). They never change how remaining qty
	is calculated.

	``target_warehouse`` is accepted for client compatibility; unused.
	"""
	if not source_warehouse:
		return []

	conditions = [
		"sle.warehouse = %(source_warehouse)s",
		"sle.is_cancelled = 0",
		"IFNULL(sle.serial_and_batch_bundle, '') != ''",
		"IFNULL(sbe.batch_no, '') != ''",
	]
	params = {"source_warehouse": source_warehouse}

	if item_name:
		conditions.append("i.item_name LIKE %(item_name)s")
		params["item_name"] = f"%{item_name}%"

	# Optional: only batches that received stock in this warehouse in range.
	# Remaining qty is still full live balance (includes older inwards).
	date_batch_filter = ""
	if from_date and to_date:
		date_batch_filter = """
			AND sbe.batch_no IN (
				SELECT sbe_d.batch_no
				FROM `tabStock Ledger Entry` sle_d
				INNER JOIN `tabSerial and Batch Entry` sbe_d
					ON sbe_d.parent = sle_d.serial_and_batch_bundle
				WHERE sle_d.warehouse = %(source_warehouse)s
				  AND sle_d.is_cancelled = 0
				  AND sle_d.actual_qty > 0
				  AND sle_d.posting_date BETWEEN %(from_date)s AND %(to_date)s
				  AND IFNULL(sbe_d.batch_no, '') != ''
			)
		"""
		params["from_date"] = from_date
		params["to_date"] = to_date

	where_clause = " AND ".join(conditions)

	data = frappe.db.sql(
		f"""
		SELECT
			sbe.batch_no,
			MAX(sle.item_code) AS item_code,
			MAX(i.item_name) AS item_name,

			SUM(SIGN(sle.actual_qty) * ABS(sbe.qty)) AS qty,

			IFNULL(MAX(p.pieces), 0) AS pieces,

			MAX(b.average_length) AS average_length,
			MAX(b.section_weight) AS section_weight,
			MAX(b.reference_doctype) AS reference_doctype,
			MAX(b.reference_name) AS reference_name

		FROM `tabStock Ledger Entry` sle

		INNER JOIN `tabSerial and Batch Entry` sbe
			ON sbe.parent = sle.serial_and_batch_bundle

		LEFT JOIN `tabBatch` b
			ON b.name = sbe.batch_no

		LEFT JOIN `tabItem` i
			ON i.name = sle.item_code

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
			AND p.warehouse = sle.warehouse

		WHERE
			{where_clause}
			{date_batch_filter}

		GROUP BY
			sbe.batch_no

		HAVING
			SUM(SIGN(sle.actual_qty) * ABS(sbe.qty)) > 0.0001

		ORDER BY
			MIN(sle.posting_date) ASC
		""",
		params,
		as_dict=1,
	)
	data = _exclude_reserved_stock(data, source_warehouse)
	_fill_missing_batch_dimensions(data)
	return data


def _exclude_reserved_stock(rows, warehouse):
	"""Leave out stock reserved for another Sales Order line (Harsh, 14 Nov 2026).

	Stock reserved for the batch's own order line stays listed.
	"""
	own_lines = {}
	for r in rows or []:
		line = source_sales_order_line(
			r.get("reference_doctype"), r.get("reference_name"), r.get("item_code"), r.get("batch_no")
		)
		if line:
			own_lines[r.get("batch_no")] = line
	reserved_map = get_reserved_batch_map(warehouse, [r.get("batch_no") for r in rows or []], own_lines)
	out = []
	for row in rows or []:
		reserved = reserved_map.get(row.get("batch_no"))
		if reserved:
			live_pieces = flt(row.get("pieces")) if flt(row.get("pieces")) > 0 else None
			free_qty, free_pieces = free_batch_limits(row.get("qty"), live_pieces, reserved)
			if free_qty <= 0.0001:
				continue
			row["reserved_qty"] = flt(reserved.get("qty"), 3)
			row["qty"] = free_qty
			if free_pieces is not None:
				row["pieces"] = free_pieces
		out.append(row)
	return out


@frappe.whitelist()
def get_transfer_batch_limits(item_code, warehouse, batch_no, source_document_type=None, source_document_name=None):
	"""Live qty/pieces in warehouse for Stock Transfer row validation.

	Batch.batch_qty / Batch.pieces are receipt-size totals and stay wrong
	after a partial Material Transfer — do not use them as transfer caps.
	Stock reserved for another order line is not free; stock reserved for
	the batch's own line (from its source document) is.
	"""
	own_line = source_sales_order_line(source_document_type, source_document_name, item_code, batch_no)
	reserved = get_reserved_batch_map(
		warehouse, [batch_no], {batch_no: own_line} if own_line else None
	).get(batch_no) or {}
	qty, pieces = free_batch_limits(
		get_live_batch_qty(item_code, warehouse, batch_no),
		get_live_batch_pieces(item_code, warehouse, batch_no),
		reserved,
	)
	return {"qty": qty, "pieces": pieces, "reserved_qty": flt(reserved.get("qty"), 3)}


def _fill_missing_batch_dimensions(rows):
	"""When Batch.average_length / section_weight were never stamped (common
	on FWO Manufacture batches), recover from the creating Stock Entry Detail
	so Stock Transfer Fetch Details still shows Length / Section Weight.
	Does not overwrite non-zero Batch values.
	"""
	missing = [
		r
		for r in (rows or [])
		if r.get("batch_no")
		and (not flt(r.get("average_length")) or not flt(r.get("section_weight")))
	]
	if not missing:
		return

	batch_nos = [r.batch_no for r in missing]
	placeholders = ", ".join(["%s"] * len(batch_nos))
	sed_rows = frappe.db.sql(
		f"""
		SELECT batch_no, average_length, section_weight, creation
		FROM `tabStock Entry Detail`
		WHERE batch_no IN ({placeholders})
		  AND (
			IFNULL(average_length, 0) > 0
			OR IFNULL(section_weight, 0) > 0
		  )
		ORDER BY creation DESC
		""",
		tuple(batch_nos),
		as_dict=1,
	)
	# First (latest) non-zero wins per batch.
	by_batch = {}
	for sed in sed_rows:
		cur = by_batch.setdefault(
			sed.batch_no, {"average_length": 0, "section_weight": 0}
		)
		if not cur["average_length"] and flt(sed.average_length):
			cur["average_length"] = flt(sed.average_length)
		if not cur["section_weight"] and flt(sed.section_weight):
			cur["section_weight"] = flt(sed.section_weight)

	for row in missing:
		dims = by_batch.get(row.batch_no) or {}
		if not flt(row.get("average_length")) and dims.get("average_length"):
			row.average_length = dims["average_length"]
		if not flt(row.get("section_weight")) and dims.get("section_weight"):
			row.section_weight = dims["section_weight"]
		# Heal Batch master only when blank — never overwrite existing Length /
		# Section Weight (keeps old correctly-stamped batches unchanged).
		cur = frappe.db.get_value(
			"Batch", row.batch_no, ["average_length", "section_weight"], as_dict=True
		) or {}
		updates = {}
		if dims.get("average_length") and not flt(cur.get("average_length")):
			updates["average_length"] = dims["average_length"]
		if dims.get("section_weight") and not flt(cur.get("section_weight")):
			updates["section_weight"] = dims["section_weight"]
		if updates:
			frappe.db.set_value("Batch", row.batch_no, updates, update_modified=False)
