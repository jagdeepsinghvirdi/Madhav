import frappe
import json
from frappe.utils import flt, cint, nowtime, nowdate
from frappe import _

from madhav.madhav.utils.stock_piece_utils import (
	int_pieces_from_qty,
	qty_from_pieces,
	resolve_entry_length,
	resolve_entry_pieces,
	resolve_entry_section_weight,
)

def on_submit(doc, method=None):
    if not doc.items:
        return

    keys = {
        (d.item_code, d.warehouse, d.batch_no)
        for d in doc.items
        if d.item_code and d.warehouse and d.batch_no
    }

    if not keys:
        return

    item_codes = list({k[0] for k in keys})
    warehouses = list({k[1] for k in keys})
    batch_nos = list({k[2] for k in keys})

    sre_rows = frappe.db.sql(
        """
        SELECT
            parent.item_code,
            parent.warehouse,
            child.batch_no,
            parent.reserved_qty
        FROM `tabStock Reservation Entry` parent
        JOIN `tabSerial and Batch Entry` child
            ON child.parent = parent.name
        WHERE parent.docstatus = 1
          AND parent.item_code IN %(item_codes)s
          AND parent.warehouse IN %(warehouses)s
          AND child.batch_no IN %(batch_nos)s
        """,
        {
            "item_codes": item_codes,
            "warehouses": warehouses,
            "batch_nos": batch_nos,
        },
        as_dict=True,
    )

    reserved_lookup = {}
    for r in sre_rows:
        key = (r.item_code, r.warehouse, r.batch_no)
        reserved_lookup[key] = reserved_lookup.get(key, 0) + flt(r.reserved_qty)

    for row in doc.items:
        if row.invoice_qty != row.qty:
            frappe.msgprint(
                _(
                    "Row {0}: Invoice qty {1} is not equal to Delivery qty {2} "
                    "for Item {3}, Batch {4} in Warehouse {5}"
                ).format(
                    row.idx,
                    row.invoice_qty,
                    row.qty,
                    row.item_code,
                    row.batch_no,
                    row.warehouse,
                ),
                title="Error with Qty",
            )
        # Removed the throw block here.
        # If invoice_qty <= batch_qty, we don't cancel SRE, so qty will naturally be < reserved_qty which is valid.

from frappe.utils import flt, get_datetime, add_to_date, nowtime


def before_insert(self, method):
    fix_group_cost_center(self)
    populate_missing_batch_bundle(self)
    change_qty_serial_and_batch(self)
    populate_missing_batch_bundle_from_si(self)

def before_validate(self, method):
    """
    Re-sync each SO-reserved row's warehouse from its Serial and Batch
    Bundle before core stock validation runs.

    Something in the standard "Create > Delivery Note" mapper flow (core
    ERPNext's set_missing_values, which runs between before_insert and
    validate) can reset warehouse back to the Sales Order Item's nominal
    warehouse, even though before_insert already set it correctly from
    the SRE's actual reservation warehouse. Core's stock-bundle
    consistency check runs as part of validate() before our own
    "validate" hook fires, so this has to happen in before_validate to
    actually take effect in time.

    Scoped to rows tied to a Sales Order reservation (against_sales_order
    + so_detail) only — a row's bundle could exist for other reasons
    (manual entry, non-SO stock movement), and resyncing its warehouse
    from an unrelated bundle wouldn't be safe or correct there.
    """
    for row in self.items:
        if not row.against_sales_order or not row.so_detail:
            continue
        if not row.serial_and_batch_bundle:
            continue
        bundle_warehouse = frappe.db.get_value(
            "Serial and Batch Bundle", row.serial_and_batch_bundle, "warehouse"
        )
        if bundle_warehouse and row.warehouse != bundle_warehouse:
            row.warehouse = bundle_warehouse


def fix_group_cost_center(self):
    """
    Delivery Notes made via the standard "Create > Delivery Note" button
    run core ERPNext's get_mapped_doc + set_missing_values(), which can
    overwrite a row's cost_center with the company's group/root cost
    center even when the source Sales Order Item already had a valid
    leaf-level cost center set. ERPNext refuses to submit against a group
    cost center ("... is a group cost center and group cost centers
    cannot be used in transactions"), so this silently blocks submit
    despite the correct value being available one hop away on the SO.

    Re-sync from the SO Item whenever the DN row ended up on a group
    cost center (or with none at all) but the SO row has a valid leaf
    cost center to fall back to.
    """
    for item in self.items:
        if not item.so_detail:
            continue

        current = item.get("cost_center")
        if current and not frappe.db.get_value("Cost Center", current, "is_group"):
            # Already a valid leaf cost center — leave it alone.
            continue

        so_cost_center = frappe.db.get_value("Sales Order Item", item.so_detail, "cost_center")
        if not so_cost_center:
            continue

        if frappe.db.get_value("Cost Center", so_cost_center, "is_group"):
            # SO row itself only has a group center — nothing safe to copy.
            continue

        item.cost_center = so_cost_center

def populate_missing_batch_bundle_from_si(self):
    """
    Delivery Notes made via the standard "Make > Delivery Note" button on a
    Sales Invoice land here with item_code/qty/rate/si_detail/
    against_sales_invoice set, but batch_no and warehouse are not
    guaranteed to have been copied from the source Sales Invoice Item.
    Without batch_no/warehouse on the row, get_available_qty_for_item()
    has nothing to fall back on for SI-originated Deliver-as-Qty rows —
    there is no Stock Reservation Entry for a plain Sales Invoice, and no
    Serial and Batch Bundle exists yet at before_submit time (core only
    builds one during its own on_submit stock ledger step) — so
    difference_qty always came back as the full invoice_qty (a false
    shortfall on every row, every time).

    Copy batch_no/warehouse (and length/pieces/section_weight, where the
    columns exist) straight from the Sales Invoice Item here, matching
    what was already selected at invoicing time, so the SLE fallback in
    get_available_qty_for_item has real data to work with.
    """
    for item in self.items:
        if not item.against_sales_invoice or not item.si_detail:
            continue
        if item.serial_and_batch_bundle or item.batch_no:
            continue
        if not frappe.db.exists("Sales Invoice Item", item.si_detail):
            continue

        si_fields = ["batch_no", "warehouse"]
        for optional in ("length", "section_weight", "pieces", "average_length"):
            if frappe.db.has_column("Sales Invoice Item", optional):
                si_fields.append(optional)

        si_item = frappe.db.get_value(
            "Sales Invoice Item", item.si_detail, si_fields, as_dict=True
        )
        if not si_item or not si_item.get("batch_no"):
            continue

        item.batch_no = si_item.batch_no
        if hasattr(item, "use_serial_batch_fields"):
            item.use_serial_batch_fields = 1
        if si_item.get("warehouse") and not item.warehouse:
            item.warehouse = si_item.warehouse

        if si_item.get("length") and hasattr(item, "length") and not flt(item.get("length")):
            item.length = si_item.length
        if si_item.get("section_weight") and hasattr(item, "section_weight") and not flt(item.get("section_weight")):
            item.section_weight = si_item.section_weight
        if si_item.get("pieces") and hasattr(item, "pieces") and not flt(item.get("pieces")):
            item.pieces = si_item.pieces
        # SI's "average_length" maps to DN Item's "length_size" field
        # (same concept, different fieldname).
        if si_item.get("average_length") and hasattr(item, "length_size") and not flt(item.get("length_size")):
            item.length_size = si_item.average_length

def populate_missing_batch_bundle(self):
    """
    Delivery Notes made via the standard "Create > Delivery Note" button
    (core get_mapped_doc) never run our SRE/batch-bundle logic — that only
    happens in make_delivery_note_custom (the "Get Items From > Sales
    Order" dialog). Rows can end up with neither batch_no nor
    serial_and_batch_bundle set, even though the SO has active,
    batch-tracked Stock Reservation Entries backing the row.

    Attach the correct Serial and Batch Bundle here so both creation paths
    converge on the same data shape before any invoice_qty /
    reconciliation logic runs.

    Reservations for a single SO line can be spread across multiple SREs,
    and those SREs can legitimately sit in DIFFERENT warehouses. A single
    DN Item row can only reference one warehouse/bundle, so:
      - SREs are grouped by warehouse first.
      - The warehouse holding the most reserved qty becomes this row's
        bundle/warehouse.
      - Any remaining warehouses get their own additional DN Item rows,
        rather than being silently merged into the primary bundle (which
        previously caused over-counted / warehouse-inconsistent bundles).
    """
    for item in self.items:
        if not item.against_sales_order or not item.so_detail:
            continue
        if item.serial_and_batch_bundle or item.batch_no:
            continue

        sre_list = frappe.get_all(
            "Stock Reservation Entry",
            filters={
                "voucher_type": "Sales Order",
                "voucher_no": item.against_sales_order,
                "voucher_detail_no": item.so_detail,
                "docstatus": 1,
            },
            fields=[
                "name",
                "reservation_based_on",
                "item_code",
                "warehouse",
                "has_serial_no",
                "has_batch_no",
                "voucher_detail_no",
            ],
        )

        batch_sres = [s for s in sre_list if s.reservation_based_on == "Serial and Batch"]
        if not batch_sres:
            continue

        by_warehouse = {}
        for sre in batch_sres:
            by_warehouse.setdefault(sre.warehouse, []).append(sre)

        ordered = sorted(
            by_warehouse.items(),
            key=lambda kv: _total_sre_reserved_qty(kv[1]),
            reverse=True,
        )
        primary_warehouse, primary_sres = ordered[0]

        if len(primary_sres) == 1:
            bundle_name = get_ssb_bundle_for_voucher_from_sre(primary_sres[0])
        else:
            bundle_name = _build_merged_batch_bundle(item, primary_sres, primary_warehouse)

        if bundle_name:
            item.serial_and_batch_bundle = bundle_name
            item.warehouse = primary_warehouse

        for other_warehouse, other_sres in ordered[1:]:
            _append_dn_row_for_other_warehouse(self, item, other_warehouse, other_sres)


