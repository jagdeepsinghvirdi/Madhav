from __future__ import annotations
import frappe
from frappe import _
from frappe.utils import flt, cint

def _get_batch_constraints(voucher_type, voucher_detail_no, item_code=None,from_voucher_type=None):
    """
    Read length_size from SO Item and return min/max length constraints.
    min_length = length_size
    max_length = length_size + 1.5
    These can be overridden per-item via frappe.flags.stock_reservation_item_ranges
    """
    # For Batch Wise Reservation Tool, don't apply length constraints
    # Let auto batch selection work without length filtering
    if from_voucher_type == "Batch Wise Reservation Tool":
        return frappe._dict({"min_length": None, "max_length": None})

    constraints = frappe._dict({"min_length": None, "max_length": None})

    if voucher_type != "Sales Order" or not voucher_detail_no:
        return constraints

    if not frappe.db.has_column("Sales Order Item", "length_size"):
        return constraints

    length_size = flt(
        frappe.db.get_value("Sales Order Item", voucher_detail_no, "length_size")
    )

    if length_size <= 0:
        return constraints

    default_min = length_size
    default_max = length_size + 1.5

    # Check if dialog passed a custom max_length for this item
    flag_ranges = getattr(frappe.flags, "stock_reservation_item_ranges", {}) or {}
    flag_data = flag_ranges.get(voucher_detail_no) or {}

    raw_max = flag_data.get("max_length")
    raw_min = flag_data.get("min_length")

    max_length = flt(raw_max) if raw_max not in (None, "", 0) else default_max
    min_length = flt(raw_min) if raw_min not in (None, "", 0) else default_min

    # Never compare or filter with None (Update Items / auto re-reserve paths).
    min_length = flt(min_length) or default_min
    max_length = flt(max_length) or default_max

    if min_length > max_length:
        min_length, max_length = max_length, min_length

    constraints.update({"min_length": min_length, "max_length": max_length})
    return constraints


def _default_length_window(length_size):
    """Return (min_length, max_length) for batch filtering; never None when length_size > 0."""
    length_size = flt(length_size)
    if length_size <= 0:
        return None, None
    return length_size - 2, length_size + 2


def _get_actual_batch_length_range(item_code, warehouse):
    """Min/max length across batches that actually have stock for this
    item/warehouse. Used to auto-widen the default ±2m length window when
    it misses all real stock (e.g. length_size set to a value far from
    what's physically in the warehouse)."""
    result = frappe.db.sql(
        """
        SELECT MIN(sbe.length) AS min_len, MAX(sbe.length) AS max_len
        FROM `tabSerial and Batch Bundle` sbb
        JOIN `tabSerial and Batch Entry` sbe ON sbe.parent = sbb.name
        WHERE sbb.item_code = %s
          AND sbb.warehouse = %s
          AND sbe.qty > 0
          AND sbe.length > 0
        """,
        (item_code, warehouse),
        as_dict=True,
    )
    if not result or result[0].min_len is None:
        return None, None
    return flt(result[0].min_len), flt(result[0].max_len)


def _get_eligible_batches_ordered(batch_nos, min_length=None, max_length=None):
    """
    From the given batch_nos list, return only those whose average_length
    is within [min_length, max_length], sorted shortest-first (length FIFO).
    Both bounds are inclusive and applied at DB level.
    """
    if not batch_nos:
        return []

    min_length = flt(min_length) if min_length not in (None, "") else None
    max_length = flt(max_length) if max_length not in (None, "") else None

    batch_table = frappe.qb.DocType("Batch")
    query = (
        frappe.qb.from_(batch_table)
        .select(batch_table.name, batch_table.average_length)
        .where(batch_table.name.isin(list(set(batch_nos))))
        .where(batch_table.disabled == 0)
        .orderby(batch_table.average_length)
        .orderby(batch_table.creation)
    )

    if min_length is not None:
        query = query.where(batch_table.average_length >= min_length)
    if max_length is not None:
        query = query.where(batch_table.average_length <= max_length)

    rows = query.run(as_dict=True)
    return [r.name for r in rows]


