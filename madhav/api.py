import frappe
import json
from frappe.model.mapper import get_mapped_doc
from erpnext.stock.get_item_details import get_bin_details, get_default_bom, get_price_list_rate
from erpnext.stock.doctype.packed_item.packed_item import is_product_bundle
from frappe.utils import (
	flt,
	get_link_to_form,
	getdate,
)
from frappe import _, cint
from erpnext.controllers.accounts_controller import set_order_defaults,validate_and_delete_children
from frappe.model.workflow import get_workflow_name, is_transition_condition_satisfied
from erpnext.stock.get_item_details import get_conversion_factor
from erpnext.buying.utils import update_last_purchase_rate
from erpnext.stock.doctype.packed_item.packed_item import make_packing_list


@frappe.whitelist()
def get_so_item_pieces_and_length(so_detail: str):
    """Return pieces, length_size and qty from Sales Order Item.

    This is used by the Delivery Note client script to safely fetch
    values without calling frappe.client.get_value from JS.
    """
    if not so_detail:
        return {}

    pieces, length_size, qty = frappe.db.get_value(
        "Sales Order Item",
        so_detail,
        ["pieces", "length_size", "qty"],
    ) or (None, None, None)

    return {
        "pieces": pieces,
        "length_size": length_size,
        "qty": qty,
    }

import frappe

import frappe
from frappe.utils import get_datetime, get_datetime_str
from datetime import datetime, timedelta

@frappe.whitelist()
def get_employee_checkin_entries(employee, attendance_date):
    # Convert string to datetime and define start and end of the day
    start_date = get_datetime(attendance_date)
    end_date = start_date + timedelta(days=1)

    # Fetch first check-in (earliest)
    in_time_doc = frappe.get_all(
        "Employee Checkin",
        filters={
            "employee": employee,
            "time": ["between", [start_date, end_date]]
        },
        fields=["time"],
        order_by="time asc",
        limit_page_length=1
    )

    # Fetch last check-in (latest)
    out_time_doc = frappe.get_all(
        "Employee Checkin",
        filters={
            "employee": employee,
            "time": ["between", [start_date, end_date]]
        },
        fields=["time"],
        order_by="time desc",
        limit_page_length=1
    )

    return {
        "in_time": in_time_doc[0].time if in_time_doc else None,
        "out_time": out_time_doc[0].time if out_time_doc else None
    }


@frappe.whitelist()
def create_stock_reservation_entries(source_name, items_details):
    import json
    from madhav.madhav.monkey_patch.stock_reservation_entry import (
        create_stock_reservation_entries_for_so_items,
    )
    if isinstance(items_details, str):
        items_details = json.loads(items_details)
    sales_order = frappe.get_doc("Sales Order", source_name)

    result = create_stock_reservation_entries_for_so_items(
        sales_order=sales_order,
        items_details=items_details,
        notify=True,
    )

    # Reflect what actually happened instead of always claiming success —
    # the inner function returns early with a "no_eligible_items" status
    # when every row got filtered out (e.g. length-window constraints),
    # and nothing gets created in that case.
    if isinstance(result, dict) and result.get("status") == "no_eligible_items":
        return {
            "status": "warning",
            "message": _(
                "No stock was reserved — no eligible batches found for the given constraints."
            ),
        }

    return {
        "status": "success",
        "message": "Stock Reservation Created Successfully",
    }

    
@frappe.whitelist()
def get_offday_status(employee, attendance_date,attendance):
    
    from datetime import datetime
    
    if isinstance(attendance_date, str):
        date_obj = datetime.strptime(attendance_date, "%Y-%m-%d").date()
    else:
        date_obj = attendance_date
    
    # Step 1: Check Holiday List
    holiday_list = frappe.db.get_value("Employee", employee, "holiday_list")
    if holiday_list:
        if frappe.db.exists("Holiday", {"holiday_date": date_obj, "parent": holiday_list}):
            holiday_doc = frappe.get_doc("Holiday List",holiday_list)

            for holiday in holiday_doc.holidays:
                if attendance:
                    if holiday.weekly_off:
                        frappe.db.set_value("Attendance", attendance, {
                        "status": "Weekly Off",
                        "leave_type": None
                        })
                        frappe.db.commit()
                        return "Weekly Off"
                    else:
                        frappe.db.set_value("Attendance", attendance, {
                            "status": "Holiday",
                            "leave_type": None
                            })
                        frappe.db.commit()
                        return "Holiday"            
    
    # Step 2: Check Shift Assignment for weekly off

    shift_assignment = frappe.get_all(
    "Shift Assignment",
    filters={
        "employee": employee,
        "start_date": ["<=", date_obj],
    },
    fields=["name", "shift_type", "off_day", "end_date"]
    )

    valid_shift_assignments = []

    for shift in shift_assignment:
        if not shift["end_date"] or shift["end_date"] >= date_obj:
            valid_shift_assignments.append(shift)

    if valid_shift_assignments:
        weekday = date_obj.strftime('%A')
        emp_offday = valid_shift_assignments[0]["off_day"]

        if weekday == emp_offday:
            if attendance:
                frappe.db.set_value("Attendance", attendance, {
                    "status": "Weekly Off",
                    "leave_type": None
                })
                frappe.db.commit()
            return "Weekly Off"

def custom_make_variant_item_code(template_item_code, template_item_name, variant):
    
    from frappe.utils import cstr
    import re
    
    """Uses template's item code and abbreviations to make variant's item code"""
    if variant.item_code:
        return
 
    abbreviations = []
    for attr in variant.attributes:
        item_attribute = frappe.db.sql(
            """select i.numeric_values, v.abbr
            from `tabItem Attribute` i left join `tabItem Attribute Value` v
                on (i.name=v.parent)
            where i.name=%(attribute)s and (v.attribute_value=%(attribute_value)s or i.numeric_values = 1)""",
            {"attribute": attr.attribute, "attribute_value": attr.attribute_value},
            as_dict=True,
        )
 
        if not item_attribute:
            continue
            # frappe.throw(_('Invalid attribute {0} {1}').format(frappe.bold(attr.attribute),
            #   frappe.bold(attr.attribute_value)), title=_('Invalid Attribute'),
            #   exc=InvalidItemAttributeValueError)
 
        abbr_or_value = (
            cstr(attr.attribute_value) if item_attribute[0].numeric_values else item_attribute[0].abbr
        )
        abbreviations.append(abbr_or_value)
 
    if abbreviations:
        # variant.item_code = "{}-{}".format(template_item_code, "-".join(abbreviations))
        variant.item_name = "{} {}".format(template_item_name, " ".join(abbreviations))
    
    # Use the same series used by standard items
    # item_series = frappe.get_meta("Item").get_field("naming_series").options.split("\n")[0]

    # from frappe.model.naming import make_autoname
    # variant.item_code = make_autoname(item_series)
    
    # Extract prefix and numeric part from template_item_code
    match = re.match(r"^([A-Z]+)(\d{5,6})$", template_item_code)
    
    if not match:
        frappe.throw("Template Item Code must be in the format PREFIX000001 (e.g., RM000001)")

    prefix, base_number = match.groups()
    base_number = int(base_number)
    
    # Get all items starting with this prefix
    existing_codes = frappe.get_all(
        "Item",
        filters={"item_code": ["like", f"{prefix}%"]},
        pluck="item_code"
    )

    # Extract numeric parts of matching codes
    suffixes = []
    for code in existing_codes:
        m = re.match(rf"^{prefix}(\d{{6}})$", code)
        if m:
            suffixes.append(int(m.group(1)))
            
    if prefix == "RM":
        
        all_numbers = sorted(set(suffixes + [base_number]))

        # Find next missing number
        next_number = None
        for i in range(1, all_numbers[-1] + 2):
            if i not in all_numbers:
                next_number = i
                break

        if not next_number:
            frappe.throw("Unable to determine next item code")
    else:
        next_number = max(suffixes or [base_number]) + 1
        
    suffix_str = f"{next_number:06d}"  # Pad to 6 digits like 000002

    # Set new item_code
    variant.item_code = f"{prefix}{suffix_str}"
    