def _total_sre_reserved_qty(sre_rows):
    """Sum of undelivered reserved qty across a list of SREs (used to pick
    the primary warehouse when a single SO line's reservations span more
    than one warehouse)."""
    total = 0
    for sre in sre_rows:
        rows = frappe.get_all(
            "Serial and Batch Entry",
            filters={"parent": sre.name, "parenttype": "Stock Reservation Entry"},
            fields=["qty", "delivered_qty"],
        )
        total += sum(flt(r.qty) - flt(r.delivered_qty) for r in rows)
    return total


def _append_dn_row_for_other_warehouse(doc, source_item, warehouse, sre_rows):
    """
    Add a separate Delivery Note Item row for SO-line reservations that
    sit in a different warehouse than the row's primary bundle, instead
    of dropping them or silently merging them into the wrong warehouse's
    bundle (which previously caused over-counted quantities).
    """
    if len(sre_rows) == 1:
        bundle_name = get_ssb_bundle_for_voucher_from_sre(sre_rows[0])
    else:
        bundle_name = _build_merged_batch_bundle(source_item, sre_rows, warehouse)
    if not bundle_name:
        return

    new_row = doc.append("items", {})
    new_row.item_code = source_item.item_code
    new_row.item_name = source_item.item_name
    new_row.against_sales_order = source_item.against_sales_order
    new_row.so_detail = source_item.so_detail
    new_row.rate = source_item.rate
    new_row.uom = source_item.uom
    new_row.conversion_factor = source_item.conversion_factor or 1
    new_row.custom_deliver_as_qty = source_item.custom_deliver_as_qty
    new_row.warehouse = warehouse
    new_row.serial_and_batch_bundle = bundle_name

    bundle = frappe.get_doc("Serial and Batch Bundle", bundle_name)
    row_qty = sum(abs(flt(e.qty)) for e in bundle.entries if e.batch_no)
    new_row.qty = row_qty
    new_row.stock_qty = row_qty * flt(new_row.conversion_factor)
    new_row.amount = flt(new_row.rate) * row_qty


def _backfill_bundle_dimensions(entry, item_code, so_detail, avail_qty):
    """Resolve missing length / section_weight / pieces on a raw
    Serial and Batch Entry row using the same resolution helpers as the
    single-SRE bundle builder (get_ssb_bundle_for_voucher_from_sre), so
    merged multi-SRE bundles get consistently-computed dimensions
    instead of whatever happened to already be stored on the source
    SRE's batch row.
    """
    length = resolve_entry_length(entry, entry.get("batch_no"), so_detail)
    section_weight = resolve_entry_section_weight(
        entry, item_code, length, entry.get("batch_no")
    )
    pieces = resolve_entry_pieces(entry, avail_qty, length, section_weight)
    entry["length"] = length
    entry["section_weight"] = section_weight
    entry["pieces"] = pieces
    return entry


def _build_merged_batch_bundle(item, sre_rows, warehouse):
    """
    Merge batch entries across multiple SREs — all reserved from the SAME
    warehouse (grouping now happens in populate_missing_batch_bundle
    before this is called) — into a single DN bundle.

    Reads batch rows directly (same query/logic as
    get_ssb_bundle_for_voucher_from_sre) instead of going through core
    ERPNext's get_ssb_bundle_for_voucher, and subtracts delivered_qty
    per entry before merging. Previously this copied each source
    bundle's full reserved qty regardless of prior partial deliveries,
    over-counting quantity in the multi-batch case.

    Uses normal doctype validation (no ignore_validate/ignore_links
    flags) to match how get_ssb_bundle_for_voucher_from_sre creates
    single-SRE bundles.
    """
    merged_entries = []

    for sre in sre_rows:
        sb_entries = frappe.db.sql(
            """
            SELECT
                serial_no,
                batch_no,
                qty,
                delivered_qty,
                pieces,
                length,
                section_weight,
                warehouse
            FROM `tabSerial and Batch Entry`
            WHERE parent = %s
              AND parenttype = 'Stock Reservation Entry'
              AND qty > IFNULL(delivered_qty, 0)
            ORDER BY idx
            """,
            sre.name,
            as_dict=True,
        )

        for row in sb_entries:
            avail_qty = flt(row.qty) - flt(row.delivered_qty)
            if avail_qty <= 0:
                continue
            entry = dict(row)
            _backfill_bundle_dimensions(entry, item.item_code, item.so_detail, avail_qty)
            entry["qty"] = avail_qty
            merged_entries.append(entry)

    if not merged_entries:
        return None

    new_bundle = frappe.new_doc("Serial and Batch Bundle")
    new_bundle.item_code = item.item_code
    new_bundle.warehouse = warehouse
    new_bundle.voucher_type = "Delivery Note"
    new_bundle.type_of_transaction = "Outward"

    for e in merged_entries:
        new_bundle.append(
            "entries",
            {
                "batch_no": e.get("batch_no"),
                "serial_no": e.get("serial_no"),
                "qty": -abs(flt(e.get("qty"))),
                "warehouse": warehouse,
                "pieces": flt(e.get("pieces")),
                "length": flt(e.get("length")),
                "section_weight": flt(e.get("section_weight")),
            },
        )

    new_bundle.total_qty = -sum(abs(flt(e.get("qty"))) for e in merged_entries)
    new_bundle.flags.ignore_permissions = True
    new_bundle.insert(ignore_permissions=True)
    return new_bundle.name


def _resolve_deliver_as_qty(row):
    """
    Deliver as Qty can come from either source doc a DN row descends
    from:
      - Sales Order (existing) — via row.against_sales_order
      - Sales Invoice (new) — via row.against_sales_invoice, for
        standalone SI-originated deliveries with no SO link at all
        (the common real-world case per client: SIs are created
        directly, not via Sales Order).
    against_sales_order takes precedence if a row somehow has both.
    Rows with neither get 0 — normal delivery, untouched.
    """
    if row.against_sales_order:
        return frappe.db.get_value(
            "Sales Order", row.against_sales_order, "deliver_as_qty"
        )
    if row.against_sales_invoice:
        return frappe.db.get_value(
            "Sales Invoice", row.against_sales_invoice, "deliver_as_qty"
        )
    return 0


def validate(self, method):

    for row in self.items:
        deliver_as_qty = _resolve_deliver_as_qty(row)

        if deliver_as_qty and not row.invoice_qty:
            frappe.throw(f"Invoice Qty is mandatory for row {row.idx}")
        if deliver_as_qty and not row.custom_deliver_as_qty:
            row.custom_deliver_as_qty = deliver_as_qty

    if _has_deliver_as_qty_over_delivery(self):
        for args in self.status_updater:
            if (
                args.get("target_dt") == "Sales Order Item"
                and args.get("overflow_type") == "delivery"
            ):
                args["validate_qty"] = False


def _has_deliver_as_qty_over_delivery(doc):
    for row in doc.items:
        if not cint(row.custom_deliver_as_qty) or not flt(row.invoice_qty):
            continue
        if flt(row.invoice_qty) > flt(row.qty):
            return True
        if row.so_detail:
            so_qty = flt(frappe.db.get_value("Sales Order Item", row.so_detail, "qty"))
            if flt(row.invoice_qty) > so_qty:
                return True
    return False


def get_batch_available_pieces(item_code, warehouse, batch_no):
    """
    Current available pieces for a batch, derived from Piece Stock Ledger
    Entry. batch_no on the PSLE itself isn't reliably populated, so we join
    through the Serial and Batch Bundle's entries (which do carry batch_no).
    """
    result = frappe.db.sql(
        """
        SELECT SUM(psle.actual_qty) as total_pieces
        FROM `tabPiece Stock Ledger Entry` psle
        INNER JOIN `tabSerial and Batch Entry` sbe
            ON sbe.parent = psle.serial_and_batch_bundle
        WHERE psle.item_code = %s
          AND psle.warehouse = %s
          AND sbe.batch_no = %s
          AND psle.is_cancelled = 0
        """,
        (item_code, warehouse, batch_no),
        as_dict=True,
    )
    return flt(result[0].total_pieces) if result and result[0].total_pieces else 0


def get_ssb_bundle_for_voucher_from_sre(sre):
	"""Create a DN Serial/Batch Bundle from SRE with reservation length and integer pieces."""
	sre_row = sre if isinstance(sre, dict) else sre.as_dict()
	sre_name = sre_row.get("name")
	so_detail = sre_row.get("voucher_detail_no")
	item_code = sre_row.get("item_code")

	sb_entries = frappe.db.sql(
		"""
		SELECT
			serial_no,
			batch_no,
			qty,
			delivered_qty,
			pieces,
			length,
			section_weight,
			warehouse
		FROM `tabSerial and Batch Entry`
		WHERE parent = %s
		  AND parenttype = 'Stock Reservation Entry'
		  AND qty > IFNULL(delivered_qty, 0)
		ORDER BY idx
		""",
		sre_name,
		as_dict=True,
	)
	if not sb_entries:
		return None

	bundle = frappe.new_doc("Serial and Batch Bundle")
	bundle.type_of_transaction = "Outward"
	bundle.voucher_type = "Delivery Note"
	bundle.posting_date = nowdate()
	bundle.posting_time = nowtime()

	for field in ("item_code", "warehouse", "has_serial_no", "has_batch_no"):
		setattr(bundle, field, sre_row[field])

	for row in sb_entries:
		avail_qty = flt(row.qty) - flt(row.delivered_qty)
		length = resolve_entry_length(row, row.batch_no, so_detail)
		section_weight = resolve_entry_section_weight(
			row, item_code, length, row.batch_no
		)
		pieces = resolve_entry_pieces(row, avail_qty, length, section_weight)

		bundle.append(
			"entries",
			{
				"serial_no": row.serial_no,
				"batch_no": row.batch_no,
				"qty": avail_qty,
				"pieces": pieces,
				"length": length,
				"section_weight": section_weight,
				"warehouse": row.warehouse or sre_row.get("warehouse"),
			},
		)

	bundle.flags.ignore_permissions = True
	bundle.save(ignore_permissions=True)
	return bundle.name