def _get_eligible_batches(batch_nos, min_length=None, max_length=None):
    return set(
        _get_eligible_batches_ordered(
            batch_nos, min_length=min_length, max_length=max_length
        )
    )


def _get_filtered_available_qty(item_code, warehouse, constraints):
    """
    Return total available qty considering only batches that pass length filter.
    """
    import erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle as _sabb

    available_batches = _sabb.get_auto_batch_nos(
        frappe._dict(
            {
                "item_code": item_code,
                "warehouse": warehouse,
                "qty": 0,
                "based_on": frappe.db.get_single_value(
                    "Stock Settings", "pick_serial_and_batch_based_on"
                ),
            }
        )
    )

    if not available_batches:
        return 0.0

    eligible_set = _get_eligible_batches(
        [b.batch_no for b in available_batches if b.batch_no],
        min_length=constraints.get("min_length"),
        max_length=constraints.get("max_length"),
    )

    return sum(flt(b.qty) for b in available_batches if b.batch_no in eligible_set)


def _get_batch_debug_details(item_code, warehouse, constraints):
    """For error messages — show what batches exist vs what passed the filter."""
    import erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle as _sabb

    available_batches = (
        _sabb.get_auto_batch_nos(
            frappe._dict(
                {
                    "item_code": item_code,
                    "warehouse": warehouse,
                    "qty": 0,
                    "based_on": frappe.db.get_single_value(
                        "Stock Settings", "pick_serial_and_batch_based_on"
                    ),
                }
            )
        )
        or []
    )

    available_batch_nos = [b.batch_no for b in available_batches if b.batch_no]
    eligible_batch_nos = _get_eligible_batches_ordered(
        available_batch_nos,
        min_length=constraints.get("min_length"),
        max_length=constraints.get("max_length"),
    )

    return frappe._dict(
        {
            "warehouse": warehouse,
            "available_batches": available_batch_nos,
            "eligible_batches": eligible_batch_nos,
        }
    )


# ---------------------------------------------------------------------------
# Consolidated message builder
# ---------------------------------------------------------------------------