import frappe
from frappe.utils import flt

@frappe.whitelist()
# def get_filtered_batches(doctype, txt, searchfield, start, page_len, filters):
#     from frappe.utils import cint
#     min_avg_length = flt(filters.get("average_length") or 0)
#     item_code = filters.get("item_code")
#     warehouse = filters.get("warehouse")
#     include_expired = cint(filters.get("include_expired") or 0)
    
#     conditions = ["average_length >= %(min_avg_length)s"]
    
#     if item_code:
#         conditions.append("item = %(item_code)s")

#     if warehouse:
#         conditions.append("warehouse = %(warehouse)s")

#     if not include_expired:
#         conditions.append("(expiry_date IS NULL OR expiry_date >= CURDATE())")

#     return frappe.db.sql(f"""
#         SELECT
#             name,
#             CONCAT(
#                 '<b>P:</b> ', CAST(IFNULL(pieces, 0) AS CHAR), ', ',
#                 '<b>L:</b> ', CAST(ROUND(IFNULL(average_length, 0), 2) AS CHAR), ', ',
#                 '<b>SW:</b> ', CAST(ROUND(IFNULL(section_weight, 0), 2) AS CHAR), ', ',
#                 CAST(ROUND(IFNULL(batch_qty, 0), 2) AS CHAR), ', ',
#                 IFNULL(batch_group_reference, 'N/A')
#             ) AS custom_label
#         FROM `tabBatch`
#         WHERE
#             {" AND ".join(conditions)} AND
#             name LIKE %(txt)s
#         ORDER BY name
#         LIMIT %(page_len)s OFFSET %(start)s
#     """, {
#         "min_avg_length": min_avg_length,
#         "txt": f"%{txt}%",
#         "start": start,
#         "page_len": page_len,
#         "item_code": item_code
#     })
    
def get_filtered_batches(doctype, txt, searchfield, start, page_len, filters):
    from frappe.utils import flt, cint

    min_avg_length = flt(filters.get("average_length") or 0)
    item_code = filters.get("item_code")
    warehouse = filters.get("warehouse")
    include_expired = cint(filters.get("include_expired") or 0)

    conditions = ["b.average_length >= %(min_avg_length)s"]

    if item_code:
        conditions.append("b.item = %(item_code)s")

    if not include_expired:
        conditions.append("(b.expiry_date IS NULL OR b.expiry_date >= CURDATE())")

    return frappe.db.sql(f"""
        SELECT
            b.name,
            CONCAT(
                '<b>P:</b> ', CAST(IFNULL(b.pieces, 0) AS CHAR), ', ',
                '<b>L:</b> ', CAST(ROUND(IFNULL(b.average_length, 0), 2) AS CHAR), ', ',
                '<b>SW:</b> ', CAST(ROUND(IFNULL(b.section_weight, 0), 2) AS CHAR), ', ',
                CAST(ROUND(IFNULL(b.batch_qty, 0), 2) AS CHAR), ', ',
                IFNULL(b.batch_group_reference, 'N/A')
            ) AS custom_label
        FROM `tabBatch` b
        WHERE
            {" AND ".join(conditions)}
            AND EXISTS (
                SELECT 1
                FROM `tabPiece Stock Ledger Entry` sle
                JOIN `tabSerial and Batch Bundle` sb ON sb.name = sle.serial_and_batch_bundle
                JOIN `tabSerial and Batch Entry` sb_entry ON sb_entry.parent = sb.name
                WHERE sb_entry.batch_no = b.name
                  {f"AND sle.warehouse = %(warehouse)s" if warehouse else ""}
                  AND sb_entry.qty > 0
            )
            AND b.name LIKE %(txt)s
        ORDER BY b.name
        LIMIT %(page_len)s OFFSET %(start)s
    """, {
        "min_avg_length": min_avg_length,
        "txt": f"%{txt}%",
        "start": start,
        "page_len": page_len,
        "item_code": item_code,
        "warehouse": warehouse
    })         

@frappe.whitelist()
def get_cutting_plan_batches(doctype, txt, searchfield, start, page_len, filters):
    """
    Get batches for cutting plan with rich presentation format
    No length filtering - shows all batches for the item and warehouse
    """
    from frappe.utils import cint

    item_code = filters.get("item_code")
    warehouse = filters.get("warehouse")
    supplier_name = (filters.get("supplier_name") or "").strip()
    include_expired = cint(filters.get("include_expired") or 0)

    conditions = []

    if item_code:
        conditions.append("b.item = %(item_code)s")

    if not include_expired:
        conditions.append("(b.expiry_date IS NULL OR b.expiry_date >= CURDATE())")

    # Build WHERE clause
    where_clause = ""
    if conditions:
        where_clause = " AND ".join(conditions) + " AND "
    
    # Optional supplier filter via PR linkage (and Stock Entry custom supplier)
    supplier_join = ""
    supplier_condition = ""
    if supplier_name:
        supplier_join = """
            LEFT JOIN `tabPurchase Receipt` pr
                ON pr.name = b.reference_name
                AND b.reference_doctype = 'Purchase Receipt'
            LEFT JOIN `tabStock Entry` se
                ON se.name = b.reference_name
                AND b.reference_doctype = 'Stock Entry'
        """
        supplier_condition = """
            AND (
                (pr.name IS NOT NULL AND (pr.supplier_name = %(supplier_name)s OR pr.supplier = %(supplier_name)s))
                OR (se.name IS NOT NULL AND se.custom_supplier = %(supplier_name)s)
            )
        """

    return frappe.db.sql(f"""
        SELECT
            b.name,
            CONCAT(
                '<b>P:</b> ', CAST(IFNULL(b.pieces, 0) AS CHAR), ', ',
                '<b>L:</b> ', CAST(ROUND(IFNULL(b.average_length, 0), 2) AS CHAR), ', ',
                '<b>SW:</b> ', CAST(ROUND(IFNULL(b.section_weight, 0), 2) AS CHAR), ', ',
                CAST(ROUND(IFNULL(b.batch_qty, 0), 2) AS CHAR), ', ',
                IFNULL(b.batch_group_reference, 'N/A')
            ) AS custom_label
        FROM `tabBatch` b
        {supplier_join}
        WHERE
            {where_clause}
            EXISTS (
                SELECT 1
                FROM `tabPiece Stock Ledger Entry` sle
                JOIN `tabSerial and Batch Bundle` sb ON sb.name = sle.serial_and_batch_bundle
                JOIN `tabSerial and Batch Entry` sb_entry ON sb_entry.parent = sb.name
                WHERE sb_entry.batch_no = b.name
                  {f"AND sle.warehouse = %(warehouse)s" if warehouse else ""}
                  AND sb_entry.qty > 0
            )
            AND b.name LIKE %(txt)s
            {supplier_condition}
            AND b.batch_qty > 0
        ORDER BY b.name
        LIMIT %(page_len)s OFFSET %(start)s
    """, {
        "txt": f"%{txt}%",
        "start": start,
        "page_len": page_len,
        "item_code": item_code,
        "warehouse": warehouse,
        "supplier_name": supplier_name
    })

@frappe.whitelist()    
def get_work_order_details(work_orders):
    """
    Fetch detailed information for selected work orders
    """
    import json
    
    if isinstance(work_orders, str):
        work_orders = json.loads(work_orders)
    
    items = []
    
    for wo_data in work_orders:
        work_order_name = wo_data.get('work_order')
        
        # Fetch work order details
        work_order = frappe.get_doc('Work Order', work_order_name)
        
        for wo_items in work_order.required_items:
            items.append({
            'item_code': wo_items.item_code,
            'item_name': wo_items.item_name,
            'source_warehouse': wo_items.source_warehouse,
            'qty': wo_items.get('required_qty', 0),
            'basic_rate': wo_items.get('basic_rate', 0),
            'work_order_reference':work_order_name,
            'sales_order': work_order.sales_order,
            'production_item': work_order.production_item,
            "fg_item_name": work_order.item_name
            })
    return items

