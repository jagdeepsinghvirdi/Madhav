import frappe
from frappe import _
from frappe.utils import flt, cint
from erpnext.manufacturing.doctype.work_order.work_order import WorkOrder as ERPNextWorkOrder

def line_work_order_qty(sales_order_item):
	"""Sales Order Item.work_order_qty by the planning rule.

	Sum of this line's submitted Work Orders (a Closed one counts only what
	it produced), leaving out sub-assembly Work Orders. Returns None for a
	product-bundle line, which keeps ERPNext's own bundle arithmetic.
	"""
	if not sales_order_item:
		return None
	item_code = frappe.db.get_value("Sales Order Item", sales_order_item, "item_code")
	if not item_code:
		return None
	if frappe.db.exists(
		"Work Order",
		{"sales_order_item": sales_order_item, "docstatus": 1, "product_bundle_item": ["is", "set"]},
	):
		return None
	qty = frappe.db.sql(
		"""
		select ifnull(sum(case when status = 'Closed' then least(produced_qty, qty) else qty end), 0)
		from `tabWork Order`
		where sales_order_item = %s and docstatus = 1 and production_item = %s
			and ifnull(production_plan_sub_assembly_item, '') = ''
		""",
		(sales_order_item, item_code),
	)[0][0]
	return flt(qty, 3)


def recount_line_work_order_qty(sales_order_item):
	"""Write the planning-rule work_order_qty (and pieces) onto one SO line.

	Call after anything changes a Work Order's qty without going through
	submit / cancel / close (those already recount): e.g. Finish Work Order
	raising the qty to the mill weight. Without it the line kept the old
	figure, so Production Plan offered too much or too little.
	"""
	qty = line_work_order_qty(sales_order_item)
	if qty is None:
		return
	frappe.db.set_value("Sales Order Item", sales_order_item, "work_order_qty", qty, update_modified=False)
	from madhav.doc_events.work_order import recompute_work_order_pieces

	recompute_work_order_pieces(sales_order_item)