def _apply_reserved_dims_to_dn_item(dn_item, so_item, sre):
	"""Stamp SO/reservation length and integer pieces on the DN row."""
	from madhav.madhav.utils.stock_piece_utils import int_pieces_from_qty

	reserved_stock_qty = flt(sre.reserved_qty) - flt(sre.delivered_qty)
	so_length = flt(so_item.get("length_size"))

	if so_length:
		if hasattr(dn_item, "length_size"):
			dn_item.length_size = so_length
		if hasattr(dn_item, "length_sizeso"):
			dn_item.length_sizeso = so_length

	# Prefer pieces already on this SRE's batch rows (per-batch, not full SO)
	sre_name = sre.name if hasattr(sre, "name") else sre.get("name")
	sre_pieces = 0
	if sre_name and frappe.db.has_column("Serial and Batch Entry", "pieces"):
		sre_pieces = cint(
			frappe.db.sql(
				"""
				select coalesce(sum(pieces), 0)
				from `tabSerial and Batch Entry`
				where parent = %s and parenttype = 'Stock Reservation Entry'
				""",
				sre_name,
			)[0][0]
		)

	section_weight = flt(so_item.get("section_weight"))
	if not section_weight and so_item.item_code:
		section_weight = flt(
			frappe.db.get_value("Item", so_item.item_code, "weight_per_meter") or 0
		)

	if sre_pieces > 0:
		scaled_pieces = sre_pieces
	elif so_length and section_weight and reserved_stock_qty > 0:
		# Single ceil from reserved weight — do not ceil(SO pieces × ratio)
		scaled_pieces = int_pieces_from_qty(
			reserved_stock_qty, so_length, section_weight
		)
	else:
		so_pieces = cint(so_item.get("pieces"))
		so_stock_qty = flt(so_item.stock_qty) or (
			flt(so_item.qty) * flt(so_item.conversion_factor or 1)
		)
		if so_pieces and so_stock_qty > 0 and reserved_stock_qty > 0:
			# Already-integer SO pieces: round proportionally (no second ceil)
			scaled_pieces = max(
				0, int(round(so_pieces * reserved_stock_qty / so_stock_qty))
			)
		else:
			scaled_pieces = so_pieces

	if hasattr(dn_item, "lengthpieces_so") and scaled_pieces:
		dn_item.lengthpieces_so = scaled_pieces
	if hasattr(dn_item, "pieces") and scaled_pieces:
		dn_item.pieces = scaled_pieces

	if hasattr(dn_item, "section_weight"):
		if section_weight:
			dn_item.section_weight = section_weight
		elif so_length and scaled_pieces and reserved_stock_qty:
			dn_item.section_weight = (reserved_stock_qty * 1000) / (
				scaled_pieces * so_length
			)


def change_qty_serial_and_batch(self):
    for item in self.items:
        if not item.against_sales_order:
            continue

        deliver_as_qty = frappe.db.get_value(
            "Sales Order",
            item.against_sales_order,
            "deliver_as_qty",
        )

        if not item.serial_and_batch_bundle:
            continue

        bundle = frappe.get_doc(
            "Serial and Batch Bundle",
            item.serial_and_batch_bundle,
        )
        bundle.reload()

        # IMPORTANT: Use item.qty here, NOT invoice_qty.
        target_qty = flt(item.qty)

        batch_entries = [e for e in bundle.entries if e.batch_no]
        if not batch_entries:
            continue

        total_original_qty = sum(abs(flt(e.qty)) for e in batch_entries)
        if not total_original_qty:
            continue

        # Plain batch items (no length/section_weight) must not go through the
        # steel piece conversion — that path forces qty to 0 when dims are missing.
        # Also do NOT cap by Batch.batch_qty: that field is not available stock and
        # incorrectly shrinks deliver-as-qty / SR-adjusted bundles on cancel/submit.
        has_piece_dims = any(
            flt(e.length) and flt(e.section_weight) for e in batch_entries
        )
        if not has_piece_dims:
            desired_qty = []
            for entry in batch_entries:
                original = abs(flt(entry.qty))
                ratio = (original / total_original_qty) if total_original_qty else 0
                desired_qty.append(target_qty * ratio)
            allocated_total_qty = sum(desired_qty)
            for i, entry in enumerate(batch_entries):
                entry.qty = -desired_qty[i]
                if hasattr(entry, "pieces"):
                    entry.pieces = 0
            item.qty = allocated_total_qty
            item.stock_qty = allocated_total_qty
            item.amount = flt(item.rate) * allocated_total_qty
            item.base_amount = item.amount * flt(self.conversion_rate or 1)
            if hasattr(item, "pieces"):
                item.pieces = 0
            bundle.total_qty = -allocated_total_qty
            bundle.flags.ignore_validate = True
            bundle.flags.ignore_links = True
            bundle.save(ignore_permissions=True)
            bundle.reload()
            continue

        # ── Step 1 (steel): Proportional QTY from reserved/fetched weight ──
        # Do NOT cap by Batch.batch_qty — that field is receipt size, not
        # available stock, and was shrinking reserved qty on DN save
        # (e.g. 0.445 → 0.443).
        desired_qty = []
        for entry in batch_entries:
            original = abs(flt(entry.qty))
            ratio = original / total_original_qty
            desired_qty.append(target_qty * ratio)

        # Absorb floating remainder on the first entry
        leftover_qty = target_qty - sum(desired_qty)
        if abs(leftover_qty) > 0.0000001 and desired_qty:
            desired_qty[0] += leftover_qty

        # ── Step 2: Integer pieces for display / piece ledger (do NOT rewrite weight) ──
        desired_pieces = []
        for i, entry in enumerate(batch_entries):
            entry_length = resolve_entry_length(
                entry, entry.batch_no, item.so_detail
            )
            entry_section_weight = resolve_entry_section_weight(
                entry, item.item_code, entry_length, entry.batch_no
            )
            if entry_length and not flt(entry.length):
                entry.length = entry_length
            if entry_section_weight and not flt(entry.section_weight):
                entry.section_weight = entry_section_weight

            existing_pieces = cint(flt(entry.pieces))
            if existing_pieces > 0:
                pieces = existing_pieces
            else:
                pieces = int_pieces_from_qty(
                    desired_qty[i], entry_length, entry_section_weight
                )
            desired_pieces.append(pieces)

        target_total_pieces = sum(desired_pieces)

        # ── Step 3: Cap integer pieces against batch availability, spill leftover ──
        available_pieces_cache = {
            entry.batch_no: int(round(get_batch_available_pieces(
                item.item_code, entry.warehouse or item.warehouse, entry.batch_no
            )))
            for entry in batch_entries
        }

        for i, entry in enumerate(batch_entries):
            available = available_pieces_cache.get(entry.batch_no, 0)
            if available and desired_pieces[i] > available:
                desired_pieces[i] = available

        actual_total_pieces = sum(desired_pieces)
        leftover_pieces = target_total_pieces - actual_total_pieces

        for i, entry in enumerate(batch_entries):
            if leftover_pieces <= 0:
                break
            available = available_pieces_cache.get(entry.batch_no, 0)
            if not available:
                continue
            room = available - desired_pieces[i]
            if room > 0:
                take = min(room, leftover_pieces)
                desired_pieces[i] += int(take)
                leftover_pieces -= int(take)

        if leftover_pieces > 0:
            frappe.msgprint(
                f"Not enough available pieces for {item.item_code} in "
                f"{item.warehouse}: short by {leftover_pieces} pieces.",
                indicator="orange",
                alert=True,
            )

        # ── Step 4: Keep reserved weight; only stamp integer pieces ──
        total_allocated_pieces = 0
        last_section_weight = 0
        for i, entry in enumerate(batch_entries):
            pieces = int(desired_pieces[i])
            entry.qty = -desired_qty[i]
            entry.pieces = pieces
            total_allocated_pieces += pieces
            last_section_weight = flt(entry.section_weight)

        allocated_total_qty = sum(desired_qty)

        item.qty = allocated_total_qty
        item.stock_qty = allocated_total_qty * flt(item.conversion_factor or 1)
        item.amount = flt(item.rate) * allocated_total_qty
        item.base_amount = item.amount * flt(self.conversion_rate or 1)
        item.pieces = int(total_allocated_pieces)
        if last_section_weight:
            item.section_weight = last_section_weight
        bundle.total_qty = -allocated_total_qty
        bundle.flags.ignore_validate = True
        bundle.flags.ignore_links = True
        bundle.save(ignore_permissions=True)
        bundle.reload()

        frappe.logger().info(
            f"Updated Bundle {bundle.name}: Total Qty={bundle.total_qty}, "
            f"Total Pieces={total_allocated_pieces}, "
            f"Entries={[{'batch': d.batch_no, 'qty': d.qty, 'pieces': d.pieces} for d in bundle.entries]}"
        )