@frappe.whitelist()
def get_production_items_from_work_orders(work_orders):
    """
    Get production items from selected work orders
    """
    import json
    
    if isinstance(work_orders, str):
        work_orders = json.loads(work_orders)
    
    production_items = []
    
    for work_order_name in work_orders:
        # Fetch work order details
        work_order = frappe.get_doc('Work Order', work_order_name)
        
        # Add production item to the list
        if work_order.production_item and work_order.production_item not in production_items:
            production_items.append({
                "fg_item": work_order.production_item,
                "work_order_reference": work_order.name
            })  
    
    return production_items

@frappe.whitelist()
def get_work_orders_by_rm(rm_item, filters=None):
    """
    Get work orders that have a specific raw material in their required items
    """
    if not filters:
        filters = {}

    if isinstance(filters, str):
        import json
        filters = json.loads(filters)

    query = """
        SELECT DISTINCT wo.name, wo.production_item, wo.item_name
        FROM `tabWork Order` wo
        INNER JOIN `tabWork Order Item` woi ON wo.name = woi.parent
        WHERE woi.item_code = %(rm_item)s
    """

    conditions = []
    params = {"rm_item": rm_item}

    # ✅ Fix: expand status NOT IN list into placeholders
    if filters.get("status"):
        status_filter = filters["status"]
        if isinstance(status_filter, list) and len(status_filter) == 2 and status_filter[0] == "not in":
            placeholders = []
            for i, status in enumerate(status_filter[1]):
                key = f"status_{i}"
                placeholders.append(f"%({key})s")
                params[key] = status
            conditions.append(f"wo.status NOT IN ({', '.join(placeholders)})")

    if filters.get("docstatus") is not None:
        conditions.append("wo.docstatus = %(docstatus)s")
        params["docstatus"] = filters["docstatus"]

    if filters.get("production_item"):
        conditions.append("wo.production_item = %(production_item)s")
        params["production_item"] = filters["production_item"]

    if filters.get("name") and isinstance(filters["name"], list) and filters["name"][0] == "like":
        conditions.append("wo.name LIKE %(work_order_name)s")
        params["work_order_name"] = filters["name"][1]

    if conditions:
        query += " AND " + " AND ".join(conditions)

    query += " ORDER BY wo.creation DESC LIMIT 20"

    # 🔍 Debugging log
    frappe.log_error(f"Query: {query}\nParams: {params}", "get_work_orders_by_rm Debug")

    return frappe.db.sql(query, params, as_dict=True)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_scrap_items(doctype, txt, searchfield, start, page_len, filters):
    import json
    
    # Parse filters if it's a JSON string
    if isinstance(filters, str):
        filters = json.loads(filters)
    
    allowed_items = filters.get('allowed_items', []) if filters else []
    
    conditions = []
    values = []
    
    # Search condition
    if txt:
        conditions.append(f"`tabItem`.`{searchfield}` LIKE %s")
        values.append(f"%{txt}%")
    
    # Build OR condition for allowed items or SCRAP group
    or_conditions = ["`tabItem`.`item_group` = %s"]
    values.append('SCRAP')
    
    if allowed_items and len(allowed_items) > 0:
        placeholders = ', '.join(['%s'] * len(allowed_items))
        or_conditions.append(f"`tabItem`.`name` IN ({placeholders})")
        values.extend(allowed_items)
    
    conditions.append(f"({' OR '.join(or_conditions)})")
    
    # Exclude FINISH GOODS
    conditions.append("`tabItem`.`item_group` != %s")
    values.append('FINISH GOODS')
    
    where_clause = ' AND '.join(conditions)
    
    return frappe.db.sql(f"""
        SELECT `tabItem`.`name`, `tabItem`.`item_name`
        FROM `tabItem`
        WHERE {where_clause}
        ORDER BY
            CASE WHEN `tabItem`.`name` LIKE %s THEN 0 ELSE 1 END,
            `tabItem`.`name`
        LIMIT %s OFFSET %s
    """, tuple(values + [f"{txt}%", page_len, start]))

@frappe.whitelist()
def get_items_from_cut_plan(work_order):
    
    if not work_order:
        return []

    cut_plan_ref = frappe.get_doc("Work Order", work_order).cutting_plan_reference
    fg_item = frappe.get_doc("Work Order", work_order).production_item
    rows = frappe.get_all(
        "Cutting plan Finish Second",
        filters={"work_order_reference": work_order, "fg_item": fg_item,"parent":cut_plan_ref},
        fields=[
            "item as item_code",
            "batch as batch_no",
            "qty",
            # "warehouse as t_warehouse",
            "pieces",
            "length_size as average_length",
            "section_weight",
            "lot_no",
            "fg_item",
            "semi_fg_length",
            "work_order_reference",
            "total_pcs",
        ],
        order_by="creation asc",
    )

    # Enrich with UOM metadata and batch flags needed by Stock Entry Detail
    for row in rows:
        row["use_serial_batch_fields"] = 1
        row["required_stock_in_pieces"] = 1
        # Default conversion factor and stock/uom
        stock_uom = frappe.db.get_value("Item", row.get("item_code"), "stock_uom")
        row["stock_uom"] = stock_uom
        row["uom"] = stock_uom
        row["conversion_factor"] = 1
        row["basic_rate"] = 0

    return rows


@frappe.whitelist()
def get_finished_cut_plan_from_mtm(work_orders):
    """
    For Finished Cut Plan: gather Stock Entry (Material Transfer for Manufacture) items
    linked to given Work Orders and prepare:
    - detail_rows: consolidated by (item_code, batch_no, s_warehouse)
    - finish_rows: non-consolidated, one row per stock entry item

    work_orders: list of work order names or JSON string
    """
    import json

    if isinstance(work_orders, str):
        work_orders = json.loads(work_orders)

    if not work_orders:
        return {"detail_rows": [], "finish_rows": []}

    # Pre-fetch WO -> FG item map
    wo_to_fg = {}
    for wo_name in work_orders:
        try:
            wo_to_fg[wo_name] = frappe.db.get_value("Work Order", wo_name, "production_item")
        except Exception:
            wo_to_fg[wo_name] = None

    # Fetch submitted MTM Stock Entries for provided WOs
    se_list = frappe.get_all(
        "Stock Entry",
        filters={
            "docstatus": 1,
            "work_order": ["in", work_orders],
            "stock_entry_type": "Material Transfer for Manufacture",
        },
        fields=["name", "work_order", "posting_date", "posting_time"],
        order_by="posting_date asc, posting_time asc, name asc",
    )

    detail_key_to_row = {}
    finish_rows = []

    for se in se_list:
        # Get items
        items = frappe.get_all(
            "Stock Entry Detail",
            filters={"parent": se["name"]},
            fields=[
                "item_code",
                "item_name",
                "s_warehouse",
                "t_warehouse",
                "qty",
                "batch_no",
                # custom fields if present
                "pieces",
                "average_length",
                "section_weight",
                "lot_no"
            ],
            order_by="idx asc",
        )

        for it in items:
            item_code = it.get("item_code")
            batch_no = it.get("batch_no")
            s_wh = it.get("t_warehouse")

            # Consolidated key for detail table
            key = (item_code or "", batch_no or "", s_wh or "")
            if key not in detail_key_to_row:
                detail_key_to_row[key] = {
                    "item_code": item_code,
                    "item_name": it.get("item_name"),
                    "source_warehouse": s_wh,
                    "qty": 0.0,
                    "pieces": 0.0,
                    "length_size": it.get("average_length"),
                    "section_weight": it.get("section_weight"),
                    "lot_no": it.get("lot_no"),
                    "batch": batch_no,
                    "work_order_reference": se.get("work_order"),
                }
            # Sum quantities/pieces
            row = detail_key_to_row[key]
            row["qty"] = float(row.get("qty") or 0) + float(it.get("qty") or 0)
            row["pieces"] = float(row.get("pieces") or 0) + float(it.get("pieces") or 0)

            # Keep length/section_weight consistent if same, else drop to None
            if row.get("length_size") != it.get("average_length"):
                row["length_size"] = row.get("length_size") if row.get("length_size") == it.get("average_length") else row.get("length_size")
            if row.get("section_weight") != it.get("section_weight"):
                row["section_weight"] = row.get("section_weight") if row.get("section_weight") == it.get("section_weight") else row.get("section_weight")

            # Non-consolidated finish row
            finish_rows.append({
                "item": item_code,
                "batch": batch_no,
                "qty": it.get("qty"),
                "pieces": it.get("pieces"),
                "length_size": it.get("average_length"),
                # "section_weight": it.get("section_weight"),
                "lot_no": it.get("lot_no"),
                "rm_reference_batch": batch_no,
                "work_order_reference": se.get("work_order"),
                "fg_item": wo_to_fg.get(se.get("work_order")),
                "section_weight":frappe.db.get_value("Item", wo_to_fg.get(se.get("work_order")),'weight_per_meter')
            })

    detail_rows = list(detail_key_to_row.values())
    return {"detail_rows": detail_rows, "finish_rows": finish_rows}