def _build_stock_reservation_summary(prep_items, filtered_out, reservation_results):
    """
    Build a single well-structured HTML message combining:
    1. Preparation summary (table of items with eligible batches)
    2. Filtered-out items (if any)
    3. Reservation results (batches used per item)
    """
    from frappe.utils import flt

    parts = []

    # --- Section 1: Preparation Summary ---
    if prep_items:
        # Deduplicate by item_code (keep first occurrence)
        seen = set()
        unique_items = []
        for item in prep_items:
            if item["item_code"] not in seen:
                seen.add(item["item_code"])
                unique_items.append(item)

        rows = ""
        for item in unique_items:
            rows += (
                "<tr>"
                "<td>{item_code}</td>"
                "<td style='text-align:right'>{batches}</td>"
                "<td style='text-align:right'>{available}</td>"
                "<td style='text-align:right'>{requested}</td>"
                "</tr>"
            ).format(
                item_code=item["item_code"],
                batches=item["eligible_batches"],
                available=flt(item["total_eligible_qty"], 3),
                requested=flt(item["requested_qty"], 3),
            )

        parts.append(
            "<p><b>{title}</b></p>"
            "<table class='table table-bordered' style='width:100%'>"
            "<thead><tr>"
            "<th>{h_item}</th>"
            "<th style='text-align:right'>{h_batches}</th>"
            "<th style='text-align:right'>{h_avail}</th>"
            "<th style='text-align:right'>{h_req}</th>"
            "</tr></thead>"
            "<tbody>{rows}</tbody>"
            "</table>".format(
                title=_("Preparation Summary"),
                h_item=_("Item Code"),
                h_batches=_("Eligible Batches"),
                h_avail=_("Available Qty"),
                h_req=_("Requested Qty"),
                rows=rows,
            )
        )

    # --- Section 2: Filtered-Out Items ---
    if filtered_out:
        # Deduplicate
        seen = set()
        unique_filtered = []
        for entry in filtered_out:
            code = entry.get("item_code")
            if code not in seen:
                seen.add(code)
                unique_filtered.append(code)

        items_str = "<br>".join(f"• {item}" for item in unique_filtered)
        parts.append(
            "<br><p><b>{title}</b></p>"
            "<p>{items}</p>"
            "<p><i>{reason}</i></p>".format(
                title=_("⚠️ Items Skipped (No Eligible Batches)"),
                items=items_str,
                reason=_(
                    "Required length is not available in stock. "
                    "Please adjust the length or check available inventory."
                ),
            )
        )

    # --- Section 3: Reservation Results ---
    if reservation_results:
        # Deduplicate by item_code
        seen = set()
        unique_results = []
        for r in reservation_results:
            if r.item_code not in seen:
                seen.add(r.item_code)
                unique_results.append(r)

        rows = ""
        for r in unique_results:
            status_badge = {
                "Reserved": '<span class="badge badge-success">{0}</span>'.format(
                    _("Reserved")
                ),
                "Partial": '<span class="badge badge-warning">{0}</span>'.format(
                    _("Partial")
                ),
                "No Eligible Batches": '<span class="badge badge-danger">{0}</span>'.format(
                    _("Failed")
                ),
            }.get(r.status, r.status)

            rows += (
                "<tr>"
                "<td>{item_code}</td>"
                "<td style='text-align:right'>{batches}</td>"
                "<td>{status}</td>"
                "</tr>"
            ).format(
                item_code=r.item_code,
                batches=r.batch_count,
                status=status_badge,
            )

        parts.append(
            "<br><p><b>{title}</b></p>"
            "<table class='table table-bordered' style='width:100%'>"
            "<thead><tr>"
            "<th>{h_item}</th>"
            "<th style='text-align:right'>{h_batches}</th>"
            "<th>{h_status}</th>"
            "</tr></thead>"
            "<tbody>{rows}</tbody>"
            "</table>".format(
                title=_("Reservation Results"),
                h_item=_("Item Code"),
                h_batches=_("Batches Used"),
                h_status=_("Status"),
                rows=rows,
            )
        )

    return "<br>".join(parts) if parts else ""


# ---------------------------------------------------------------------------
# Stamp custom fields on SRE sb_entries after reservation
# ---------------------------------------------------------------------------


def _update_sb_entries_custom_fields(doc):
    """Stamp length, pieces and section_weight on each Serial and Batch Entry row.

    Intended for newly auto-reserved SO lines only: batch-first length and
    integer pieces are derived here so reservation rows match physical batch
    dimensions. DN cancel restore reuses snapshot values instead — see
    ``_recreate_stock_reservation_from_snapshot`` in delivery_note.py.
    """
    if doc.voucher_type != "Sales Order" or not doc.voucher_detail_no:
        return

    from madhav.madhav.utils.stock_piece_utils import (
        int_pieces_from_qty,
        resolve_entry_length,
        resolve_entry_section_weight,
    )

    so_item = frappe.get_doc("Sales Order Item", doc.voucher_detail_no)

    for row in doc.get("sb_entries", []):
        length = resolve_entry_length(row, row.batch_no, doc.voucher_detail_no)
        section_weight = resolve_entry_section_weight(
            row, doc.item_code, length, row.batch_no
        )
        row.length = length
        row.section_weight = section_weight

        entry_qty = flt(row.qty)
        if entry_qty and length and section_weight:
            row.pieces = int_pieces_from_qty(entry_qty, length, section_weight)
        else:
            row.pieces = cint(so_item.get("pieces"))