class WorkOrder(ERPNextWorkOrder):
    
    def update_work_order_qty_in_so(self):
        if (not self.sales_order and not self.sales_order_item) or self.production_plan_sub_assembly_item:
            return

        total_bundle_qty = 1
        if self.product_bundle_item:
            total_bundle_qty = frappe.db.sql(
                """ select sum(qty) from
                `tabProduct Bundle Item` where parent = %s""",
                (frappe.db.escape(self.product_bundle_item)),
            )[0][0]

            if not total_bundle_qty:
                # product bundle is 0 (product bundle allows 0 qty for items)
                total_bundle_qty = 1

        cond = "product_bundle_item = %s" if self.product_bundle_item else "production_item = %s"

        # A closed Work Order still made what it produced, so that part keeps
        # counting; only the unmade balance drops out. (Standard ERPNext drops
        # the whole closed Work Order, which made the Sales Order look as if
        # the produced stock had never been made.)
        qty = frappe.db.sql(
            f""" select sum(case when status = 'Closed' then least(produced_qty, qty) else qty end) from
            `tabWork Order` where sales_order = %s and sales_order_item = %s and docstatus = 1 and {cond}
            """,
            (self.sales_order,self.sales_order_item, (self.product_bundle_item or self.production_item)),
            as_list=1,
        )

        work_order_qty = qty[0][0] if qty and qty[0][0] else 0
        frappe.db.set_value(
            "Sales Order Item",
            self.sales_order_item,
            "work_order_qty",
            flt(work_order_qty / total_bundle_qty, 3),
        )

    def on_close_or_cancel(self):
        super().on_close_or_cancel()
        # Close gives back only the pieces never made; recount from the Work
        # Orders so repeated closes or a later cancel can't drift.
        if self.status == "Closed" and self.sales_order_item:
            from madhav.doc_events.work_order import recompute_work_order_pieces

            recompute_work_order_pieces(self.sales_order_item)

    def on_cancel(self):
        super().on_cancel()
        # Remember how much of the order line was planned when this Work Order
        # gave its quantity back (see validate_released_plan_qty).
        if self.sales_order_item and self.meta.has_field("planned_qty_at_cancel"):
            self.db_set(
                "planned_qty_at_cancel",
                flt(frappe.db.get_value("Sales Order Item", self.sales_order_item, "production_plan_qty")),
                update_modified=False,
            )

    def validate_qty(self):
        super().validate_qty()
        self.validate_released_plan_qty()

    def validate_released_plan_qty(self):
        """Don't plan the same quantity twice.

        A cancelled Work Order gives its quantity back to the Sales Order line,
        and a new Production Plan can pick it up. Once a newer Production Plan
        has planned that line, the old plan row must not make a Work Order for
        the released quantity as well. Until then, re-making it from the old
        row (Cancel & Amend, splitting a Work Order) is fine: the release then
        simply shrinks again.

        Runs on save and on submit (Production Plan "Make Work Order" inserts
        drafts without validation, so submit is where it is caught).
        """
        if (
            self.docstatus == 2
            or self.get("_action") == "update_after_submit"
            or not self.production_plan_item
            or self.production_plan_sub_assembly_item
            or not self.sales_order_item
            or self.flags.get("party_transfer_replacement")
        ):
            return
        from madhav.madhav.utils.production_demand import get_plan_row_cancel_release

        released = get_plan_row_cancel_release(self.production_plan_item, exclude_work_order=self.name)
        if released.qty <= 0.0005:
            return
        # Was the order line planned again since the cancel? Compare its planned
        # qty now with what it was when the Work Order was cancelled (recorded on
        # the cancelled Work Order); this also catches a draft plan that was
        # created earlier and submitted after the cancel.
        snapshot = None
        if frappe.get_meta("Work Order").has_field("planned_qty_at_cancel"):
            snapshot = frappe.db.sql(
                """select max(ifnull(planned_qty_at_cancel, 0)) from `tabWork Order`
                where production_plan_item = %s and docstatus = 2 and name != %s""",
                (self.production_plan_item, self.name or ""),
            )[0][0]
        line_planned = flt(frappe.db.get_value("Sales Order Item", self.sales_order_item, "production_plan_qty"))
        if flt(snapshot) > 0:
            if line_planned <= flt(snapshot) + 0.0005:
                return
        else:
            # Cancelled before the snapshot existed: fall back to plan creation time.
            last_cancel = frappe.db.sql(
                """select max(modified) from `tabWork Order`
                where production_plan_item = %s and docstatus = 2 and name != %s""",
                (self.production_plan_item, self.name or ""),
            )[0][0]
            if not frappe.db.sql(
                """select pp.name from `tabProduction Plan Item` ppi
                inner join `tabProduction Plan` pp on pp.name = ppi.parent
                where ppi.sales_order_item = %s and pp.docstatus = 1 and pp.name != %s and pp.modified > %s
                limit 1""",
                (self.sales_order_item, self.production_plan, last_cancel),
            ):
                return
        replanned_by = frappe.db.sql(
            """select pp.name from `tabProduction Plan Item` ppi
            inner join `tabProduction Plan` pp on pp.name = ppi.parent
            where ppi.sales_order_item = %s and pp.docstatus = 1 and pp.name != %s
            order by pp.modified desc limit 1""",
            (self.sales_order_item, self.production_plan),
        ) or [[_("another Production Plan")]]
        planned = flt(frappe.db.get_value("Production Plan Item", self.production_plan_item, "planned_qty"))
        ordered = flt(
            frappe.db.sql(
                """select ifnull(sum(qty), 0) from `tabWork Order`
                where production_plan_item = %s and docstatus = 1 and name != %s""",
                (self.production_plan_item, self.name or ""),
            )[0][0]
        )
        allowance = flt(frappe.db.get_single_value("Manufacturing Settings", "overproduction_percentage_for_work_order"))
        open_qty = max(0.0, planned - ordered - released.qty)
        max_qty = flt(open_qty * (1 + allowance / 100), 3)
        if flt(self.qty) > max_qty + 0.0005:
            frappe.throw(
                frappe._(
                    "{0} of Production Plan {1} was given back to Sales Order {2} when its Work Order was "
                    "cancelled, and Production Plan {3} has planned that order line since. "
                    "Making it here as well would plan it twice. This plan row can still make {4}."
                ).format(
                    flt(released.qty, 3), self.production_plan, self.sales_order, replanned_by[0][0], max_qty
                ),
                title=frappe._("Quantity already planned again"),
            )