def get_batch_qty_from_sle(item_code, warehouse, batch_no):
    """Real physical stock for a batch, from Stock Ledger Entry via the
    Serial and Batch Entry join (batch_no is not reliably populated
    directly on the SLE itself)."""
    result = frappe.db.sql(
        """
        SELECT SUM(sle.actual_qty) as qty
        FROM `tabStock Ledger Entry` sle
        INNER JOIN `tabSerial and Batch Entry` sbe
            ON sbe.parent = sle.serial_and_batch_bundle
        WHERE sle.item_code = %s
          AND sle.warehouse = %s
          AND sbe.batch_no = %s
          AND sle.is_cancelled = 0
        """,
        (item_code, warehouse, batch_no),
        as_dict=True,
    )
    return flt(result[0].qty) if result and result[0].qty else 0


def get_available_qty_for_item(row):
    """"Available" must mean the batch's real physical stock, not the
    tentative reservation amount sitting on this row's bundle. A
    reservation can legitimately be smaller than what a batch truly
    holds - checking the shortfall against the reservation size alone
    was falsely flagging perfectly healthy deliveries as short and
    dragging them into an unnecessary Stock Reconciliation, which then
    double-counted against the DN's own bundle subtraction.
    """
    warehouse = row.warehouse
    if not warehouse and row.serial_and_batch_bundle:
        warehouse = frappe.db.get_value(
            "Serial and Batch Bundle", row.serial_and_batch_bundle, "warehouse"
        )

    batch_available = 0
    if row.batch_no and warehouse:
        batch_available = get_batch_qty_from_sle(row.item_code, warehouse, row.batch_no)

    if row.serial_and_batch_bundle:
        sbb = frappe.get_doc("Serial and Batch Bundle", row.serial_and_batch_bundle)
        bundle_qty = sum(abs(flt(e.qty)) for e in sbb.entries if e.batch_no)
        if batch_available:
            return max(batch_available, bundle_qty)
        if bundle_qty:
            return bundle_qty

    if batch_available:
        return batch_available

    if row.against_sales_order and row.so_detail:
        sre_rows = frappe.get_all(
            "Stock Reservation Entry",
            filters={
                "voucher_type": "Sales Order",
                "voucher_no": row.against_sales_order,
                "voucher_detail_no": row.so_detail,
                "docstatus": 1,
            },
            fields=["reserved_qty", "delivered_qty"],
        )
        if sre_rows:
            return sum(flt(d.reserved_qty) - flt(d.delivered_qty) for d in sre_rows)

    return 0



def update_bundle_to_invoice_qty(item, invoice_qty, qty, deliver_as_qty):
    if not deliver_as_qty:
        return
    if not item.serial_and_batch_bundle:
        return

    bundle = frappe.get_doc("Serial and Batch Bundle", item.serial_and_batch_bundle)
    bundle.reload()

    batch_entries = [e for e in bundle.entries if e.batch_no]
    if not batch_entries:
        return

    total_original_qty = sum(abs(flt(e.qty)) for e in batch_entries)
    if not total_original_qty:
        return

    target_qty = flt(invoice_qty)

    # No change needed
    if abs(target_qty - total_original_qty) < 0.0001:
        return

    expanding = target_qty > total_original_qty + 0.0001
    desired = []
    for entry in batch_entries:
        original = abs(flt(entry.qty))
        ratio = original / total_original_qty
        desired_qty = target_qty * ratio

        if not expanding:
            batch_qty = flt(
                frappe.db.get_value("Batch", entry.batch_no, "batch_qty") or 0
            )
            if batch_qty > 0:
                desired_qty = min(desired_qty, batch_qty)
        desired.append(desired_qty)

    actual_total = sum(desired)
    leftover = target_qty - actual_total

    if not expanding:
        for i, entry in enumerate(batch_entries):
            if abs(leftover) < 0.0001:
                break
            batch_qty = flt(
                frappe.db.get_value("Batch", entry.batch_no, "batch_qty") or 0
            )
            if batch_qty <= 0:
                desired[i] += leftover
                leftover = 0
                break
            room = batch_qty - desired[i]
            if room > 0:
                take = min(room, leftover)
                desired[i] += take
                leftover -= take
    elif abs(leftover) > 0.0001:
        desired[0] += leftover
        leftover = 0

    total_pieces = 0

    for i, entry in enumerate(batch_entries):
        entry_length = resolve_entry_length(
            entry, entry.batch_no, item.so_detail
        )
        entry_section_weight = resolve_entry_section_weight(
            entry, item.item_code, entry_length, entry.batch_no
        )
        if entry_length and not flt(entry.length):
            entry.length = entry_length
        if entry_section_weight and not flt(entry.section_weight):
            entry.section_weight = entry_section_weight

        pieces = int_pieces_from_qty(desired[i], entry_length, entry_section_weight)
        entry.qty = -desired[i]
        entry.pieces = pieces
        total_pieces += pieces

    if hasattr(item, "pieces"):
        item.pieces = int(total_pieces)

    bundle.total_qty = -target_qty
    bundle.flags.ignore_validate = True
    bundle.flags.ignore_links = True
    bundle.save(ignore_permissions=True)

from frappe.utils import get_datetime, add_to_date, nowtime


def before_submit(self, method):
    for i in self.items:
        if not i.custom_deliver_as_qty:
            i.difference_qty = 0
            continue

        available_qty = get_available_qty_for_item(i)

        if flt(i.invoice_qty) > available_qty:
            i.difference_qty = flt(i.invoice_qty) - available_qty
        else:
            i.difference_qty = 0

        no_shortfall = flt(i.difference_qty) <= 0 and flt(i.invoice_qty) > 0 and i.custom_deliver_as_qty

        if no_shortfall and i.serial_and_batch_bundle:
            i.qty = flt(i.invoice_qty)
            i.stock_qty = flt(i.invoice_qty) * flt(i.conversion_factor or 1)
            update_bundle_to_invoice_qty(i, flt(i.invoice_qty), flt(i.qty), flt(i.custom_deliver_as_qty))
        elif no_shortfall and i.batch_no:
            # SI-originated rows: no bundle yet — core builds the Serial
            # and Batch Bundle itself from batch_no + use_serial_batch_fields
            # during its own on_submit stock ledger step, using this
            # corrected qty. Nothing to proportionally rescale here.
            i.qty = flt(i.invoice_qty)
            i.stock_qty = flt(i.invoice_qty) * flt(i.conversion_factor or 1)
            if hasattr(i, "use_serial_batch_fields"):
                i.use_serial_batch_fields = 1

    cancel_stock_reservations_from_so(self)
    create_stock_reconciliation(self)
    self.calculate_taxes_and_totals()

CANCELLED_SRE_COMMENT_PREFIX = "MADHAV_DN_CANCELLED_SRE::"


def cancel_stock_reservations_from_so(doc):
    """
    Snapshot active SO reservations linked to this DN, then cancel only those
    needed for Deliver-as-Qty overage (difference_qty > 0).
    """
    snapshots = []
    seen_sre = set()
    cancel_errors = []
    so_details_needing_cancel = {
        row.so_detail
        for row in doc.items
        if row.so_detail and flt(row.difference_qty) > 0
    }

    for row in doc.items:
        if not row.against_sales_order or not row.so_detail:
            continue

        sre_list = frappe.get_all(
            "Stock Reservation Entry",
            filters={
                "voucher_type": "Sales Order",
                "voucher_no": row.against_sales_order,
                "voucher_detail_no": row.so_detail,
                "docstatus": 1,
            },
            pluck="name",
        )

        for sre_name in sre_list:
            if sre_name in seen_sre:
                continue
            seen_sre.add(sre_name)
            try:
                sre = frappe.get_doc("Stock Reservation Entry", sre_name)
                if sre.docstatus != 1:
                    continue
                snapshots.append(_snapshot_sre(sre))

                if row.so_detail in so_details_needing_cancel:
                    sre.flags.ignore_permissions = True
                    sre.cancel()
            except Exception:
                frappe.log_error(
                    title="SRE Snapshot/Cancel Error",
                    message=f"{sre_name}\n{frappe.get_traceback()}",
                )
                cancel_errors.append(sre_name)

    if snapshots and doc.name:
        _store_cancelled_sre_snapshot(doc.name, snapshots)

    if cancel_errors:
        frappe.throw(
            _(
                "Failed to snapshot/cancel Stock Reservation Entry(ies) for Delivery Note {0}:<br>{1}"
            ).format(frappe.bold(doc.name), "<br>".join(frappe.bold(e) for e in cancel_errors)),
            title=_("Stock Reservation Error"),
        )


def _snapshot_sre(sre):
	"""Capture fields needed to recreate a Stock Reservation Entry."""
	return {
		"name": sre.name,
		"item_code": sre.item_code,
		"warehouse": sre.warehouse,
		"company": sre.company,
		"stock_uom": sre.stock_uom,
		"voucher_type": sre.voucher_type,
		"voucher_no": sre.voucher_no,
		"voucher_detail_no": sre.voucher_detail_no,
		"voucher_qty": flt(sre.voucher_qty),
		"reserved_qty": flt(sre.reserved_qty),
		"delivered_qty": flt(sre.delivered_qty),
		"available_qty": flt(sre.available_qty),
		"reservation_based_on": sre.reservation_based_on,
		"has_batch_no": cint(sre.has_batch_no),
		"has_serial_no": cint(sre.has_serial_no),
		"from_voucher_type": sre.from_voucher_type,
		"from_voucher_no": sre.from_voucher_no,
		"from_voucher_detail_no": sre.from_voucher_detail_no,
		"sb_entries": [
			{
				"batch_no": e.batch_no,
				"serial_no": e.serial_no,
				"qty": flt(e.qty),
				"delivered_qty": flt(e.get("delivered_qty")),
				"warehouse": e.warehouse,
				"pieces": flt(e.get("pieces")),
				"length": flt(e.get("length")),
				"section_weight": flt(e.get("section_weight")),
			}
			for e in (sre.get("sb_entries") or [])
		],
	}