def auto_reserve_serial_and_batch(self, based_on=None):
    """
    Intercept ERPNext's auto batch picking.
    Instead of picking any available batch, pick ALL batches whose
    average_length is within [SO item length_size, length_size + 1.5],
    sorted shortest-first.
    """
    import erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle as _sabb

    constraints = _get_batch_constraints(
        self.voucher_type, self.voucher_detail_no, self.item_code ,self.from_voucher_type
    )

    # No length constraints on this item — run original unchanged
    if constraints.get("min_length") is None and constraints.get("max_length") is None:
        _ORIGINAL_AUTO_RESERVE_SERIAL_AND_BATCH(self, based_on=based_on)
        _update_sb_entries_custom_fields(self)
        return

    based_on_value = based_on or frappe.db.get_single_value(
        "Stock Settings", "pick_serial_and_batch_based_on"
    )

    # Step 1: Get all available batches for this item+warehouse
    available_batches = _sabb.get_auto_batch_nos(
        frappe._dict(
            {
                "item_code": self.item_code,
                "warehouse": self.warehouse,
                "qty": 0,
                "based_on": based_on_value,
            }
        )
    )

    if not available_batches:
        _ORIGINAL_AUTO_RESERVE_SERIAL_AND_BATCH(self, based_on=based_on)
        _update_sb_entries_custom_fields(self)
        return

    # Step 2: Filter by average_length, sort shortest-first (length FIFO)
    all_batch_nos = [b.batch_no for b in available_batches if b.batch_no]
    eligible_ordered = _get_eligible_batches_ordered(
        all_batch_nos,
        min_length=constraints.get("min_length"),
        max_length=constraints.get("max_length"),
    )

    if not eligible_ordered:
        results = frappe.flags.setdefault("stock_reservation_results", [])
        results.append(
            frappe._dict(
                {
                    "item_code": self.item_code,
                    "batch_count": 0,
                    "status": "No Eligible Batches",
                    "min_length": constraints.get("min_length"),
                    "max_length": constraints.get("max_length"),
                }
            )
        )
        _update_sb_entries_custom_fields(self)
        return

    # Step 3: Build ordered batch list preserving qty from get_auto_batch_nos
    eligible_set = set(eligible_ordered)
    batch_qty_map = {
        b.batch_no: b for b in available_batches if b.batch_no in eligible_set
    }

    # CRITICAL FIX: Create ordered batches with ALL eligible batches
    # Don't filter by quantity - we want ALL batches within the length range
    ordered_batches = []
    for bn in eligible_ordered:
        if bn in batch_qty_map:
            batch_info = batch_qty_map[bn]
            # IMPORTANT: Set qty to the actual available quantity for ALL batches
            # This ensures all eligible batches are included in the reservation
            batch_info.qty = batch_info.qty  # Keep the full available qty
            ordered_batches.append(batch_info)

    # Debug: Log how many eligible batches we found
    frappe.log_error(
        f"Found {len(ordered_batches)} eligible batches for item {self.item_code}",
        "Stock Reservation Debug",
    )

    # Step 4: Patch get_auto_batch_nos to return ALL eligible batches
    _original_get_auto_batch_nos = _sabb.get_auto_batch_nos

    def _patched_get_auto_batch_nos(kwargs):
        if (
            kwargs.get("item_code") == self.item_code
            and kwargs.get("warehouse") == self.warehouse
        ):
            qty = flt(kwargs.get("qty"))

            # If we're requesting the full quantity for reservation
            if qty > 0:
                # We need to return batches that can fulfill the required qty
                # BUT we should include all eligible batches in the selection
                # Let's calculate cumulative qty and include all batches until we meet the requirement
                cumulative_qty = 0
                selected_batches = []

                for batch in ordered_batches:
                    if cumulative_qty < qty:
                        # Include this batch in the selection
                        # Calculate how much to take from this batch
                        remaining_needed = qty - cumulative_qty
                        take_qty = min(batch.qty, remaining_needed)

                        # Create a copy with the taken qty
                        batch_copy = frappe._dict(batch)
                        batch_copy.qty = take_qty
                        selected_batches.append(batch_copy)
                        cumulative_qty += take_qty
                    else:
                        break

                # If we still need more quantity but have no more batches,
                # return what we have (will trigger shortage error)
                if cumulative_qty < qty:
                    results = frappe.flags.setdefault("stock_reservation_results", [])
                    results.append(
                        frappe._dict(
                            {
                                "item_code": self.item_code,
                                "batch_count": len(selected_batches),
                                "status": "Partial",
                                "available_qty": cumulative_qty,
                                "requested_qty": qty,
                            }
                        )
                    )

                return selected_batches if selected_batches else ordered_batches
            else:
                # When qty is 0, return ALL eligible batches (this is for the initial selection)
                return ordered_batches

        return _original_get_auto_batch_nos(kwargs)

    _sabb.get_auto_batch_nos = _patched_get_auto_batch_nos

    try:
        _ORIGINAL_AUTO_RESERVE_SERIAL_AND_BATCH(self, based_on=based_on)

        # Collect result silently — caller will emit one consolidated message
        if hasattr(self, "sb_entries") and self.sb_entries:
            batch_count = len(
                set([entry.batch_no for entry in self.sb_entries if entry.batch_no])
            )
            results = frappe.flags.setdefault("stock_reservation_results", [])
            results.append(
                frappe._dict(
                    {
                        "item_code": self.item_code,
                        "batch_count": batch_count,
                        "status": "Reserved",
                    }
                )
            )
    finally:
        _sabb.get_auto_batch_nos = _original_get_auto_batch_nos

    _update_sb_entries_custom_fields(self)