@frappe.whitelist()
def make_stock_entry(work_order_id, purpose, qty=None, target_warehouse=None):
    work_order = frappe.get_doc("Work Order", work_order_id)
    if not frappe.db.get_value("Warehouse", work_order.wip_warehouse, "is_group"):
        wip_warehouse = work_order.wip_warehouse
    else:
        wip_warehouse = None
 
    stock_entry = frappe.new_doc("Stock Entry")
    stock_entry.purpose = purpose
    stock_entry.work_order = work_order_id
    stock_entry.company = work_order.company
    stock_entry.from_bom = 1
    stock_entry.bom_no = work_order.bom_no
    stock_entry.use_multi_level_bom = work_order.use_multi_level_bom
    # accept 0 qty as well
    stock_entry.fg_completed_qty = (
        qty if qty is not None else (flt(work_order.qty) - flt(work_order.produced_qty))
    )
 
    if work_order.bom_no:
        stock_entry.inspection_required = frappe.db.get_value("BOM", work_order.bom_no, "inspection_required")
 
    if purpose == "Material Transfer for Manufacture":
        stock_entry.to_warehouse = wip_warehouse
        if hasattr(work_order, 'project'):
            stock_entry.project = work_order.project
    else:
        stock_entry.from_warehouse = (
            work_order.source_warehouse
            if work_order.skip_transfer and not work_order.from_wip_warehouse
            else wip_warehouse
        )
        stock_entry.to_warehouse = work_order.fg_warehouse
        if hasattr(work_order, 'project'):
            stock_entry.project = work_order.project
 
    if purpose == "Disassemble":
        stock_entry.from_warehouse = work_order.fg_warehouse
        stock_entry.to_warehouse = target_warehouse or work_order.source_warehouse
 
    stock_entry.set_stock_entry_type()
    
    # Modified part: Handle Manufacture purpose differently to avoid consolidation
    if purpose == "Manufacture":
        # Get items from Material Transfer entries instead of BOM to avoid consolidation
        get_items_from_material_transfer(stock_entry, work_order_id, qty)
        # Add finished goods
        add_finished_goods_to_stock_entry(stock_entry, work_order, qty)
        
        # Try to set serial/batch numbers safely
        try:
            if hasattr(stock_entry, 'set_serial_no_batch_for_finished_good'):
                stock_entry.set_serial_no_batch_for_finished_good()
        except AttributeError as e:
            frappe.log_error(f"Error setting serial/batch numbers: {str(e)}")
            pass
    else:
        # For other purposes, use the original method
        stock_entry.get_items()
        
        # Only call this for non-Disassemble purposes
        if purpose != "Disassemble":
            try:
                if hasattr(stock_entry, 'set_serial_no_batch_for_finished_good'):
                    stock_entry.set_serial_no_batch_for_finished_good()
            except AttributeError as e:
                frappe.log_error(f"Error setting serial/batch numbers: {str(e)}")
                pass
 
    return stock_entry.as_dict()


