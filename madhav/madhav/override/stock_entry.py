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
        # FWO Manufacture SE only: drop stray zero-qty rows before core
        # validate_qty_is_not_zero (P4: "Quantity for Item RMxxxx cannot be zero").
        # Normal Manufacture / Transfer / Issue SE paths are unchanged.
        if self.flags.get("madhav_fwo_manufacture"):
            from madhav.madhav.doctype.finish_work_order.finish_work_order import (
                _sanitize_fwo_manufacture_stock_entry,
            )
            self._reapply_fwo_row_qty(stage="before core validate")
            _sanitize_fwo_manufacture_stock_entry(self, self.work_order or self.name)

        # from_bom=0 only: core clears fg_completed_qty during validate.
        self._restore_fg_completed_qty_for_manual_manufacture()
        super().validate()
        self._restore_fg_completed_qty_for_manual_manufacture()

        if self.flags.get("madhav_fwo_manufacture") and self._reapply_fwo_row_qty(
            stage="after core validate"
        ):
            # Keep derived values in step with the restored quantities.
            self.set_transfer_qty()
            self.calculate_rate_and_amount()

    def _reapply_fwo_row_qty(self, stage):
        """Put back the consume / scrap / FG quantities Finish Work Order built.

        Returns True when any row had been changed. Matches rows by position and
        item code; if the row layout no longer matches, nothing is touched.
        """
        intended = self.flags.get("madhav_fwo_row_qty")
        rows = self.get("items") or []
        if not intended or len(intended) != len(rows):
            return False
        if any(r.item_code != item for r, (item, _qty) in zip(rows, intended)):
            return False

        changed = []
        for r, (item, qty) in zip(rows, intended):
            if qty > 0 and abs(flt(r.qty) - qty) > 1e-9:
                changed.append({"item_code": item, "was": flt(r.qty), "restored": qty})
                r.qty = qty

        if changed:
            frappe.log_error(
                title="FWO Stock Entry qty restored",
                message=frappe.as_json(
                    {"work_order": self.work_order, "stage": stage, "rows": changed},
                    indent=2,
                ),
            )
        return bool(changed)

    def _restore_fg_completed_qty_for_manual_manufacture(self):
        if self.purpose != "Manufacture" or cint(self.from_bom):
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