def _store_cancelled_sre_snapshot(delivery_note, snapshots):
    """Persist SRE snapshots on the DN for restore-on-cancel."""
    existing = frappe.get_all(
        "Comment",
        filters={
            "reference_doctype": "Delivery Note",
            "reference_name": delivery_note,
            "comment_type": "Info",
        },
        fields=["name", "content"],
    )
    for row in existing:
        content = row.content or ""
        if content.startswith(CANCELLED_SRE_COMMENT_PREFIX) or content.startswith(
            "MADHAV_DN_CANCELLED_SRE::"
        ):
            frappe.delete_doc("Comment", row.name, ignore_permissions=True, force=True)

    frappe.get_doc(
        {
            "doctype": "Comment",
            "comment_type": "Info",
            "reference_doctype": "Delivery Note",
            "reference_name": delivery_note,
            "content": CANCELLED_SRE_COMMENT_PREFIX + json.dumps(snapshots),
        }
    ).insert(ignore_permissions=True)


def _load_cancelled_sre_snapshot(delivery_note):
	"""Load SRE snapshot stored on the DN."""
	prefixes = (CANCELLED_SRE_COMMENT_PREFIX, "MADHAV_DN_CANCELLED_SRE::")
	comments = frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": "Delivery Note",
			"reference_name": delivery_note,
			"comment_type": "Info",
		},
		fields=["name", "content"],
		order_by="creation desc",
		limit=20,
	)
	for comment in comments:
		content = comment.content or ""
		for prefix in prefixes:
			if not content.startswith(prefix):
				continue
			payload = content[len(prefix) :]
			try:
				snapshots = json.loads(payload)
			except Exception:
				frappe.log_error(
					title="DN Cancel - Invalid SRE Snapshot",
					message=f"{delivery_note}\n{content}",
				)
				frappe.throw(
					_(
						"Could not read stock reservation snapshot for Delivery Note {0}. "
						"Cancellation aborted so reservations are not left inconsistent."
					).format(frappe.bold(delivery_note)),
					title=_("Invalid Reservation Snapshot"),
				)
			if not isinstance(snapshots, list):
				frappe.throw(
					_(
						"Stock reservation snapshot for Delivery Note {0} is invalid "
						"(expected a list of reservations)."
					).format(frappe.bold(delivery_note)),
					title=_("Invalid Reservation Snapshot"),
				)
			return snapshots, comment.name
	return [], None


def on_cancel(doc, method=None):
	"""Release stock adjustments, restore reserved qty, re-sync SO on DN cancel."""
	if getattr(doc, "is_return", 0):
		return

	release_stock_used_by_delivery_note(doc)
	reverse_sre_delivery_for_dn(doc)
	restore_stock_reservations_after_cancel(doc)
	update_sales_order_quantities_on_cancel(doc)


def release_stock_used_by_delivery_note(doc):
	"""Cancel Stock Reconciliations created for this DN (keep cancelled docs for audit)."""
	cancel_stock_reconciliations_for_delivery_note(doc.name, delete=False)


def reverse_sre_delivery_for_dn(doc):
	"""Reduce SRE delivered_qty only by what this DN still accounts for."""
	if not frappe.db.get_single_value("Stock Settings", "enable_stock_reservation"):
		return

	dn_qty_by_detail = {}
	for item in doc.items:
		if not item.against_sales_order or not item.so_detail:
			continue
		dn_qty_by_detail.setdefault(
			item.so_detail,
			frappe._dict(
				sales_order=item.against_sales_order,
				qty=0,
				dn_items=[],
			),
		)
		dn_qty_by_detail[item.so_detail].qty += flt(item.stock_qty)
		dn_qty_by_detail[item.so_detail].dn_items.append(item)

	for so_detail, info in dn_qty_by_detail.items():
		other_delivered = _delivered_qty_excluding_dn(so_detail, doc.name)
		sre_names = frappe.get_all(
			"Stock Reservation Entry",
			filters={
				"docstatus": 1,
				"voucher_type": "Sales Order",
				"voucher_no": info.sales_order,
				"voucher_detail_no": so_detail,
				"status": ["in", ["Partially Delivered", "Delivered"]],
			},
			pluck="name",
			order_by="creation desc",
		)
		if not sre_names:
			continue

		sres = [frappe.get_doc("Stock Reservation Entry", n) for n in sre_names]
		current_delivered = sum(flt(s.delivered_qty) for s in sres)
		excess = current_delivered - other_delivered
		if excess <= 0:
			continue

		qty_to_undeliver = min(excess, flt(info.qty))
		if qty_to_undeliver <= 0:
			continue

		batch_qty = {}
		for item in info.dn_items:
			for batch_no, qty in _dn_item_batch_qty_map(item).items():
				batch_qty[batch_no] = batch_qty.get(batch_no, 0) + qty

		for sre in sres:
			if qty_to_undeliver <= 0:
				break
			can = min(flt(sre.delivered_qty), qty_to_undeliver)
			if can <= 0:
				continue
			undelivered = _undeliver_sre_qty(sre, can, batch_qty)
			if undelivered <= 0:
				continue
			sre.db_set("delivered_qty", flt(sre.delivered_qty) - undelivered, update_modified=False)
			sre.reload()
			sre.update_status()
			sre.update_reserved_stock_in_bin()
			qty_to_undeliver -= undelivered


def _dn_item_batch_qty_map(item):
	batch_qty = {}
	if not item.serial_and_batch_bundle:
		return batch_qty
	try:
		sbb = frappe.get_doc("Serial and Batch Bundle", item.serial_and_batch_bundle)
	except Exception:
		return batch_qty
	for entry in sbb.entries or []:
		if entry.batch_no:
			batch_qty[entry.batch_no] = batch_qty.get(entry.batch_no, 0) + abs(flt(entry.qty))
	return batch_qty


def _undeliver_sre_qty(sre, qty, batch_qty=None):
	"""Reduce sb_entry delivered_qty then return how much header delivered can fall."""
	if qty <= 0:
		return 0

	if sre.reservation_based_on != "Serial and Batch" or not sre.get("sb_entries"):
		return qty

	remaining = qty
	batch_qty = dict(batch_qty or {})

	def _undo_from_entries(prefer_batches):
		nonlocal remaining
		for entry in sre.sb_entries:
			if remaining <= 0:
				break
			if prefer_batches and entry.batch_no not in batch_qty:
				continue
			limit = batch_qty.get(entry.batch_no, remaining) if prefer_batches else remaining
			undo = min(flt(entry.delivered_qty), remaining, limit)
			if undo <= 0:
				continue
			entry.db_set("delivered_qty", flt(entry.delivered_qty) - undo, update_modified=False)
			remaining -= undo
			if prefer_batches and entry.batch_no in batch_qty:
				batch_qty[entry.batch_no] = max(batch_qty[entry.batch_no] - undo, 0)

	if batch_qty:
		_undo_from_entries(prefer_batches=True)
	if remaining > 0:
		_undo_from_entries(prefer_batches=False)

	return qty - remaining


def update_sales_order_quantities_on_cancel(doc):
	"""Re-sync SO delivered_qty / per_delivered after DN cancel cleanup."""
	so_item_rows = [
		row.so_detail for row in doc.items if row.against_sales_order and row.so_detail
	]
	if not so_item_rows:
		return

	doc.update_prevdoc_status()

	so_names = {row.against_sales_order for row in doc.items if row.against_sales_order}
	for so_name in so_names:
		affected = [
			row.so_detail
			for row in doc.items
			if row.against_sales_order == so_name and row.so_detail
		]
		so = frappe.get_doc("Sales Order", so_name)
		so.update_reserved_qty(affected)


def _distribute_delivered_across_snapshots(snapshots, exclude_dn):
	"""Assign delivered_qty per restored SRE without double-counting SO deliveries."""
	from collections import defaultdict

	prepared = [dict(s) for s in snapshots]
	by_detail = defaultdict(list)
	for snap in prepared:
		by_detail[snap.get("voucher_detail_no")].append(snap)

	for so_detail, group in by_detail.items():
		if not so_detail:
			continue
		other_dn_delivered = _delivered_qty_excluding_dn(so_detail, exclude_dn)
		own_total = sum(flt(s.get("delivered_qty")) for s in group)
		extra = max(other_dn_delivered - own_total, 0)

		for snap in group:
			own = flt(snap.get("delivered_qty"))
			reserved = flt(snap.get("reserved_qty"))
			snap["delivered_qty"] = min(own, reserved)
			for entry in snap.get("sb_entries") or []:
				entry["delivered_qty"] = min(
					flt(entry.get("delivered_qty")),
					flt(entry.get("qty")),
				)

		if extra <= 0:
			continue

		for snap in group:
			if extra <= 0:
				break
			reserved = flt(snap.get("reserved_qty"))
			room = max(reserved - flt(snap.get("delivered_qty")), 0)
			if room <= 0:
				continue
			add = min(room, extra)
			snap["delivered_qty"] = flt(snap.get("delivered_qty")) + add
			extra -= add
			remaining_add = add
			for entry in snap.get("sb_entries") or []:
				if remaining_add <= 0:
					break
				entry_room = max(flt(entry.get("qty")) - flt(entry.get("delivered_qty")), 0)
				if entry_room <= 0:
					continue
				entry_add = min(entry_room, remaining_add)
				entry["delivered_qty"] = flt(entry.get("delivered_qty")) + entry_add
				remaining_add -= entry_add

	return prepared


