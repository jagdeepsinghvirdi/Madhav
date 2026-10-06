"""Use a batch's already-posted inward rate when a backdated issue cannot see it.

ERPNext values an outgoing batch only from Serial and Batch Bundles posted
before the voucher's posting time. Manufacture entries here are often dated
before the Purchase Invoice that received the same batch, so that lookup finds
no quantity and stores valuation rate 0 on the Stock Entry, the bundle, and
the ledger. The purchase rate is already on the inward bundle. Apply it only
for batches the normal calculation left unvalued.
"""

import frappe
from frappe.utils import flt

from erpnext.stock.serial_batch_bundle import BatchNoValuation


def fill_unvalued_outgoing_batches(valuation, source_rates, allow_zero=False):
	"""Copy source rates onto batches the posting-time average could not value.

	Batches that already have available quantity, or an average rate, are left
	alone. A source rate of 0 is also left alone, so a genuine zero-value
	receipt is not replaced.
	"""
	if allow_zero:
		return
	sle = getattr(valuation, "sle", None)
	if flt(getattr(sle, "actual_qty", 0)) >= 0:
		return

	batch_nos = getattr(valuation, "batch_nos", None) or {}
	if not isinstance(batch_nos, dict):
		return

	non_batchwise = set(getattr(valuation, "non_batchwise_valuation_batches", []) or [])
	available = getattr(valuation, "available_qty", {}) or {}
	avg_rate = getattr(valuation, "batch_avg_rate", None)
	if avg_rate is None:
		return

	for batch_no, ledger in batch_nos.items():
		if batch_no in non_batchwise:
			continue
		# Same condition ERPNext uses to skip a batch: no qty at posting time.
		# A negative balance is left to the normal path.
		if flt(available.get(batch_no)):
			continue
		if flt(avg_rate.get(batch_no)):
			continue
		rate = flt((source_rates or {}).get(batch_no))
		if not rate:
			continue
		avg_rate[batch_no] = rate
		valuation.stock_value_change = flt(getattr(valuation, "stock_value_change", 0)) + (
			rate * _ledger_qty(ledger)
		)


def _ledger_qty(ledger):
	qty = getattr(ledger, "qty", None)
	if qty is None and isinstance(ledger, dict):
		qty = ledger.get("qty")
	return flt(qty)


def get_batch_source_valuation_rates(item_code, warehouse, batch_nos, exclude_voucher_no=None):
	"""Weighted average of submitted inward bundles for these batches.

	No posting-time filter: the inward document may be dated after a backdated
	issue and still be the batch's actual cost.
	"""
	batch_nos = [batch for batch in (batch_nos or []) if batch]
	if not item_code or not warehouse or not batch_nos:
		return {}

	parent = frappe.qb.DocType("Serial and Batch Bundle")
	child = frappe.qb.DocType("Serial and Batch Entry")
	query = (
		frappe.qb.from_(parent)
		.inner_join(child)
		.on(parent.name == child.parent)
		.select(
			child.batch_no,
			frappe.query_builder.functions.Sum(child.qty).as_("qty"),
			frappe.query_builder.functions.Sum(child.stock_value_difference).as_("value"),
			frappe.query_builder.functions.Sum(child.incoming_rate * child.qty).as_("rate_value"),
		)
		.where(
			(parent.item_code == item_code)
			& (parent.warehouse == warehouse)
			& (parent.docstatus == 1)
			& (parent.is_cancelled == 0)
			& (parent.type_of_transaction == "Inward")
			& (child.batch_no.isin(batch_nos))
			& (child.qty > 0)
		)
		.groupby(child.batch_no)
	)
	if exclude_voucher_no:
		query = query.where(parent.voucher_no != exclude_voucher_no)

	rates = {}
	for row in query.run(as_dict=True):
		qty = flt(row.qty)
		if not qty:
			continue
		value = flt(row.value) or flt(row.rate_value)
		if not value:
			continue
		rates[row.batch_no] = value / qty
	return rates


def _row_allows_zero_valuation(sle):
	voucher_type = getattr(sle, "voucher_type", None)
	detail = getattr(sle, "voucher_detail_no", None)
	if not voucher_type or not detail:
		return False
	child = {
		"Stock Entry": "Stock Entry Detail",
		"Purchase Receipt": "Purchase Receipt Item",
		"Purchase Invoice": "Purchase Invoice Item",
		"Delivery Note": "Delivery Note Item",
		"Sales Invoice": "Sales Invoice Item",
		"Stock Reconciliation": "Stock Reconciliation Item",
	}.get(voucher_type)
	if not child or not frappe.db.exists(child, detail):
		return False
	return bool(frappe.db.get_value(child, detail, "allow_zero_valuation_rate"))


_original_set_stock_value_difference = BatchNoValuation.set_stock_value_difference


def set_stock_value_difference(self):
	_original_set_stock_value_difference(self)
	try:
		sle = self.sle
		fill_unvalued_outgoing_batches(
			self,
			get_batch_source_valuation_rates(
				getattr(sle, "item_code", None),
				getattr(sle, "warehouse", None),
				list((getattr(self, "batch_nos", None) or {}).keys()),
				exclude_voucher_no=getattr(sle, "voucher_no", None),
			),
			allow_zero=_row_allows_zero_valuation(sle),
		)
	except Exception:
		frappe.log_error(title="Batch source valuation rate", message=frappe.get_traceback())


BatchNoValuation.set_stock_value_difference = set_stock_value_difference