@frappe.whitelist()
def get_finished_cut_plan_from_manufacturing(work_orders):
    """
    For Finished Cut Plan: gather finished items from submitted Manufacture Stock Entries
    linked to given Work Orders and prepare rows to append into `cut_plan_detail`.

    - Only finished item rows are considered (FG lines). We identify them as rows where
      the `item_code` matches the Work Order's `production_item` and the row has a
      target warehouse (t_warehouse). These are the produced outputs.
    - Rows are consolidated by (item_code, batch_no, t_warehouse) so that "batch * qty"
      is represented as one row per batch with total qty.

    Parameters
    ----------
    work_orders: list[str] | str
        List of Work Order names, or a JSON-encoded list.
    """
    import json

    if isinstance(work_orders, str):
        work_orders = json.loads(work_orders)

    if not work_orders:
        return {"detail_rows": [], "finish_rows": []}

    # Pre-fetch WO -> FG item map
    wo_to_fg = {}
    for wo_name in work_orders:
        try:
            wo_to_fg[wo_name] = frappe.db.get_value("Work Order", wo_name, "production_item")
        except Exception:
            wo_to_fg[wo_name] = None

    # Fetch submitted Manufacture Stock Entries for provided WOs
    se_list = frappe.get_all(
        "Stock Entry",
        filters={
            "docstatus": 1,
            "work_order": ["in", work_orders],
            "stock_entry_type": "Manufacture",
        },
        fields=["name", "work_order", "posting_date", "posting_time"],
        order_by="posting_date asc, posting_time asc, name asc",
    )

    detail_key_to_row = {}
    finish_rows = []

    for se in se_list:
        work_order_name = se.get("work_order")
        fg_item_code = wo_to_fg.get(work_order_name)

        # Get items on this Stock Entry
        items = frappe.get_all(
            "Stock Entry Detail",
            filters={"parent": se["name"]},
            fields=[
                "item_code",
                "item_name",
                "s_warehouse",
                "t_warehouse",
                "qty",
                "batch_no",
                # optional/custom fields used in UI
                "pieces",
                "total_pcs",
                "average_length",
                "lot_no",
            ],
            order_by="idx asc",
        )

        for it in items:
            item_code = it.get("item_code")
            batch_no = it.get("batch_no")
            t_wh = it.get("t_warehouse")

            # FINISHED rows: only FG item with incoming qty (t_warehouse present)
            if fg_item_code and item_code == fg_item_code and t_wh:
                key = (item_code or "", batch_no or "", t_wh or "")
                if key not in detail_key_to_row:
                    detail_key_to_row[key] = {
                        "item_code": item_code,
                        "item_name": it.get("item_name"),
                        # For Finished items, use t_warehouse as the source for subsequent planning
                        "source_warehouse": t_wh,
                        "qty": 0.0,
                        "pieces": 0.0,
                        "batch": batch_no,
                        "work_order_reference": work_order_name,
                        # Use FG item's section weight if available
                        "section_weight": frappe.db.get_value("Item", fg_item_code, "weight_per_meter"),
                    }
                row = detail_key_to_row[key]
                row["qty"] = float(row.get("qty") or 0) + float(it.get("qty") or 0)
                row["pieces"] = float(row.get("pieces") or 0) + float(it.get("total_pcs") or it.get("pieces") or 0)
                continue

            # NON-FINISHED rows: append to finish_rows for Cutting Plan Finish table
            finish_rows.append({
                "item": item_code,
                "batch": batch_no,
                "qty": it.get("qty"),
                "pieces": int(it.get("total_pcs")),
                "length_size": it.get("average_length"),
                "lot_no": it.get("lot_no"),
                "rm_reference_batch": batch_no,
                "work_order_reference": work_order_name,
                "fg_item": fg_item_code,
                # For consumed components, use source warehouse
                "warehouse": it.get("s_warehouse"),
            })

    detail_rows = list(detail_key_to_row.values())
    
    return {"detail_rows": detail_rows, "finish_rows": finish_rows}


@frappe.whitelist()
def get_material_request_for_item(item_code):
    # You can safely use ignore_permissions=True here
    res = frappe.db.get_value("Material Request Item", {"item_code": item_code}, "parent", as_dict=True)
    return res


@frappe.whitelist()
def get_items_with_material_request(doctype, txt, searchfield, start, page_len, filters):
    allowed_groups = filters.get("item_groups", [])
    if isinstance(allowed_groups, str):
        allowed_groups = frappe.parse_json(allowed_groups)

    return frappe.db.sql("""
        SELECT DISTINCT i.name, i.item_name
        FROM `tabItem` i
        INNER JOIN `tabMaterial Request Item` mri ON mri.item_code = i.name
        INNER JOIN `tabMaterial Request` mr ON mr.name = mri.parent
        WHERE i.item_group IN %(groups)s
          AND mr.docstatus = 1
          AND (i.name LIKE %(txt)s OR i.item_name LIKE %(txt)s)
        ORDER BY i.name
        LIMIT %(start)s, %(page_len)s
    """, {
        "groups": tuple(allowed_groups),
        "txt": f"%{txt}%",
        "start": start,
        "page_len": page_len
    })

@frappe.whitelist()
def get_so_item_pcs(sales_order, item_code, sales_order_item, row_id=None):

    if not sales_order or not item_code:
        return {}

    so_item = frappe.db.get_value(
        "Sales Order Item",
        {
            "parent": sales_order,
            "item_code": item_code,
            "name": sales_order_item
        },
        [ "length_size", "pieces", "total_weight", "stock_reserved_qty","assorted_length"],
        as_dict=True,
    ) or {}
    so = frappe.db.get_value(
        "Sales Order",
        sales_order,
        ["po_no", "customer", "customer_name"],
        as_dict=True
    ) or {}
    
    reserved = flt(so_item.get("stock_reserved_qty"))
    planned_qty = flt(frappe.form_dict.get("planned_qty"))

    adjusted_planned_qty = planned_qty - reserved
    if adjusted_planned_qty < 0:
        adjusted_planned_qty = 0
        
    so_item.update({
        "planned_qty": adjusted_planned_qty,
        "po_no": so.get("po_no", ""),
        "customer": so.get("customer", ""),
        "customer_name": so.get("customer_name", ""),
        "row_id": row_id
    })

    return so_item

