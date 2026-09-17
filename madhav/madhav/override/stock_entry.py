import frappe
from frappe import _, bold
from erpnext.stock.doctype.stock_entry.stock_entry import StockEntry as _StockEntry
from frappe.utils import (
	cint,
	comma_or,
	cstr,
	flt,
	format_time,
	formatdate,
	get_link_to_form,
	getdate,
	nowdate,
)
class CustomStockEntry(_StockEntry):
    def validate(self):
        # Finish Work Order builds Manufacture SE manually (from_bom=0).
        # Drop any zero-qty rows before core validate_qty_is_not_zero —
        # demo sites with "Material Transferred for Manufacture" backflush
        # can otherwise surface "Quantity for Item RMxxxx cannot be zero"
        # even though FWO already posted the real consume qty.
        self._drop_zero_qty_rows_for_fwo_manufacture()
        self._restore_fg_completed_qty_for_manual_manufacture()
        super().validate()
        self._restore_fg_completed_qty_for_manual_manufacture()

    def _drop_zero_qty_rows_for_fwo_manufacture(self):
        if not self.flags.get("madhav_fwo_manufacture"):
            return
        if self.purpose != "Manufacture":
            return
        for row in list(self.get("items") or []):
            if flt(row.qty) <= 0:
                self.remove(row)

    def _restore_fg_completed_qty_for_manual_manufacture(self):
        # FWO uses from_bom=0; core clears fg_completed_qty in that case.
        # Also restore when flag is set even if from_bom was flipped.
        if self.purpose != "Manufacture":
            return
        if cint(self.from_bom) and not self.flags.get("madhav_fwo_manufacture"):
            return
        fg_qty = sum(
            flt(d.qty) for d in (self.get("items") or []) if cint(d.is_finished_item)
        )
        if fg_qty:
            self.fg_completed_qty = fg_qty

    def validate_work_order(self):
        if self.purpose in (
            "Manufacture",
            "Material Transfer for Manufacture",
            "Material Consumption for Manufacture",
            "Disassemble",
        ):
            # check if work order is entered

            if (
                self.purpose == "Material Consumption for Manufacture"
            ) and self.work_order:
                if not self.fg_completed_qty:
                    frappe.throw(_("For Quantity (Manufactured Qty) is mandatory Custom"))
                self.check_if_operations_completed()
                self.check_duplicate_entry_for_work_order()
        elif self.purpose not in ["Material Transfer","Manufacture"]:
            self.work_order = None
            
    def validate_component_and_quantities(self):
        if self.purpose not in ["Manufacture", "Material Transfer for Manufacture"]:
            return

        if not frappe.db.get_single_value("Manufacturing Settings", "validate_components_quantities_per_bom"):
            return

        if not self.fg_completed_qty:
            return

        raw_materials = self.get_bom_raw_materials(self.fg_completed_qty)

        precision = frappe.get_precision("Stock Entry Detail", "qty")
        for item_code, details in raw_materials.items():
            if matched_item := self.get_matched_items(item_code):
                if flt(details.get("qty"), precision) != flt(matched_item.qty, precision):
                    frappe.throw(
                        _("For the item {0}, the quantity should be {1} according to the BOM {2}.").format(
                            frappe.bold(item_code),
                            flt(details.get("qty")),
                            get_link_to_form("BOM", self.bom_no),
                        ),
                        title=_("Incorrect Component Quantity"),
                    )
            else:
                frappe.throw(
                    _("According to the BOM {0}, the Item '{1}' is missing in the stock entry.").format(
                        get_link_to_form("BOM", self.bom_no), frappe.bold(item_code)
                    ),
                    title=_("Missing Item"),
                )