def restore_stock_reservations_after_cancel(doc):
	"""Restore Stock Reservation Entries from the submit-time snapshot."""
	if not frappe.db.get_single_value("Stock Settings", "enable_stock_reservation"):
		return

	snapshots, comment_name = _load_cancelled_sre_snapshot(doc.name)
	if not snapshots:
		return

	prepared = _distribute_delivered_across_snapshots(snapshots, doc.name)

	errors = []
	for snapshot in prepared:
		try:
			if _snapshot_sre_still_active(snapshot):
				continue
			_recreate_stock_reservation_from_snapshot(snapshot)
		except Exception:
			frappe.log_error(
				title="DN Cancel - SRE Restore Error",
				message=f"{doc.name}\n{frappe.as_json(snapshot)}\n{frappe.get_traceback()}",
			)
			errors.append(
				f"{snapshot.get('item_code') or ''} / "
				f"{snapshot.get('voucher_no') or ''} / "
				f"reserved {flt(snapshot.get('reserved_qty'))}"
			)

	if comment_name:
		frappe.delete_doc("Comment", comment_name, ignore_permissions=True, force=True)

	if errors:
		frappe.throw(
			_(
				"Failed to restore stock reservation(s) while cancelling Delivery Note {0}:<br>{1}"
			).format(frappe.bold(doc.name), "<br>".join(frappe.bold(e) for e in errors)),
			title=_("Stock Reservation Restore Failed"),
		)


def _snapshot_sre_still_active(snapshot):
	"""True when the snapshotted SRE was never cancelled and is still submitted."""
	sre_name = snapshot.get("name")
	if not sre_name or not frappe.db.exists("Stock Reservation Entry", sre_name):
		return False
	return cint(frappe.db.get_value("Stock Reservation Entry", sre_name, "docstatus")) == 1


def _delivered_qty_excluding_dn(so_detail, exclude_dn):
	"""Stock qty still delivered against SO item from other submitted DNs."""
	if not so_detail:
		return 0
	return flt(
		frappe.db.sql(
			"""
			select coalesce(sum(dni.stock_qty), 0)
			from `tabDelivery Note Item` dni
			join `tabDelivery Note` dn on dn.name = dni.parent
			where dni.so_detail = %s
			  and dn.docstatus = 1
			  and ifnull(dn.is_return, 0) = 0
			  and dn.name != %s
			""",
			(so_detail, exclude_dn or ""),
		)[0][0]
	)


def _get_active_reserved_qty(sales_order, so_detail, warehouse=None):
	"""Net reserved qty still available to deliver (reserved - delivered)."""
	filters = {
		"voucher_type": "Sales Order",
		"voucher_no": sales_order,
		"voucher_detail_no": so_detail,
		"docstatus": 1,
		"status": ["in", ["Reserved", "Partially Reserved", "Partially Delivered"]],
	}
	if warehouse:
		filters["warehouse"] = warehouse

	rows = frappe.get_all(
		"Stock Reservation Entry",
		filters=filters,
		fields=["reserved_qty", "delivered_qty"],
	)
	return sum(flt(r.reserved_qty) - flt(r.delivered_qty) for r in rows)


def _get_active_reserved_stock_qty(sales_order, so_detail, warehouse=None):
	"""Gross reserved qty on active SREs (do not subtract delivered)."""
	filters = {
		"voucher_type": "Sales Order",
		"voucher_no": sales_order,
		"voucher_detail_no": so_detail,
		"docstatus": 1,
		"status": ["in", ["Reserved", "Partially Reserved", "Partially Delivered", "Delivered"]],
	}
	if warehouse:
		filters["warehouse"] = warehouse

	rows = frappe.get_all(
		"Stock Reservation Entry",
		filters=filters,
		fields=["reserved_qty"],
	)
	return sum(flt(r.reserved_qty) for r in rows)


def _recreate_stock_reservation_from_snapshot(snapshot):
	reserved_qty = flt(snapshot.get("reserved_qty"))
	if reserved_qty <= 0:
		return

	sales_order = snapshot.get("voucher_no")
	so_detail = snapshot.get("voucher_detail_no")
	warehouse = snapshot.get("warehouse")
	delivered_qty = flt(snapshot.get("delivered_qty"))
	voucher_qty = flt(snapshot.get("voucher_qty") or reserved_qty)

	if so_detail and frappe.db.exists("Sales Order Item", so_detail):
		soi = frappe.db.get_value(
			"Sales Order Item",
			so_detail,
			["qty", "stock_qty", "conversion_factor"],
			as_dict=True,
		)
		if soi:
			voucher_qty = flt(soi.stock_qty) or (
				flt(soi.qty) * flt(soi.conversion_factor or 1)
			)

	active_qty = _get_active_reserved_stock_qty(sales_order, so_detail, warehouse)
	remaining_capacity = max(voucher_qty - active_qty, 0)
	if remaining_capacity <= 0:
		return

	if reserved_qty > remaining_capacity:
		reserved_qty = remaining_capacity

	delivered_qty = min(delivered_qty, reserved_qty)

	sre = frappe.new_doc("Stock Reservation Entry")
	sre.item_code = snapshot.get("item_code")
	sre.warehouse = warehouse
	sre.company = snapshot.get("company")
	sre.stock_uom = snapshot.get("stock_uom")
	sre.voucher_type = snapshot.get("voucher_type") or "Sales Order"
	sre.voucher_no = sales_order
	sre.voucher_detail_no = so_detail
	sre.voucher_qty = voucher_qty
	sre.reserved_qty = reserved_qty
	sre.available_qty = flt(snapshot.get("available_qty") or reserved_qty)
	sre.available_qty_to_reserve = reserved_qty
	from_voucher_type = snapshot.get("from_voucher_type")
	from_voucher_no = snapshot.get("from_voucher_no")
	from_voucher_detail_no = snapshot.get("from_voucher_detail_no")
	if from_voucher_type and from_voucher_no:
		if cint(frappe.db.get_value(from_voucher_type, from_voucher_no, "docstatus")) == 2:
			from_voucher_type = from_voucher_no = from_voucher_detail_no = None
	sre.from_voucher_type = from_voucher_type
	sre.from_voucher_no = from_voucher_no
	sre.from_voucher_detail_no = from_voucher_detail_no

	reservation_based_on = snapshot.get("reservation_based_on") or "Qty"
	sb_entries = snapshot.get("sb_entries") or []

	if reservation_based_on == "Serial and Batch" and sb_entries:
		sre.has_batch_no = 1
		sre.has_serial_no = cint(snapshot.get("has_serial_no"))
		sre.reservation_based_on = "Serial and Batch"
		sre.use_serial_batch_fields = 1

		total_sb_qty = sum(
			flt(e.get("qty")) for e in sb_entries if e.get("batch_no") or e.get("serial_no")
		)
		scale = (
			(reserved_qty / total_sb_qty)
			if total_sb_qty > 0 and abs(total_sb_qty - reserved_qty) > 0.0001
			else 1.0
		)

		for entry in sb_entries:
			if not entry.get("batch_no") and not entry.get("serial_no"):
				continue
			entry_qty = flt(entry.get("qty")) * scale
			if entry_qty <= 0:
				continue
			entry_delivered = min(flt(entry.get("delivered_qty")) * scale, entry_qty)
			sre.append(
				"sb_entries",
				{
					"batch_no": entry.get("batch_no"),
					"serial_no": entry.get("serial_no"),
					"qty": entry_qty,
					"delivered_qty": entry_delivered,
					"warehouse": entry.get("warehouse") or warehouse,
					"pieces": flt(entry.get("pieces")),
					"length": flt(entry.get("length")),
					"section_weight": flt(entry.get("section_weight")),
				},
			)

		if not sre.sb_entries:
			return

		reserved_qty = sum(flt(e.qty) for e in sre.sb_entries)
		sre.reserved_qty = reserved_qty
		sre.available_qty_to_reserve = reserved_qty
		delivered_qty = min(delivered_qty, reserved_qty)

		sre.auto_reserve_serial_and_batch = lambda *args, **kwargs: None
	else:
		sre.reservation_based_on = "Qty"
		sre.has_batch_no = 0
		sre.has_serial_no = 0

	sre.flags.ignore_permissions = True
	sre.insert()
	sre.submit()

	if delivered_qty > 0:
		sre.db_set("delivered_qty", delivered_qty, update_modified=False)
		if sre.reservation_based_on == "Serial and Batch":
			for entry in sre.sb_entries:
				if flt(entry.delivered_qty) > 0:
					frappe.db.set_value(
						"Serial and Batch Entry",
						entry.name,
						"delivered_qty",
						flt(entry.delivered_qty),
						update_modified=False,
					)
		sre.reload()
		sre.update_status()
		sre.update_reserved_qty_in_voucher()
		sre.update_reserved_stock_in_bin()


def cancel_stock_reconciliations_for_delivery_note(delivery_note, delete=False):
    """Cancel Stock Reconciliations created for a DN."""
    sr_names = frappe.get_all(
        "Stock Reconciliation Item",
        filters={"delivery_note_ref": delivery_note},
        pluck="parent",
    )

    errors = []
    for sr_name in set(sr_names):
        try:
            sr = frappe.get_doc("Stock Reconciliation", sr_name)
            if sr.docstatus == 1:
                sr.flags.ignore_permissions = True
                sr.cancel()

            if delete and frappe.db.exists("Stock Reconciliation", sr_name):
                if cint(frappe.db.get_value("Stock Reconciliation", sr_name, "docstatus")) == 2:
                    frappe.delete_doc(
                        "Stock Reconciliation",
                        sr_name,
                        ignore_permissions=True,
                        force=True,
                    )
        except Exception:
            frappe.log_error(
                title="DN Cancel - Stock Reconciliation Release Error",
                message=f"{sr_name}\n{frappe.get_traceback()}",
            )
            errors.append(sr_name)

    if errors and not delete:
        frappe.throw(
            _(
                "Failed to cancel Stock Reconciliation(s) linked to Delivery Note {0}:<br>{1}"
            ).format(frappe.bold(delivery_note), "<br>".join(frappe.bold(e) for e in errors)),
            title=_("Stock Reconciliation Cancel Failed"),
        )