@frappe.whitelist()
def update_latest_wo_from_pp(production_plan):

    if not production_plan:
        return {"status": "no_pp"}

    # Fetch all Work Orders for this PP
    wo_list = frappe.get_all(
        "Work Order",
        filters={"production_plan": production_plan},
        fields=["name", "production_plan_item", "sales_order"]
    )

    if not wo_list:
        return {"status": "no_wo"}

    updated = 0

    for wo in wo_list:
        if not wo.production_plan_item:
            continue

        # Fetch linked PP Item row
        pp_item = frappe.db.get_value(
            "Production Plan Item",
            wo.production_plan_item,
            ["length_size_m", "pieces", "po_no","assorted_length","remark"],
            as_dict=True
        )
        sales_order = frappe.db.get_value(
            "Sales Order", 
            {"name": wo.sales_order}, 
            ["customer", "customer_name"],
            as_dict=True)
        
        if not pp_item:
            continue

        wo_doc = frappe.get_doc("Work Order", wo.name)
        wo_doc.length = pp_item.length_size_m or 0
        wo_doc.pieces = pp_item.pieces or 0
        wo_doc.po_no = pp_item.po_no or ""
        wo_doc.assorted_length = pp_item.assorted_length or ""
        wo_doc.remarks = pp_item.remark or ""
        wo_doc.customer = sales_order.customer
        wo_doc.customer_name = sales_order.customer_name
        wo_doc.skip_transfer = 1

        wo_doc.save()

        wo_doc.submit()

        updated += 1

    return {
        "status": "ok",
        "updated_wo": updated
    }


@frappe.whitelist()
def populate_pending_work_orders(filters=None):

    filters = frappe.parse_json(filters) if filters else {}

    conditions = []
    values = {}

    # Base Conditions
    conditions.append("wo.status NOT IN ('Draft', 'Completed', 'Cancelled')")

    # Company Filter
    if filters.get("company"):
        conditions.append("wo.company = %(company)s")
        values["company"] = filters.get("company")

    # Exclude already used Work Orders in other Finish Work Orders
    # if filters.get("current_doc"):
    #     conditions.append("""
    #         wo.name NOT IN (
    #             SELECT pwo.work_order
    #             FROM `tabPending Work Orders` pwo
    #             INNER JOIN `tabFinish Work Order` fwo
    #                 ON fwo.name = pwo.parent
    #             WHERE fwo.docstatus != 2
    #             AND fwo.name != %(current_doc)s
    #         )
    #     """)
    #     values["current_doc"] = filters.get("current_doc")
    # else:
    #     conditions.append("""
    #         wo.name NOT IN (
    #             SELECT pwo.work_order
    #             FROM `tabPending Work Orders` pwo
    #             INNER JOIN `tabFinish Work Order` fwo
    #                 ON fwo.name = pwo.parent
    #             WHERE fwo.docstatus != 2
    #         )
    #     """)

    # Item Name Filter
    if filters.get("item_name"):
        conditions.append("wo.item_name LIKE %(item_name)s")
        values["item_name"] = f"%{filters.get('item_name')}%"

    # Work Order Filter
    if filters.get("wo_number"):
        conditions.append("wo.name = %(wo_number)s")
        values["wo_number"] = filters.get("wo_number")

    # Sales Order Filter
    if filters.get("sales_order"):
        conditions.append("wo.sales_order = %(sales_order)s")
        values["sales_order"] = filters.get("sales_order")

    # Date Filter
    if filters.get("date"):
        conditions.append("wo.creation BETWEEN %(from_date)s AND %(to_date)s")
        values["from_date"] = f"{filters.get('date')} 00:00:00"
        values["to_date"] = f"{filters.get('date')} 23:59:59"

    where_clause = " AND ".join(conditions)

    query = f"""
        SELECT
            wo.name,
            wo.source_warehouse,
            wo.customer,
            so.customer_name,
            so.quality_required,
            wo.fg_warehouse,
            wo.assorted_length,
            wo.production_item,
            wo.stock_uom,
            wo.item_name,
            wo.pieces,
            wo.pending_pcs,
            wo.length,
            wo.completed_pcs,
            wo.variation_allowed,
            wo.po_no,
            wo.remarks,
            wo.qty,
            wo.sales_order,
            wo.produced_qty,
            i.weight_per_meter

        FROM `tabWork Order` wo

        LEFT JOIN `tabItem` i
            ON i.name = wo.production_item

        LEFT JOIN `tabSales Order` so
            ON so.name = wo.sales_order

        WHERE {where_clause}

        ORDER BY wo.item_name, wo.customer_name , wo.length ASC
    """

    return frappe.db.sql(query, values=values, as_dict=True)
    

@frappe.whitelist()
def sync_work_orders_from_sales_order(sales_order):

    so = frappe.get_doc("Sales Order", sales_order)

    # Build item map from Sales Order Items
    item_map = {}

    for row in so.items:
        item_map[row.item_code] = {
            "pieces": row.pieces,
            "length": row.length_size,
            "completed_pcs": row.completed_pcs,
            "pending_pcs": row.pending_pcs,
            "variation_allowed": row.variation_allowed
        }

    work_orders = frappe.get_all(
        "Work Order",
        filters={"sales_order": sales_order},
        fields=["name", "production_item"]
    )

    updated = 0

    for wo in work_orders:
        if wo.production_item not in item_map:
            continue

        data = item_map[wo.production_item]

        frappe.db.set_value("Work Order", wo.name, {
            "pieces": data["pieces"],
            "length": data["length"],
            "completed_pcs": data["completed_pcs"],
            "pending_pcs": data["pending_pcs"],
            "variation_allowed": data["variation_allowed"],
            "po_no": so.po_no
        })

        updated += 1

    return f"{updated} Work Orders updated successfully"

def get_requested_item_qty(sales_order):
    result = {}
    for d in frappe.db.get_all(
        "Material Request Item",
        filters={"docstatus": 1, "sales_order": sales_order},
        fields=["sales_order_item", "sum(qty) as qty", "sum(received_qty) as received_qty"],
        group_by="sales_order_item",
    ):
        result[d.sales_order_item] = frappe._dict({"qty": d.qty, "received_qty": d.received_qty})

    return result

@frappe.whitelist()
def make_material_request(source_name, target_doc=None):
    requested_item_qty = get_requested_item_qty(source_name)

    def postprocess(source, target):
        if source.tc_name and frappe.db.get_value("Terms and Conditions", source.tc_name, "buying") != 1:
            target.tc_name = None
            target.terms = None

    def get_remaining_qty(so_item):
        reserved = flt(so_item.get("stock_reserved_qty"))
        effective_qty = flt(so_item.qty) - reserved

        return flt(
            effective_qty
            - flt(requested_item_qty.get(so_item.name, {}).get("qty"))
            - max(
                flt(so_item.get("delivered_qty"))
                - flt(requested_item_qty.get(so_item.name, {}).get("received_qty")),
                0,
            )
        )

    def update_item(source, target, source_parent):
        # qty is for packed items, because packed items don't have stock_qty field
        target.project = source_parent.project
        target.qty = get_remaining_qty(source)
        target.stock_qty = flt(target.qty) * flt(target.conversion_factor)
        target.actual_qty = get_bin_details(
            target.item_code, target.warehouse, source_parent.company, True
        ).get("actual_qty", 0)

        args = target.as_dict().copy()
        args.update(
            {
                "company": source_parent.get("company"),
                "price_list": frappe.db.get_single_value("Buying Settings", "buying_price_list"),
                "currency": source_parent.get("currency"),
                "conversion_rate": source_parent.get("conversion_rate"),
            }
        )

        target.rate = flt(
            get_price_list_rate(args=args, item_doc=frappe.get_cached_doc("Item", target.item_code)).get(
                "price_list_rate"
            )
        )
        target.amount = target.qty * target.rate

    doc = get_mapped_doc(
        "Sales Order",
        source_name,
        {
            "Sales Order": {"doctype": "Material Request", "validation": {"docstatus": ["=", 1]}},
            "Packed Item": {
                "doctype": "Material Request Item",
                "field_map": {"parent": "sales_order", "uom": "stock_uom"},
                "postprocess": update_item,
            },
            "Sales Order Item": {
                "doctype": "Material Request Item",
                "field_map": {
                    "name": "sales_order_item",
                    "parent": "sales_order",
                    "delivery_date": "schedule_date",
                    "bom_no": "bom_no",
                },
                "condition": lambda item: (
                    not item.is_manufacture
                    and not frappe.db.exists(
                        "Product Bundle", {"name": item.item_code, "disabled": 0}
                    )
                    and get_remaining_qty(item) > 0
                ),
                "postprocess": update_item,
            },
        },
        target_doc,
        postprocess,
    )

    return doc