def create_stock_reservation_entries_for_so_items(
    sales_order,
    items_details=None,
    from_voucher_type=None,
    notify=True,
):
    """
    Before calling ERPNext's original function:
    1. Read max_length per item from the dialog (via items_details)
    2. Set frappe.flags so auto_reserve_serial_and_batch can read constraints
    3. Filter out items with no eligible batches (show clear warning)
    4. Cap qty_to_reserve to eligible batch stock
    5. Create Serial and Batch Bundle with specified batch_no if provided
    """
    if from_voucher_type in ["Purchase Receipt","Stock Entry"]:
        return _ORIGINAL_CREATE_STOCK_RESERVATION_ENTRIES_FOR_SO_ITEMS(
            sales_order=sales_order,
            items_details=items_details,
            from_voucher_type=from_voucher_type,
            notify=notify,
        )
    items_details = list(items_details or [])
    frappe.flags.stock_reservation_item_ranges = {}
    frappe.flags.stock_reservation_results = []  # collected by auto_reserve_serial_and_batch

    try:
        if items_details:
            updated_items_details = []
            filtered_out_items = []
            successful_items = []

            for row in items_details:
                row = frappe._dict(row)
                so_item = frappe.get_doc(
                    "Sales Order Item", row.get("sales_order_item")
                )
                warehouse = row.get("warehouse") or so_item.warehouse
                has_batch_no = frappe.get_cached_value(
                    "Item", so_item.item_code, "has_batch_no"
                )

                # Length window for batch filter — never store None when length_size exists
                dialog_min_length = row.get("min_length")
                dialog_max_length = row.get("max_length")
                used_default_window = False
                if dialog_min_length in (None, "", 0) or dialog_max_length in (None, "", 0):
                    default_min, default_max = _default_length_window(so_item.length_size)
                    if dialog_min_length in (None, "", 0):
                        dialog_min_length = default_min
                        used_default_window = True
                    if dialog_max_length in (None, "", 0):
                        dialog_max_length = default_max
                        used_default_window = True

                # Store in flags so auto_reserve_serial_and_batch reads it
                frappe.flags.stock_reservation_item_ranges[so_item.name] = {
                    "min_length": dialog_min_length,
                    "max_length": dialog_max_length,
                }

                constraints = _get_batch_constraints(
                    "Sales Order", so_item.name, so_item.item_code, from_voucher_type
                )

                if has_batch_no:
                    eligible_stock_qty = _get_filtered_available_qty(
                        so_item.item_code, warehouse, constraints
                    )

                    # If the static default window (not one the user typed
                    # into the dialog) missed every batch, auto-widen it to
                    # the actual length range of real stock for this
                    # item/warehouse instead of silently filtering
                    # everything out.
                    if eligible_stock_qty <= 0 and used_default_window:
                        actual_min, actual_max = _get_actual_batch_length_range(
                            so_item.item_code, warehouse
                        )
                        if actual_min is not None:
                            dialog_min_length = actual_min
                            dialog_max_length = actual_max
                            frappe.flags.stock_reservation_item_ranges[so_item.name] = {
                                "min_length": dialog_min_length,
                                "max_length": dialog_max_length,
                            }
                            constraints = _get_batch_constraints(
                                "Sales Order", so_item.name, so_item.item_code, from_voucher_type
                            )
                            eligible_stock_qty = _get_filtered_available_qty(
                                so_item.item_code, warehouse, constraints
                            )

                    # Get batch details for debugging
                    debug = _get_batch_debug_details(
                        so_item.item_code, warehouse, constraints
                    )

                    if eligible_stock_qty <= 0:
                        filtered_out_items.append(
                            {
                                "item_code": so_item.item_code,
                                "message": _(
                                    "Row #{0}: No eligible batch for Item {1}. "
                                    "Length range: {2}m – {3}m. "
                                    "Available batches: [{4}]. "
                                    "Eligible batches: [{5}]."
                                ).format(
                                    so_item.idx,
                                    frappe.bold(so_item.item_code),
                                    frappe.bold(constraints.get("min_length", "-")),
                                    frappe.bold(constraints.get("max_length", "-")),
                                    frappe.bold(
                                        ", ".join(debug.available_batches) or "None"
                                    ),
                                    frappe.bold(
                                        ", ".join(debug.eligible_batches) or "None"
                                    ),
                                ),
                            }
                        )
                        continue

                    # Log eligible batches count
                    frappe.log_error(
                        f"Item {so_item.item_code}: Found {len(debug.eligible_batches)} eligible batches with total qty {eligible_stock_qty}",
                        "Stock Reservation Debug",
                    )

                    # Cap qty to what's actually available in eligible batches
                    conversion_factor = (
                        flt(row.get("conversion_factor"))
                        or flt(so_item.conversion_factor)
                        or 1
                    )
                    requested_qty = flt(row.get("qty_to_reserve"))

                    requested_qty = requested_qty * conversion_factor

                    requested_qty = min(requested_qty, eligible_stock_qty)

                    if requested_qty <= 0:
                        filtered_out_items.append(
                            {
                                "item_code": so_item.item_code,
                                "message": _(
                                    "Row #{0}: Quantity to reserve for Item {1} becomes 0 after length filter."
                                ).format(so_item.idx, frappe.bold(so_item.item_code)),
                            }
                        )
                        continue

                    row.qty_to_reserve = requested_qty / conversion_factor

                    successful_items.append(
                        {
                            "item_code": so_item.item_code,
                            "eligible_batches": len(debug.eligible_batches),
                            "total_eligible_qty": eligible_stock_qty,
                            "requested_qty": requested_qty,
                        }
                    )

                updated_items_details.append(row)

            # Show success message if items were successfully prepared
            if successful_items and notify:
                # Store for consolidated message — don't emit yet
                frappe.flags.stock_reservation_prep_items = successful_items

            if filtered_out_items:
                # Store for consolidated message — don't emit yet
                frappe.flags.stock_reservation_filtered_out = filtered_out_items

            if not updated_items_details:
                # Every row was filtered out (e.g. no batch matched the
                # length window) — surface that to the user instead of
                # silently returning, which the caller was previously
                # reporting back as a plain "success".
                if notify and filtered_out_items:
                    summary_html = _build_stock_reservation_summary(
                        [], filtered_out_items, []
                    )
                    if summary_html:
                        frappe.msgprint(
                            summary_html,
                            title=_("Stock Reservation Summary"),
                            indicator="orange",
                        )
                return {"status": "no_eligible_items", "filtered_out": filtered_out_items}

            items_details = updated_items_details

        else:
            # Called on SO submit / Update Items (no dialog) — set length window per item
            for so_item in sales_order.get("items") or []:
                if not so_item.get("reserve_stock"):
                    continue

                default_min, default_max = _default_length_window(so_item.length_size)
                if default_min is not None:
                    frappe.flags.stock_reservation_item_ranges[so_item.name] = {
                        "min_length": default_min,
                        "max_length": default_max,
                    }

                has_batch_no = frappe.get_cached_value(
                    "Item", so_item.item_code, "has_batch_no"
                )
                constraints = _get_batch_constraints(
                    "Sales Order", so_item.name, so_item.item_code, from_voucher_type
                )
                if has_batch_no:
                    so_item.qty_to_reserve = _get_filtered_available_qty(
                        so_item.item_code, so_item.warehouse, constraints
                    )


        for row in items_details:
            row["reserve_stock"] = 1

        # Call the original function
        result = _ORIGINAL_CREATE_STOCK_RESERVATION_ENTRIES_FOR_SO_ITEMS(
            sales_order=sales_order,
            items_details=items_details or None,
            from_voucher_type=from_voucher_type,
            notify=notify,
        )

        # Show final consolidated message after reservation
        if notify:
            prep_items = frappe.flags.get("stock_reservation_prep_items") or []
            filtered_out = frappe.flags.get("stock_reservation_filtered_out") or []
            reservation_results = frappe.flags.get("stock_reservation_results") or []

            if prep_items or filtered_out or reservation_results:
                summary_html = _build_stock_reservation_summary(
                    prep_items, filtered_out, reservation_results
                )
                if summary_html:
                    frappe.msgprint(
                        summary_html,
                        title=_("Stock Reservation Summary"),
                        indicator="green",
                    )

        return result

    finally:
        frappe.flags.stock_reservation_item_ranges = {}
        frappe.flags.stock_reservation_results = []
        frappe.flags.stock_reservation_prep_items = []
        frappe.flags.stock_reservation_filtered_out = []