@frappe.whitelist()
def create_sr_from_dn(delivery_note):
    doc = frappe.get_doc("Delivery Note", delivery_note)
    create_stock_reconciliation(doc)
    return "done"


def create_stock_reconciliation(self):
    import frappe
    from frappe.utils import flt, nowtime, get_datetime, add_to_date

    items_with_invoice_qty = [row for row in self.items if flt(row.difference_qty) > 0 and row.custom_deliver_as_qty]
    if not items_with_invoice_qty:
        return

    cancel_stock_reconciliations_for_delivery_note(self.name, delete=False)

    sr = frappe.new_doc("Stock Reconciliation")
    sr.purpose = "Stock Reconciliation"
    if sr.meta.has_field("cost_center") and self.meta.has_field("cost_center"):
        sr.cost_center = self.get("cost_center")
    if sr.meta.has_field("branch") and self.meta.has_field("branch"):
        sr.branch = self.get("branch")

    db_posting = frappe.db.get_value(
        "Delivery Note", self.name, ["posting_date", "posting_time"], as_dict=True
    )

    dn_posting_date = db_posting.posting_date if db_posting else self.posting_date
    dn_posting_time = (
        db_posting.posting_time if db_posting else (self.posting_time or nowtime())
    )

    dt = get_datetime(f"{dn_posting_date} {dn_posting_time}")
    before_dt = add_to_date(dt, seconds=-10)

    sr.set_posting_time = 1
    sr.posting_date = before_dt.date()
    sr.posting_time = before_dt.strftime('%H:%M:%S')
    sr.company = self.company

    if self.set_warehouse:
        sr.set_warehouse = self.set_warehouse

    for row in items_with_invoice_qty:
        total_qty = flt(row.qty) + flt(row.difference_qty)

        valuation_rate = 0
        if total_qty and flt(row.amount):
            valuation_rate = flt(row.amount) / total_qty
        if not valuation_rate:
            valuation_rate = (
                flt(row.incoming_rate)
                or flt(row.rate)
                or flt(frappe.get_cached_value("Item", row.item_code, "valuation_rate"))
                or 1
            )

        if row.serial_and_batch_bundle:
            sbb = frappe.get_doc("Serial and Batch Bundle", row.serial_and_batch_bundle)

            batch_entries = [e for e in sbb.entries if e.batch_no]
            batch_count = len(batch_entries)

            if batch_count > 0:
                total_bundle_qty = sum(abs(flt(e.qty)) for e in batch_entries)

                for entry in batch_entries:
                    batch_qty = abs(flt(entry.qty))

                    ratio = (
                        batch_qty / total_bundle_qty
                        if total_bundle_qty
                        else 1.0 / batch_count
                    )

                    entry_invoice_qty = flt(row.invoice_qty) * ratio
                    entry_diff_qty = entry_invoice_qty - batch_qty
                    entry_dn_qty = batch_qty

                    sr.append(
                        "items",
                        {
                            "item_code": row.item_code,
                            "warehouse": row.warehouse or self.set_warehouse,
                            "use_serial_batch_fields": 1,
                            "batch_no": entry.batch_no,
                            "qty": entry_invoice_qty,
                            "difference_qty": entry_diff_qty,
                            "reconcile_all_serial_batch": 0,
                            "delivery_note_qty": entry_dn_qty,
                            "valuation_rate": valuation_rate,
                            "current_rate": flt(row.incoming_rate),
                            "pieces": (
                                flt(row.get("pieces")) * ratio
                                if row.get("pieces")
                                else 0
                            ),
                            "length": flt(row.get("length")),
                            "average_length": flt(row.get("average_length")),
                            "section_weight": flt(row.get("section_weight")),
                            "delivery_note_ref": self.name,
                            "serial_and_batch_bundle": None,
                        },
                    )
                continue

        sr.append(
            "items",
            {
                "item_code": row.item_code,
                "warehouse": row.warehouse or self.set_warehouse,
                "batch_no": row.batch_no or None,
                "use_serial_batch_fields": 1 if row.batch_no else 0,
                "qty": row.invoice_qty,
                "difference_qty": flt(row.difference_qty),
                "reconcile_all_serial_batch": 0,
                "delivery_note_qty": flt(row.qty),
                "amount": flt(row.amount),
                "valuation_rate": valuation_rate,
                "current_rate": flt(row.incoming_rate),
                "pieces": flt(row.get("pieces")),
                "length": flt(row.get("length")),
                "average_length": flt(row.get("average_length")),
                "section_weight": flt(row.get("section_weight")),
                "delivery_note_ref": self.name,
                "serial_and_batch_bundle": None,
            },
        )

    sr.flags.ignore_permissions = True
    sr.insert(ignore_permissions=True)

    for sr_item in sr.items:
        if (
            flt(sr_item.qty)
            and flt(sr_item.current_qty)
            and flt(sr_item.current_valuation_rate)
        ):
            new_rate = flt(
                flt(sr_item.current_qty)
                * flt(sr_item.current_valuation_rate)
                / flt(sr_item.qty)
            )
            if new_rate:
                sr_item.valuation_rate = new_rate
                sr_item.amount = flt(sr_item.qty) * flt(sr_item.valuation_rate)
        if flt(sr_item.qty) and not flt(sr_item.valuation_rate):
            sr_item.valuation_rate = (
                flt(sr_item.current_valuation_rate)
                or flt(sr_item.current_rate)
                or flt(frappe.db.get_value("Item", sr_item.item_code, "valuation_rate"))
                or flt(
                    frappe.db.get_value(
                        "Bin",
                        {"item_code": sr_item.item_code, "warehouse": sr_item.warehouse},
                        "valuation_rate",
                    )
                )
                or 1
            )
            sr_item.amount = flt(sr_item.qty) * flt(sr_item.valuation_rate)

    sr.save(ignore_permissions=True)

    for sr_item in sr.items:
        if flt(sr_item.qty) and not flt(sr_item.valuation_rate):
            sr_item.valuation_rate = (
                flt(frappe.db.get_value("Item", sr_item.item_code, "valuation_rate")) or 1
            )
            sr_item.amount = flt(sr_item.qty) * flt(sr_item.valuation_rate)
            sr_item.db_set("valuation_rate", sr_item.valuation_rate, update_modified=False)
            sr_item.db_set("amount", sr_item.amount, update_modified=False)

    sr.submit()

    # Snapshot each affected batch's pieces BEFORE applying this SR's
    # updates, so pieces can be correctly decremented by what was taken
    # rather than overwritten with the taken amount as if it were the
    # batch's new total.
    batch_nos_in_sr = list({sr_item.batch_no for sr_item in sr.items if sr_item.batch_no})
    original_batch_pieces = {}
    if batch_nos_in_sr and frappe.db.has_column("Batch", "pieces"):
        for b in frappe.db.get_all(
            "Batch", filters={"name": ["in", batch_nos_in_sr]}, fields=["name", "pieces"]
        ):
            original_batch_pieces[b.name] = flt(b.pieces)

    for sr_item in sr.items:
        if not sr_item.batch_no:
            continue
        batch_update = {}
        if frappe.db.has_column("Batch", "batch_qty"):
            batch_update["batch_qty"] = flt(sr_item.qty)
        if frappe.db.has_column("Batch", "pieces") and sr_item.get("pieces"):
            # Pieces taken must be subtracted from what the batch actually
            # held, not overwritten with the taken amount — e.g. a batch
            # starting at 35 pieces, with 20 taken via this invoice,
            # should end at 15, not be overwritten to show 20 as if that
            # were the batch's entire remaining stock.
            #
            # NOTE: this direct write is only the final value for items
            # where required_stock_in_pieces is OFF (the generic Piece
            # Stock Ledger mechanism never fires for those, so this is
            # the sole correction). For items where it IS on, the DN's
            # own natural delivery (processed right after this function
            # returns, still within the same before_submit flow) will
            # independently call recalculate_batch_pieces() and overwrite
            # this value based on the full ledger sum — do NOT also
            # insert a Piece Stock Ledger Entry here, or the same pieces
            # get subtracted twice (once here, once by the DN's own
            # delivery), producing a negative/wrong final value.
            starting_pieces = original_batch_pieces.get(sr_item.batch_no, 0)
            batch_update["pieces"] = max(starting_pieces - flt(sr_item.pieces), 0)
        # Client requirement: Length must also reflect the reconciled
        # value. Only write it when the SR item actually carries a
        # positive length — never overwrite existing Batch length with
        # a blank/zero from an SR item that had no length data (e.g.
        # a plain qty-only reconciliation for a non-steel item).
        length_val = sr_item.get("length") or sr_item.get("average_length")
        if frappe.db.has_column("Batch", "average_length") and flt(length_val):
            batch_update["average_length"] = flt(length_val)
        if batch_update:
            frappe.db.set_value(
                "Batch", sr_item.batch_no, batch_update, update_modified=False
            )

    for dn_row in self.items:
        if flt(dn_row.difference_qty) <= 0:
            continue

        if dn_row.serial_and_batch_bundle:
            update_bundle_to_invoice_qty(
                dn_row,
                flt(dn_row.invoice_qty),
                flt(dn_row.qty),
                flt(dn_row.custom_deliver_as_qty),
            )
        elif dn_row.batch_no and hasattr(dn_row, "use_serial_batch_fields"):
            dn_row.use_serial_batch_fields = 1

        dn_row.qty = flt(dn_row.invoice_qty)
        dn_row.stock_qty = flt(dn_row.invoice_qty) * flt(dn_row.conversion_factor or 1)

    for row in self.items:
        row.amount = 0
        row.base_amount = 0
        row.net_amount = 0
        row.base_net_amount = 0

    self.set_missing_values()

    if hasattr(self, "apply_pricing_rule"):
        self.apply_pricing_rule()

    for row in self.items:
        row.amount = flt(row.qty) * flt(row.rate)
        row.base_amount = flt(row.amount) * flt(self.conversion_rate or 1)

    self.calculate_taxes_and_totals()

    self.run_method("validate")

    if hasattr(self, "validate_stock"):
        self.validate_stock()

    if hasattr(self, "validate_with_previous_doc"):
        self.validate_with_previous_doc()

    if hasattr(self, "recalculate_rate_and_amount"):
        self.recalculate_rate_and_amount()

    frappe.msgprint(
        f"Stock Reconciliation <b>{sr.name}</b> created and submitted for Delivery Note <b>{self.name}</b>.",
        alert=True,
    )