def set_delivery_date(items, sales_order):
    delivery_dates = frappe.get_all(
        "Sales Order Item", filters={"parent": sales_order}, fields=["delivery_date", "item_code"]
    )

    delivery_by_item = frappe._dict()
    for date in delivery_dates:
        delivery_by_item[date.item_code] = date.delivery_date

    for item in items:
        if item.product_bundle:
            item.schedule_date = delivery_by_item[item.product_bundle]
   
@frappe.whitelist()
def make_purchase_order(source_name, selected_items=None, target_doc=None):
    if not selected_items:
        return

    if isinstance(selected_items, str):
        selected_items = json.loads(selected_items)

    items_to_map = [item.get("item_code") for item in selected_items if item.get("item_code")]
    items_to_map = list(set(items_to_map))

    def is_drop_ship_order(target):
        drop_ship = True
        for item in target.items:
            if not item.delivered_by_supplier:
                drop_ship = False
                break

        return drop_ship

    def set_missing_values(source, target):
        target.supplier = ""
        target.apply_discount_on = ""
        target.additional_discount_percentage = 0.0
        target.discount_amount = 0.0
        target.inter_company_order_reference = ""
        target.shipping_rule = ""
        target.tc_name = ""
        target.terms = ""
        target.payment_terms_template = ""
        target.payment_schedule = []

        if is_drop_ship_order(target):
            if source.shipping_address_name:
                target.shipping_address = source.shipping_address_name
                target.shipping_address_display = source.shipping_address
            else:
                target.shipping_address = source.customer_address
                target.shipping_address_display = source.address_display

            target.customer_contact_person = source.contact_person
            target.customer_contact_display = source.contact_display
            target.customer_contact_mobile = source.contact_mobile
            target.customer_contact_email = source.contact_email
        else:
            target.customer = target.customer_name = target.shipping_address = None

        target.run_method("set_missing_values")
        if not target.taxes:
            target.append_taxes_from_item_tax_template()
        target.run_method("calculate_taxes_and_totals")

    def update_item(source, target, source_parent):
        target.schedule_date = source.delivery_date
        reserved = flt(source.get("stock_reserved_qty"))

        effective_qty = flt(source.qty) - reserved
        effective_stock_qty = flt(source.stock_qty) - reserved

        target.qty = effective_qty - (flt(source.ordered_qty) / flt(source.conversion_factor))
        target.stock_qty = effective_stock_qty - flt(source.ordered_qty)
        target.project = source_parent.project

    def update_item_for_packed_item(source, target, source_parent):
        target.qty = flt(source.qty) - flt(source.ordered_qty)

    # po = frappe.get_list("Purchase Order", filters={"sales_order":source_name, "supplier":supplier, "docstatus": ("<", "2")})
    doc = get_mapped_doc(
        "Sales Order",
        source_name,
        {
            "Sales Order": {
                "doctype": "Purchase Order",
                "field_no_map": [
                    "address_display",
                    "contact_display",
                    "contact_mobile",
                    "contact_email",
                    "contact_person",
                    "taxes_and_charges",
                    "shipping_address",
                    "dispatch_address",
                ],
                "validation": {"docstatus": ["=", 1]},
            },
            "Sales Order Item": {
                "doctype": "Purchase Order Item",
                "field_map": [
                    ["name", "sales_order_item"],
                    ["parent", "sales_order"],
                    ["stock_uom", "stock_uom"],
                    ["uom", "uom"],
                    ["conversion_factor", "conversion_factor"],
                    ["delivery_date", "schedule_date"],
                ],
                "field_no_map": [
                    "rate",
                    "price_list_rate",
                    "item_tax_template",
                    "discount_percentage",
                    "discount_amount",
                    "supplier",
                    "pricing_rules",
                ],
                "postprocess": update_item,
                "condition": lambda doc: (
                    flt(doc.ordered_qty) < (flt(doc.stock_qty) - flt(doc.get("stock_reserved_qty")))
                    and doc.item_code in items_to_map
                    and not is_product_bundle(doc.item_code)
                    and not doc.is_manufacture
                ),
            },
            "Packed Item": {
                "doctype": "Purchase Order Item",
                "field_map": [
                    ["name", "sales_order_packed_item"],
                    ["parent", "sales_order"],
                    ["uom", "uom"],
                    ["conversion_factor", "conversion_factor"],
                    ["parent_item", "product_bundle"],
                    ["rate", "rate"],
                ],
                "field_no_map": [
                    "price_list_rate",
                    "item_tax_template",
                    "discount_percentage",
                    "discount_amount",
                    "supplier",
                    "pricing_rules",
                ],
                "postprocess": update_item_for_packed_item,
                "condition": lambda doc: doc.parent_item in items_to_map
                and flt(doc.ordered_qty) < flt(doc.qty),
            },
        },
        target_doc,
        set_missing_values,
    )

    set_delivery_date(doc.items, source_name)
    doc.set_onload("load_after_mapping", False)

    return doc

