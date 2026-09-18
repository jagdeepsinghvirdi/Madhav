# Copyright (c) 2026, Finbyz pvt. ltd. and contributors
# For license information, please see license.txt

"""Delivery Note monkey patches.

Same item can be reserved in multiple warehouses (e.g. Finished Goods from
one Stock Entry + For Mill EXTRA from another). Core
``validate_against_stock_reservation_entries`` only checks that the DN row
warehouse is *somewhere* in the SO-line reserved list — but
``super().validate()`` / ``set_missing_values`` often restamps every row
with the Sales Order's nominal warehouse, and before_validate sync alone
can lose the per-SRE warehouse.

Deliver-as-Qty overage also cancels the row's SRE in ``before_submit``
before stock reconciliation re-validates. Active reserved warehouses may
then only list a sibling SRE (other WH, same item). Core would throw
mismatch or (with naive sync) flip the bundle onto the sibling WH.

Fix: re-sync + pin from ``custom_sre`` (active only — cancelled links cannot
be saved), and allow the warehouse of an SRE (active or Cancelled) that
still matches the row's batch on this SO line.
"""

import frappe
from frappe import _
from erpnext.stock.doctype.delivery_note.delivery_note import DeliveryNote
from erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry import (
	get_sre_reserved_warehouses_for_voucher,
)

# Kept for tests / callers that patch the previous core delegate.
_original_validate_against_stock_reservation_entries = (
	DeliveryNote.validate_against_stock_reservation_entries
)


def _force_warehouse_from_custom_sre(doc):
	"""Pin each DN row to its linked SRE warehouse (FG vs Mill EXTRA)."""
	from madhav.doc_events.delivery_note import (
		_align_bundle_warehouse,
		_warehouse_from_custom_sre,
	)

	for item in doc.get("items") or []:
		sre_wh = _warehouse_from_custom_sre(item)
		if not sre_wh:
			continue
		if item.warehouse != sre_wh:
			item.warehouse = sre_wh
			if item.serial_and_batch_bundle:
				_align_bundle_warehouse(item.serial_and_batch_bundle, sre_wh)


def _allowed_warehouses_for_dn_item(item):
	"""Active SRE warehouses + warehouse of the SRE that holds this row's batch.

	Cancelled Mill EXTRA SREs cannot be stored in ``custom_sre`` (Link rejects
	cancelled docs). Look up by batch on the SO line instead so validation
	still allows the physical warehouse after Deliver-as-Qty cancel.
	"""
	from madhav.doc_events.delivery_note import (
		_sre_warehouse_for_batch_including_cancelled,
		_warehouse_from_custom_sre,
	)

	allowed = list(
		get_sre_reserved_warehouses_for_voucher(
			"Sales Order", item.against_sales_order, item.so_detail
		)
		or []
	)
	sre_wh = _warehouse_from_custom_sre(item)
	if sre_wh and sre_wh not in allowed:
		allowed.append(sre_wh)

	if item.batch_no:
		batch_wh = _sre_warehouse_for_batch_including_cancelled(
			item.against_sales_order, item.so_detail, item.batch_no
		)
		if batch_wh and batch_wh not in allowed:
			allowed.append(batch_wh)

	return allowed


def validate_against_stock_reservation_entries(self):
	from madhav.doc_events.delivery_note import _sync_dn_item_warehouse_to_reservation

	_sync_dn_item_warehouse_to_reservation(self)
	_force_warehouse_from_custom_sre(self)

	# Mirror core validation, but treat custom_sre / batch-matched SRE
	# warehouse as allowed even after Deliver-as-Qty cancels that SRE.
	if self.is_return:
		return

	for item in self.get("items"):
		if not item.against_sales_order or not item.so_detail:
			continue

		allowed = _allowed_warehouses_for_dn_item(item)
		if not allowed:
			continue

		if not item.warehouse:
			item.warehouse = allowed[0]
			continue

		if item.warehouse not in allowed:
			msg = _("Row #{0}: Stock is reserved for item {1} in warehouse {2}.").format(
				item.idx,
				frappe.bold(item.item_code),
				frappe.bold(allowed[0])
				if len(allowed) == 1
				else _("{0} and {1}").format(
					frappe.bold(", ".join(allowed[:-1])),
					frappe.bold(allowed[-1]),
				),
			)
			frappe.throw(msg, title=_("Stock Reservation Warehouse Mismatch"))