import frappe
import json
from frappe.utils import flt
from erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry import (
    get_sre_details_for_voucher,
)
from erpnext.stock.doctype.packed_item.packed_item import make_packing_list
from frappe.model.mapper import get_mapped_doc


@frappe.whitelist()
def make_delivery_note_custom(source_name, target_doc=None, kwargs=None):

    if not kwargs:
        kwargs = frappe.flags.args or {}

    if isinstance(kwargs, str):
        try:
            kwargs = json.loads(kwargs)
        except Exception:
            kwargs = {}

    if isinstance(kwargs, list):
        temp = {}
        for k in kwargs:
            if isinstance(k, dict):
                temp.update(k)
        kwargs = temp

    if not isinstance(kwargs, dict):
        kwargs = {}

    kwargs = frappe._dict(kwargs)

    selected_sre = kwargs.get("selected_sre", [])
    for_reserved_stock = kwargs.get("for_reserved_stock")

    so = frappe.get_doc("Sales Order", source_name)

    def set_missing_values(source, target):
        target.run_method("set_missing_values")
        target.run_method("calculate_taxes_and_totals")
        target.run_method("set_use_serial_batch_fields")
        make_packing_list(target)

    def update_item(source, target, source_parent):
        target.qty = flt(source.qty) - flt(source.delivered_qty)
        target.amount = target.qty * flt(source.rate)
        target.base_amount = target.qty * flt(source.base_rate)
        target.deliver_as_qty = source_parent.deliver_as_qty

    target_doc = get_mapped_doc(
        "Sales Order",
        so.name,
        {
            "Sales Order": {
                "doctype": "Delivery Note",
                "validation": {"docstatus": ["=", 1]},
            },
            "Sales Order Item": {
                "doctype": "Delivery Note Item",
                "field_map": {
                    "name": "so_detail",
                    "parent": "against_sales_order",
                    "rate": "rate",
                },
                "postprocess": update_item,
            },
        },
        target_doc,
    )

    target_doc.items = []

    if for_reserved_stock:
        sre_list = get_sre_details_for_voucher("Sales Order", source_name)

        so_items = {d.name: d for d in so.items}

        for sre in sre_list:

            if selected_sre and sre.voucher_detail_no not in selected_sre:
                continue

            so_item = so_items.get(sre.voucher_detail_no)
            if not so_item:
                continue

            dn_item = get_mapped_doc(
                "Sales Order Item",
                so_item.name,
                {
                    "Sales Order Item": {
                        "doctype": "Delivery Note Item",
                        "field_map": {
                            "name": "so_detail",
                            "parent": "against_sales_order",
                            "rate": "rate",
                        },
                    }
                },
                ignore_permissions=True,
            )

            dn_item.qty = flt(sre.reserved_qty) / flt(dn_item.conversion_factor or 1)
            dn_item.warehouse = sre.warehouse
            dn_item.custom_deliver_as_qty = so.deliver_as_qty
            _apply_reserved_dims_to_dn_item(dn_item, so_item, sre)
            if sre.reservation_based_on == "Serial and Batch":
                dn_item.serial_and_batch_bundle = get_ssb_bundle_for_voucher_from_sre(sre)

            if frappe.get_meta("Delivery Note Item").has_field("custom_sre"):
                dn_item.custom_sre = sre.name

            target_doc.append("items", dn_item)

    set_missing_values(so, target_doc)

    return target_doc

@frappe.whitelist()
def make_delivery_note_from_si(source_name, target_doc=None):
    si = frappe.get_doc("Sales Invoice", source_name)

    def set_missing_values(source, target):
        target.run_method("set_missing_values")
        target.run_method("calculate_taxes_and_totals")
        target.run_method("set_use_serial_batch_fields")
        make_packing_list(target)

    def update_item(source, target, source_parent):
        target.qty = flt(source.qty)
        target.invoice_qty = flt(source.qty)
        target.amount = target.qty * flt(source.rate)
        target.custom_deliver_as_qty = cint(source_parent.deliver_as_qty)
        # Deliberately leave batch_no / warehouse blank here.
        # populate_missing_batch_bundle_from_si (existing logic) fills
        # these in on save once si_detail/against_sales_invoice are set.
        target.batch_no = None
        target.warehouse = None

    target_doc = get_mapped_doc(
        "Sales Invoice",
        si.name,
        {
            "Sales Invoice": {
                "doctype": "Delivery Note",
                "validation": {"docstatus": ["=", 1]},
            },
            "Sales Invoice Item": {
                "doctype": "Delivery Note Item",
                "field_map": {
                    "name": "si_detail",
                    "parent": "against_sales_invoice",
                    "rate": "rate",
                },
                "postprocess": update_item,
            },
        },
        target_doc,
        set_missing_values,
    )

    return target_doc
    
import frappe
import json
from frappe.utils import flt, cstr


@frappe.whitelist()
def get_sales_order_items_for_selector(filters=None):

    if isinstance(filters, str):
        filters = json.loads(filters)

    filters = filters or {}

    so_filters = [["docstatus", "=", 1]]

    for key, val in filters.items():
        if key in ("dynamic_filters", "project", "po_no"):
            continue

        if val:
            if isinstance(val, list) and len(val) == 2:
                so_filters.append([key, val[0], val[1]])
            else:
                so_filters.append([key, "=", val])

    if filters.get("project"):
        so_filters.append(["project", "=", filters.get("project")])

    if filters.get("po_no"):
        so_filters.append(["po_no", "like", f"%{filters['po_no']}%"])

    dynamic_filters = filters.get("dynamic_filters")

    if dynamic_filters:
        if isinstance(dynamic_filters, str):
            dynamic_filters = json.loads(dynamic_filters)

        for df in dynamic_filters:
            if len(df) >= 4:
                fieldname = df[1]
                operator = df[2]
                value = df[3]

                if operator == "Between" and isinstance(value, str) and " to " in value:
                    value = value.split(" to ")

                so_filters.append([fieldname, operator, value])

    sales_orders = frappe.get_all(
        "Sales Order",
        fields=["name", "customer", "transaction_date", "currency", "company", "po_no"],
        filters=so_filters,
        order_by="transaction_date desc",
    )

    if not sales_orders:
        return []

    so_names = [d.name for d in sales_orders]
    so_map = {d.name: d for d in sales_orders}

    optional_soi_cols = []
    for col in ("pieces", "length_size", "assorted_length", "description"):
        if frappe.db.has_column("Sales Order Item", col):
            optional_soi_cols.append(f"soi.{col}")
        else:
            optional_soi_cols.append(f"NULL AS {col}")

    section_weight_sel = (
        "item.weight_per_meter AS section_weight"
        if frappe.db.has_column("Item", "weight_per_meter")
        else "0 AS section_weight"
    )

    items = frappe.db.sql(
        f"""
        SELECT
            soi.name,
            soi.parent,
            soi.item_code,
            soi.item_name,
            soi.qty,
            soi.delivered_qty,
            soi.rate,
            soi.amount,
            soi.uom,
            {", ".join(optional_soi_cols)},
            {section_weight_sel}
        FROM `tabSales Order Item` soi
        LEFT JOIN `tabItem` item
            ON item.name = soi.item_code
        WHERE soi.parent IN %(so_names)s
        ORDER BY soi.parent ASC, soi.idx ASC
        """,
        {"so_names": so_names},
        as_dict=True,
    )

    reservation_rows = frappe.db.sql(
        """
        SELECT
            sre.voucher_detail_no,
            SUM(sre.reserved_qty - sre.delivered_qty) AS reserved_qty
        FROM `tabStock Reservation Entry` sre
        WHERE sre.docstatus = 1
        AND sre.voucher_type = 'Sales Order'
        AND sre.status IN ('Reserved', 'Partially Reserved','Partially Delivered')
        AND (%(company)s IS NULL OR sre.company = %(company)s)
        GROUP BY sre.voucher_detail_no
        """,
        {"company": filters.get("company")},
        as_dict=True,
    )

    reservation_map = {
        r.voucher_detail_no: flt(r.reserved_qty) for r in reservation_rows
    }

    rows = []

    for row in items:

        pending_qty = flt(row.qty) - flt(row.delivered_qty)

        if pending_qty <= 0:
            continue

        so = so_map.get(row.parent) or {}

        reserved_qty = flt(reservation_map.get(row.name, 0))
        if reserved_qty <= 0:
            continue

        rows.append(
            {
                "name": row.name,
                "parent": row.parent,
                "customer": so.get("customer"),
                "transaction_date": so.get("transaction_date"),
                "company": so.get("company"),
                "item_code": row.item_code,
                "item_name": row.item_name,
                "qty": flt(row.qty),
                "pending_qty": pending_qty,
                "reserved_qty": reserved_qty,
                "uom": row.uom,
                "pieces": row.pieces,
                "length": row.length_size,
                "section_weight": flt(row.section_weight),
                "po_no": so.get("po_no"),
                "assorted_length": row.assorted_length,
                "description": row.description,
            }
        )

    return rows