@frappe.whitelist()
def update_child_qty_rate(parent_doctype, trans_items, parent_doctype_name, child_docname="items"):
    from erpnext.buying.doctype.supplier_quotation.supplier_quotation import get_purchased_items
    from erpnext.selling.doctype.quotation.quotation import get_ordered_items

    def check_doc_permissions(doc, perm_type="create"):
        try:
            doc.check_permission(perm_type)
        except frappe.PermissionError:
            actions = {"create": "add", "write": "update"}

            frappe.throw(
                _("You do not have permissions to {} items in a {}.").format(
                    actions[perm_type], parent_doctype
                ),
                title=_("Insufficient Permissions"),
            )

    def validate_workflow_conditions(doc):
        workflow = get_workflow_name(doc.doctype)
        if not workflow:
            return

        workflow_doc = frappe.get_doc("Workflow", workflow)
        current_state = doc.get(workflow_doc.workflow_state_field)
        roles = frappe.get_roles()

        transitions = []
        for transition in workflow_doc.transitions:
            if transition.next_state == current_state and transition.allowed in roles:
                if not is_transition_condition_satisfied(transition, doc):
                    continue
                transitions.append(transition.as_dict())

        if not transitions:
            frappe.throw(
                _("You are not allowed to update as per the conditions set in {} Workflow.").format(
                    get_link_to_form("Workflow", workflow)
                ),
                title=_("Insufficient Permissions"),
            )

    def get_new_child_item(item_row):
        child_doctype = parent_doctype + " Item"
        return set_order_defaults(parent_doctype, parent_doctype_name, child_doctype, child_docname, item_row)

    def is_allowed_zero_qty():
        if parent_doctype == "Sales Order":
            return frappe.db.get_single_value("Selling Settings", "allow_zero_qty_in_sales_order") or False
        elif parent_doctype == "Purchase Order":
            return frappe.db.get_single_value("Buying Settings", "allow_zero_qty_in_purchase_order") or False
        return False

    def validate_quantity_and_rate(child_item, new_data):
        if not flt(new_data.get("qty")) and not is_allowed_zero_qty():
            frappe.throw(
                _("Row #{0}:Quantity for Item {1} cannot be zero.").format(
                    new_data.get("idx"), frappe.bold(new_data.get("item_code"))
                ),
                title=_("Invalid Qty"),
            )

        qty_limits = {
            "Sales Order": ("delivered_qty", _("Cannot set quantity less than delivered quantity")),
            "Purchase Order": ("received_qty", _("Cannot set quantity less than received quantity")),
        }

        if parent_doctype in qty_limits:
            qty_field, error_message = qty_limits[parent_doctype]
            if flt(new_data.get("qty")) < flt(child_item.get(qty_field)):
                frappe.throw(
                    _("Row #{0}:").format(new_data.get("idx"))
                    + error_message.format(frappe.bold(new_data.get("item_code"))),
                    title=_("Invalid Qty"),
                )

        if parent_doctype in ["Quotation", "Supplier Quotation"]:
            if (parent_doctype == "Quotation" and not ordered_items) or (
                parent_doctype == "Supplier Quotation" and not purchased_items
            ):
                return

            qty_to_check = (
                ordered_items.get(child_item.name)
                if parent_doctype == "Quotation"
                else purchased_items.get(child_item.name)
            )

            if qty_to_check:
                if not rate_unchanged:
                    frappe.throw(
                        _(
                            "Cannot update rate as item {0} is already ordered or purchased against this quotation"
                        ).format(frappe.bold(new_data.get("item_code")))
                    )

                if flt(new_data.get("qty")) < qty_to_check:
                    frappe.throw(_("Cannot reduce quantity than ordered or purchased quantity"))

    def should_update_supplied_items(doc) -> bool:
        """Subcontracted PO can allow following changes *after submit*:

        1. Change rate of subcontracting - regardless of other changes.
        2. Change qty and/or add new items and/or remove items
                Exception: Transfer/Consumption is already made, qty change not allowed.
        """

        supplied_items_processed = any(
            item.supplied_qty or item.consumed_qty or item.returned_qty for item in doc.supplied_items
        )

        update_supplied_items = any_qty_changed or items_added_or_removed or any_conversion_factor_changed
        if update_supplied_items and supplied_items_processed:
            frappe.throw(_("Item qty can not be updated as raw materials are already processed."))

        return update_supplied_items

    def validate_fg_item_for_subcontracting(new_data, is_new):
        if is_new:
            if not new_data.get("fg_item"):
                frappe.throw(
                    _("Finished Good Item is not specified for service item {0}").format(
                        new_data["item_code"]
                    )
                )
            else:
                is_sub_contracted_item, default_bom = frappe.db.get_value(
                    "Item", new_data["fg_item"], ["is_sub_contracted_item", "default_bom"]
                )

                if not is_sub_contracted_item:
                    frappe.throw(
                        _("Finished Good Item {0} must be a sub-contracted item").format(new_data["fg_item"])
                    )
                elif not default_bom:
                    frappe.throw(_("Default BOM not found for FG Item {0}").format(new_data["fg_item"]))

        if not new_data.get("fg_item_qty"):
            frappe.throw(_("Finished Good Item {0} Qty can not be zero").format(new_data["fg_item"]))

    data = json.loads(trans_items)
    any_qty_changed = False  # updated to true if any item's qty changes
    items_added_or_removed = False  # updated to true if any new item is added or removed
    any_conversion_factor_changed = False
    # Madhav: track stock-impacting SO line changes so we do NOT wipe all SREs
    # on every Update Items (rate / date / length / pieces alone must keep reservations).
    removed_so_item_names = []
    so_lines_needing_sre_cancel = set()

    parent = frappe.get_doc(parent_doctype, parent_doctype_name)

    check_doc_permissions(parent, "write")

    if parent_doctype == "Sales Order":
        updated_item_names = {d.get("docname") for d in data if d.get("docname")}
        removed_so_item_names = [
            item.name for item in parent.items if item.name not in updated_item_names
        ]

    if parent_doctype == "Quotation":
        ordered_items = get_ordered_items(parent.name)
        _removed_items = validate_and_delete_children(parent, data, ordered_items)
    elif parent_doctype == "Supplier Quotation":
        purchased_items = get_purchased_items(parent.name)
        _removed_items = validate_and_delete_children(parent, data, purchased_items)
    else:
        _removed_items = validate_and_delete_children(parent, data)

    items_added_or_removed |= _removed_items

    for d in data:
        new_child_flag = False

        if not d.get("item_code"):
            # ignore empty rows
            continue

        if not d.get("docname"):
            new_child_flag = True
            items_added_or_removed = True
            check_doc_permissions(parent, "create")
            child_item = get_new_child_item(d)
        else:
            check_doc_permissions(parent, "write")
            child_item = frappe.get_doc(parent_doctype + " Item", d.get("docname"))

            prev_rate, new_rate = flt(child_item.get("rate")), flt(d.get("rate"))
            prev_qty, new_qty = flt(child_item.get("qty")), flt(d.get("qty"))
            prev_fg_qty, new_fg_qty = flt(child_item.get("fg_item_qty")), flt(d.get("fg_item_qty"))
            prev_con_fac, new_con_fac = (
                flt(child_item.get("conversion_factor")),
                flt(d.get("conversion_factor")),
            )
            prev_uom, new_uom = child_item.get("uom"), d.get("uom")

            if parent_doctype == "Sales Order":
                prev_date, new_date = child_item.get("delivery_date"), d.get("delivery_date")
            elif parent_doctype == "Purchase Order":
                prev_date, new_date = child_item.get("schedule_date"), d.get("schedule_date")

            rate_unchanged = prev_rate == new_rate
            qty_unchanged = prev_qty == new_qty
            fg_qty_unchanged = prev_fg_qty == new_fg_qty
            uom_unchanged = prev_uom == new_uom
            conversion_factor_unchanged = prev_con_fac == new_con_fac
            any_conversion_factor_changed |= not conversion_factor_unchanged
            date_unchanged = (
                (prev_date == getdate(new_date) if prev_date and new_date else False)
                if parent_doctype not in ["Quotation", "Supplier Quotation"]
                else None
            )  # in case of delivery note etc
            prev_pieces = flt(child_item.get("pieces"))
            new_pieces = flt(d.get("pieces"))

            prev_length = flt(child_item.get("length_size"))
            new_length = flt(d.get("length_size"))

            pieces_unchanged = prev_pieces == new_pieces
            length_unchanged = prev_length == new_length
            if (
                rate_unchanged
                and qty_unchanged
                and fg_qty_unchanged
                and conversion_factor_unchanged
                and uom_unchanged
                and date_unchanged
                and pieces_unchanged
                and length_unchanged
            ):
                continue

            # Stock-impacting changes on an existing SO line → cancel that line's
            # SREs only (never wipe the whole SO). Length/pieces/rate/date alone
            # must not recreate reservations (picks wrong batches via length window).
            # Qty increase keeps existing SREs; only decrease / WH / UOM force cancel.
            if parent_doctype == "Sales Order" and d.get("docname"):
                prev_warehouse = child_item.get("warehouse")
                new_warehouse = d.get("warehouse") or prev_warehouse
                warehouse_changed = prev_warehouse != new_warehouse
                qty_decreased = new_qty < prev_qty
                if (
                    qty_decreased
                    or not conversion_factor_unchanged
                    or not uom_unchanged
                    or warehouse_changed
                ):
                    so_lines_needing_sre_cancel.add(d.get("docname"))

        validate_quantity_and_rate(child_item, d)

        if flt(child_item.get("qty")) != flt(d.get("qty")):
            any_qty_changed = True

        if (
            parent.doctype == "Purchase Order"
            and parent.is_subcontracted
            and not parent.is_old_subcontracting_flow
        ):
            validate_fg_item_for_subcontracting(d, new_child_flag)
            child_item.fg_item_qty = flt(d["fg_item_qty"])

            if new_child_flag:
                child_item.fg_item = d["fg_item"]

        child_item.qty = flt(d.get("qty"))

        if hasattr(child_item, "pieces"):
            child_item.pieces = flt(d.get("pieces"))

        if hasattr(child_item, "length_size"):
            child_item.length_size = flt(d.get("length_size"))
        rate_precision = child_item.precision("rate") or 2
        conv_fac_precision = child_item.precision("conversion_factor") or 2
        qty_precision = child_item.precision("qty") or 2

        prev_rate, new_rate = flt(child_item.get("rate")), flt(d.get("rate"))
        rate_unchanged = prev_rate == new_rate
        if not rate_unchanged and not child_item.get("qty") and is_allowed_zero_qty():
            frappe.throw(_("Rate of '{}' items cannot be changed").format(frappe.bold(_("Unit Price"))))
        # Amount cannot be lesser than billed amount, except for negative amounts
        row_rate = flt(d.get("rate"), rate_precision)

        if parent_doctype in ["Purchase Order", "Sales Order"]:
            amount_below_billed_amt = flt(child_item.billed_amt, rate_precision) > flt(
                row_rate * flt(d.get("qty"), qty_precision), rate_precision
            )
            if amount_below_billed_amt and row_rate > 0.0:
                frappe.throw(
                    _(
                        "Row #{0}: Cannot set Rate if the billed amount is greater than the amount for Item {1}."
                    ).format(child_item.idx, child_item.item_code)
                )
            else:
                child_item.rate = row_rate
        else:
            child_item.rate = row_rate

        if d.get("conversion_factor"):
            if child_item.stock_uom == child_item.uom:
                child_item.conversion_factor = 1
            else:
                child_item.conversion_factor = flt(d.get("conversion_factor"), conv_fac_precision)

        if d.get("uom"):
            child_item.uom = d.get("uom")
            conversion_factor = flt(
                get_conversion_factor(child_item.item_code, child_item.uom).get("conversion_factor")
            )
            child_item.conversion_factor = (
                flt(d.get("conversion_factor"), conv_fac_precision) or conversion_factor
            )

        if child_item.get("total_weight") and child_item.get("weight_per_unit"):
            child_item.total_weight = flt(
                child_item.weight_per_unit * child_item.qty * child_item.conversion_factor,
                child_item.precision("total_weight"),
            )

        if d.get("delivery_date") and parent_doctype == "Sales Order":
            child_item.delivery_date = d.get("delivery_date")

        if d.get("schedule_date") and parent_doctype == "Purchase Order":
            child_item.schedule_date = d.get("schedule_date")

        if d.get("bom_no") and parent_doctype == "Sales Order":
            child_item.bom_no = d.get("bom_no")

        # ===================================================================
        # NEW CODE: Fetch is_manufacture from Item master and set cost_center/branch from parent
        # ===================================================================
        if parent_doctype == "Sales Order":
            # Fetch is_manufacture from Item master if item_code exists
            if child_item.item_code:
                is_manufacture = frappe.db.get_value("Item", child_item.item_code, "is_manufacture")
                if is_manufacture is not None:
                    child_item.is_manufacture = cint(is_manufacture)

            # Set cost_center and branch from parent Sales Order if available
            if parent.get("cost_center"):
                child_item.cost_center = parent.cost_center
            if parent.get("branch"):
                child_item.branch = parent.branch
        # ===================================================================

        if parent_doctype in ["Sales Order", "Purchase Order"]:
            if flt(child_item.price_list_rate):
                if flt(child_item.rate) > flt(child_item.price_list_rate):
                    #  if rate is greater than price_list_rate, set margin
                    #  or set discount
                    child_item.discount_percentage = 0
                    child_item.margin_type = "Amount"
                    child_item.margin_rate_or_amount = flt(
                        child_item.rate - child_item.price_list_rate,
                        child_item.precision("margin_rate_or_amount"),
                    )
                    child_item.rate_with_margin = child_item.rate
                else:
                    child_item.discount_percentage = flt(
                        (1 - flt(child_item.rate) / flt(child_item.price_list_rate)) * 100.0,
                        child_item.precision("discount_percentage"),
                    )
                    child_item.discount_amount = flt(child_item.price_list_rate) - flt(child_item.rate)
                    child_item.margin_type = ""
                    child_item.margin_rate_or_amount = 0
                    child_item.rate_with_margin = 0

        child_item.flags.ignore_validate_update_after_submit = True
        if new_child_flag:
            parent.load_from_db()
            child_item.idx = len(parent.items) + 1
            child_item.insert()
        else:
            child_item.save(ignore_permissions=True)

    parent.reload()
    parent.flags.ignore_validate_update_after_submit = True
    parent.set_qty_as_per_stock_uom()
    parent.calculate_taxes_and_totals()
    parent.set_total_in_words()
    if parent_doctype == "Sales Order":
        make_packing_list(parent)
        parent.set_gross_profit()
    frappe.get_doc("Authorization Control").validate_approving_authority(
        parent.doctype, parent.company, parent.base_grand_total
    )

    if parent_doctype != "Supplier Quotation":
        parent.set_payment_schedule()
    if parent_doctype == "Purchase Order":
        parent.set_tax_withholding()
        parent.validate_minimum_order_qty()
        parent.validate_budget()
        if parent.is_against_so():
            parent.update_status_updater()
    elif parent_doctype == "Sales Order":
        parent.check_credit_limit()

    # reset index of child table
    for idx, row in enumerate(parent.get(child_docname), start=1):
        row.idx = idx

    parent.save()

    if parent_doctype == "Purchase Order":
        update_last_purchase_rate(parent, is_submit=1)

        if any_qty_changed or items_added_or_removed or any_conversion_factor_changed:
            parent.update_prevdoc_status()

        parent.update_requested_qty()
        parent.update_ordered_qty()
        parent.update_ordered_and_reserved_qty()
        parent.update_receiving_percentage()

        if parent.is_subcontracted:
            if parent.is_old_subcontracting_flow:
                if should_update_supplied_items(parent):
                    parent.update_reserved_qty_for_subcontract()
                    parent.create_raw_materials_supplied()
                parent.save()
            else:
                if not parent.can_update_items():
                    frappe.throw(
                        _(
                            "Items cannot be updated as Subcontracting Order is created against the Purchase Order {0}."
                        ).format(frappe.bold(parent.name))
                    )
    elif parent_doctype == "Sales Order":
        parent.validate_selling_price()
        parent.validate_for_duplicate_items()
        parent.validate_warehouse()
        parent.update_reserved_qty()
        parent.update_project()
        parent.update_prevdoc_status("submit")
        parent.update_delivery_status()

    parent.reload()
    validate_workflow_conditions(parent)

    if parent_doctype in ["Purchase Order", "Sales Order"]:
        parent.update_blanket_order()
        parent.update_billing_percentage()
        parent.set_status()

    parent.validate_uom_is_integer("uom", "qty")
    parent.validate_uom_is_integer("stock_uom", "stock_qty")

    # Madhav: do NOT cancel-all + auto-recreate SREs on every SO Update Items.
    # That wiped BWRT / Finish WO / length-window batch picks and re-reserved
    # different batches even when only rate / date / length / pieces changed.
    # Only cancel SREs for removed lines or stock-impacting line edits.
    # User re-reserves manually via Stock Reservation / Batch Wise Reservation.
    if parent_doctype == "Sales Order":
        _cancel_so_line_stock_reservations(
            parent.name,
            set(removed_so_item_names) | so_lines_needing_sre_cancel,
        )


def _cancel_so_line_stock_reservations(sales_order, so_item_names):
    """Cancel submitted SREs linked to specific Sales Order Item rows only."""
    if not so_item_names:
        return

    from erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry import (
        cancel_stock_reservation_entries,
    )

    for so_detail in so_item_names:
        if not so_detail:
            continue
        cancel_stock_reservation_entries(
            voucher_type="Sales Order",
            voucher_no=sales_order,
            voucher_detail_no=so_detail,
            notify=False,
        )
