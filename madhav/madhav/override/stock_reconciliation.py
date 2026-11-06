from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import StockReconciliation as _StockReconciliation
from frappe import bold, _
import frappe
from frappe.utils import flt


class StockReconciliation(_StockReconciliation):
    def validate_reserved_stock(self) -> None:
        reco_keys = {
            (item.item_code, item.warehouse, item.batch_no)
            for item in self.items if item.batch_no
        }
        if not reco_keys:
            return

        item_codes = list({k[0] for k in reco_keys})
        warehouses = list({k[1] for k in reco_keys})
        batch_nos = list({k[2] for k in reco_keys})

        # Sum the batch row's own open qty. The SRE header holds every
        # batch on that reservation, so summing it once per child row
        # reports the full header (for example 6.09) against a batch that
        # no longer has anything open and blocks a Delivery Note shortfall
        # reconciliation.
        sre_rows = frappe.db.sql(
            """
            SELECT
                parent.item_code,
                parent.warehouse,
                child.batch_no,
                GROUP_CONCAT(DISTINCT parent.voucher_no ORDER BY parent.voucher_no SEPARATOR ', ') AS sales_orders,
                SUM(IFNULL(child.qty, 0) - IFNULL(child.delivered_qty, 0)) AS reserved_qty
            FROM `tabStock Reservation Entry` parent
            INNER JOIN `tabSerial and Batch Entry` child
                ON child.parent = parent.name
               AND child.parenttype = 'Stock Reservation Entry'
            WHERE parent.docstatus = 1
              AND IFNULL(parent.status, '') NOT IN ('Delivered', 'Cancelled')
              AND parent.item_code IN %(item_codes)s
              AND parent.warehouse IN %(warehouses)s
              AND child.batch_no IN %(batch_nos)s
            GROUP BY parent.item_code, parent.warehouse, child.batch_no
            """,
            {
                "item_codes": item_codes,
                "warehouses": warehouses,
                "batch_nos": batch_nos,
            },
            as_dict=True,
        )

        if self._action == "cancel" and self.flags.get("madhav_dn_cancel"):
            # Delivery Note cancel reverses its own shortfall reconciliation.
            # The DN cancel has just put the delivered stock back, so taking
            # out this SR's top-up leaves the batch exactly where it was
            # before the DN. Reservations re-opened by that cancel are not a
            # reason to block - only a real shortage is.
            return self._validate_reserved_stock_for_dn_cancel(sre_rows, reco_keys)

        data = []
        for row in sre_rows:
            key = (row.item_code, row.warehouse, row.batch_no)
            reserved_qty = flt(row.reserved_qty)
            if key in reco_keys and reserved_qty > 0.001:
                data.append([
                    row.item_code,
                    row.warehouse,
                    row.batch_no,
                    reserved_qty,
                    row.sales_orders or "",
                ])

        if not data:
            return  #allow submission

        #Block only for exact batch match
        if len(data) == 1:
            d = data[0]
            msg = _(
                "{0} units are reserved for Item {1}, Batch {2} in Warehouse {3} against Sales Order {4}, please un-reserve to {5}."
            ).format(bold(d[3]), bold(d[0]), bold(d[2]), bold(d[1]), bold(d[4] or "-"), self._action)
        else:
            items_html = ""
            for d in data:
                items_html += "<li>{} units of Item {} Batch {} in Warehouse {} (Sales Order {})</li>".format(
                    bold(d[3]), bold(d[0]), bold(d[2]), bold(d[1]), bold(d[4] or "-")
                )
            msg = _(
                "Reserved stock found for following Item-Batch combinations. Un-reserve to {0}:<br><br>{1}"
            ).format(self._action, items_html)

        frappe.throw(msg, title=_("Stock Reservation"))

    def _validate_reserved_stock_for_dn_cancel(self, sre_rows, reco_keys):
        """Cancel of a Delivery Note's shortfall SR: block only a real shortage.

        Stock left in the batch after this cancel = current batch qty minus
        what this SR added (qty - current_qty on its row). The cancel may go
        ahead when that still covers every open reservation on the batch.
        """
        reserved_by_key = {
            (r.item_code, r.warehouse, r.batch_no): r for r in sre_rows
        }

        added_by_key = {}
        for item in self.items:
            if not item.batch_no:
                continue
            key = (item.item_code, item.warehouse, item.batch_no)
            added_by_key[key] = added_by_key.get(key, 0) + (
                flt(item.qty) - flt(item.current_qty)
            )

        data = []
        for key in reco_keys:
            res = reserved_by_key.get(key)
            reserved_qty = flt(res.reserved_qty) if res else 0
            if reserved_qty <= 0.001:
                continue
            item_code, warehouse, batch_no = key
            batch_qty = flt(
                frappe.db.sql(
                    """
                    select sum(sign(sle.actual_qty) * abs(sbe.qty))
                    from `tabStock Ledger Entry` sle
                    inner join `tabSerial and Batch Entry` sbe
                        on sbe.parent = sle.serial_and_batch_bundle
                    where sle.item_code = %s and sle.warehouse = %s
                      and sle.is_cancelled = 0 and sbe.batch_no = %s
                    """,
                    (item_code, warehouse, batch_no),
                )[0][0]
                or 0
            )
            left_after_cancel = batch_qty - flt(added_by_key.get(key))
            if reserved_qty > left_after_cancel + 0.001:
                data.append([
                    item_code,
                    warehouse,
                    batch_no,
                    reserved_qty,
                    left_after_cancel,
                    res.sales_orders or "",
                ])

        if not data:
            return

        items_html = "".join(
            "<li>{}</li>".format(
                _(
                    "Item {0} Batch {1} in Warehouse {2}: {3} reserved (Sales Order {4}) "
                    "but only {5} would be left after this cancel"
                ).format(
                    bold(d[0]), bold(d[2]), bold(d[1]), bold(flt(d[3], 3)),
                    bold(d[5] or "-"), bold(flt(d[4], 3)),
                )
            )
            for d in data
        )
        frappe.throw(
            _(
                "This batch stock is reserved and would fall short after cancelling. "
                "Un-reserve the extra qty, then cancel:<br><br>{0}"
            ).format("<ul>" + items_html + "</ul>"),
            title=_("Stock Reservation"),
        )
