import frappe
from frappe.utils import flt


def validate(self,method):
    self.db_set("pending_pcs",flt(self.pieces) - flt(self.completed_pcs))
    self.db_set("pending_qty",flt(self.qty) - flt(self.produced_qty))

def recompute_work_order_pieces(sales_order_item):
    """Sales Order Item.work_order_pieces = pieces of its submitted Work
    Orders, counting a Closed one only for the pieces it actually made.

    Recounting (instead of adding on submit and subtracting on cancel) keeps
    the figure right after closes, repeated closes and cancels of closed
    Work Orders."""
    if not sales_order_item:
        return
    total = frappe.db.sql(
        """
        select ifnull(sum(case when status = 'Closed' then least(ifnull(completed_pcs, 0), ifnull(pieces, 0))
            else ifnull(pieces, 0) end), 0)
        from `tabWork Order`
        where sales_order_item = %s and docstatus = 1
            and ifnull(production_plan_sub_assembly_item, '') = ''
        """,
        sales_order_item,
    )[0][0]
    frappe.db.set_value(
        "Sales Order Item", sales_order_item, "work_order_pieces", max(0, flt(total)), update_modified=False
    )


def update_so_pieces_from_work_order(doc, method=None):
    recompute_work_order_pieces(doc.sales_order_item)


def revert_so_pieces_from_work_order(doc, method=None):
    recompute_work_order_pieces(doc.sales_order_item)