# ---------------------------------------------------------------------------
# Bind patches — runs once at import time via hooks.py
# ---------------------------------------------------------------------------

from erpnext.stock.doctype.stock_reservation_entry import (
    stock_reservation_entry as _sre_module,
)

_ORIGINAL_CREATE_STOCK_RESERVATION_ENTRIES_FOR_SO_ITEMS = (
    _sre_module.create_stock_reservation_entries_for_so_items
)
_ORIGINAL_AUTO_RESERVE_SERIAL_AND_BATCH = (
    _sre_module.StockReservationEntry.auto_reserve_serial_and_batch
)

from frappe.query_builder.functions import Sum

def get_sre_reserved_qty_for_items_and_warehouses(
    item_code_list: list, warehouse_list: list | None = None , batch_list :list | None = None
) -> dict:
    """Returns {(item_code, warehouse, batch_no): reserved_qty}"""

    if not item_code_list:
        return {}

    sre = frappe.qb.DocType("Stock Reservation Entry")
    srei = frappe.qb.DocType("Serial and Batch Entry")

    query = (
        frappe.qb.from_(sre)
        .inner_join(srei)
        .on(sre.name == srei.parent)
        .select(
            sre.item_code,
            sre.warehouse,
            srei.batch_no,
            Sum(sre.reserved_qty - sre.delivered_qty).as_("reserved_qty"),
        )
        .where(
            (sre.docstatus == 1)
            & sre.item_code.isin(item_code_list)
            & (sre.status.notin(["Delivered", "Cancelled"]))
        )
        .groupby(sre.item_code, sre.warehouse, srei.batch_no)
    )

    if warehouse_list:
        query = query.where(sre.warehouse.isin(warehouse_list))

    data = query.run(as_dict=True)

    return {
        (d["item_code"], d["warehouse"], d["batch_no"]): d["reserved_qty"]
        for d in data
    } if data else {}