def get_items_from_material_transfer(stock_entry, work_order_id, qty=None):
    """Get items from Material Transfer entries without consolidation"""
    
    # Get all Material Transfer entries for this work order
    material_transfers = frappe.get_all("Stock Entry", 
        filters={
            "work_order": work_order_id,
            "purpose": "Material Transfer for Manufacture",
            "docstatus": 1
        },
        order_by="creation"
    )
    
    for transfer_name in material_transfers:
        transfer_doc = frappe.get_doc("Stock Entry", transfer_name.name)
        
        # Add each item from transfer as separate row (no consolidation)
        for item in transfer_doc.items:
            if item.t_warehouse:  # Only items that were transferred to production
                # Calculate proportional qty if partial manufacture
                actual_qty = item.qty
                if qty and stock_entry.fg_completed_qty:
                    # Get total fg qty from work order
                    work_order_doc = frappe.get_doc("Work Order", work_order_id)
                    total_fg_qty = work_order_doc.qty
                    if total_fg_qty and total_fg_qty > 0:
                        proportion = flt(stock_entry.fg_completed_qty) / flt(total_fg_qty)
                        actual_qty = flt(item.qty) * proportion
                
                # Create a minimal item dict to avoid attribute errors
                item_dict = {
                    "item_code": item.item_code,
                    "qty": actual_qty,
                    "s_warehouse": item.t_warehouse,  # From WIP warehouse
                    "is_finished_item": 0,
                    "is_scrap_item": 0,
                }
                
                # Add essential fields
                if hasattr(item, 'uom') and item.uom:
                    item_dict["uom"] = item.uom
                if hasattr(item, 'stock_uom') and item.stock_uom:
                    item_dict["stock_uom"] = item.stock_uom
                if hasattr(item, 'conversion_factor'):
                    item_dict["conversion_factor"] = item.conversion_factor or 1
                else:
                    item_dict["conversion_factor"] = 1
                    
                # Add other safe fields
                safe_fields = ['item_name', 'description', 'basic_rate', 'expense_account', 'batch_no', 'serial_no']
                for field in safe_fields:
                    if hasattr(item, field) and getattr(item, field):
                        item_dict[field] = getattr(item, field)
                
                # Calculate basic_amount if basic_rate exists
                if 'basic_rate' in item_dict:
                    item_dict["basic_amount"] = flt(actual_qty) * flt(item_dict["basic_rate"])
                
                # Propagate custom fields for pieces/length/section weight if present
                custom_fields = [
                    ('pieces', 'pieces'),
                    ('average_length', 'average_length'),
                    ('section_weight', 'section_weight'),
                    ('lot_no', 'lot_no'),
                    ('total_pcs', 'total_pcs'),
                ]
                for src, dest in custom_fields:
                    if hasattr(item, src) and getattr(item, src) is not None:
                        item_dict[dest] = getattr(item, src)
                
                stock_entry.append("items", item_dict)


def add_finished_goods_to_stock_entry(stock_entry, work_order, qty=None):
    """Add finished goods to stock entry"""
    
    fg_qty = qty if qty is not None else stock_entry.fg_completed_qty
    
    # Get finished item details
    finished_item = work_order.production_item
    
    try:
        # Get basic item info from database to avoid loading full doc
        item_info = frappe.db.get_value("Item", finished_item, 
            ["item_name", "description", "stock_uom", "item_group"], as_dict=1)
        
        if item_info:
            finished_item_dict = {
                "item_code": finished_item,
                "qty": fg_qty,
                "t_warehouse": work_order.fg_warehouse,
                "is_finished_item": 1,
                "is_scrap_item": 0,
                "uom": item_info.stock_uom,
                "stock_uom": item_info.stock_uom,
                "conversion_factor": 1,
            }
            
            # Add optional fields safely
            if item_info.item_name:
                finished_item_dict["item_name"] = item_info.item_name
            if item_info.description:
                finished_item_dict["description"] = item_info.description
            if item_info.item_group:
                finished_item_dict["item_group"] = item_info.item_group
            
            stock_entry.append("items", finished_item_dict)
            
    except Exception as e:
        frappe.log_error(f"Error adding finished goods: {str(e)}")
        # Add minimal finished goods entry as fallback
        stock_entry.append("items", {
            "item_code": finished_item,
            "qty": fg_qty,
            "t_warehouse": work_order.fg_warehouse,
            "is_finished_item": 1,
            "is_scrap_item": 0,
            "conversion_factor": 1,
        })
 


