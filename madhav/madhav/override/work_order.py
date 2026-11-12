import frappe
from frappe.utils import flt, cint
from erpnext.manufacturing.doctype.work_order.work_order import WorkOrder as ERPNextWorkOrder

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
        # Cancel already gives all the pieces back (revert_so_pieces_from_work_order).
        # Close gives back only the pieces that were never made.
        if self.status == "Closed" and self.sales_order_item and self.get("production_plan"):
            unmade = max(0, flt(self.get("pieces")) - flt(self.get("completed_pcs")))
            if unmade:
                current = flt(frappe.db.get_value("Sales Order Item", self.sales_order_item, "work_order_pieces"))
                frappe.db.set_value(
                    "Sales Order Item",
                    self.sales_order_item,
                    "work_order_pieces",
                    max(0, current - unmade),
                    update_modified=False,
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
 


