import frappe
from frappe import _
from frappe.utils import cint, flt, now


def sre_source_label(doc):
	"""Human source: FWO / BWRT / Transfer / Sales Order / Manual."""
	from_type = (getattr(doc, "from_voucher_type", None) or "").strip()
	from_no = (getattr(doc, "from_voucher_no", None) or "").strip()
	if from_type:
		return f"{from_type} {from_no}".strip()
	voucher_type = (getattr(doc, "voucher_type", None) or "").strip()
	voucher_no = (getattr(doc, "voucher_no", None) or "").strip()
	if voucher_type == "Sales Order" and voucher_no:
		return f"Sales Order {voucher_no}"
	return "Manual"


def _audit_comment(doc, action):
	if doc.flags.get("in_sre_audit_comment"):
		return
	user = frappe.session.user or "Guest"
	when = now()
	source = sre_source_label(doc)
	message = _("{0} by {1} on {2} via {3}.").format(
		action, frappe.bold(user), when, frappe.bold(source)
	)
	doc.flags.in_sre_audit_comment = True
	try:
		doc.add_comment("Info", message)
	except Exception:
		frappe.log_error(
			title="SRE audit comment failed",
			message=f"{action} {doc.name}: {frappe.get_traceback()}",
		)
	finally:
		doc.flags.in_sre_audit_comment = False


def _sb_snapshot(doc):
	return [
		(
			(row.batch_no or "").strip(),
			flt(row.qty),
			(getattr(row, "serial_no", None) or "").strip(),
			(row.warehouse or "").strip(),
		)
		for row in (getattr(doc, "sb_entries", None) or [])
	]


def _is_meaningful_edit(doc):
	"""Submitted SRE qty / batch change — not DN delivered_qty, not first submit."""
	if cint(doc.docstatus) != 1:
		return False
	if doc.flags.get("in_sre_audit_comment"):
		return False
	if getattr(doc, "_action", None) in ("submit", "cancel"):
		return False
	before = doc.get_doc_before_save()
	if before and cint(before.docstatus) == 0:
		return False
	if doc.has_value_changed("reserved_qty"):
		return True
	return bool(before) and _sb_snapshot(before) != _sb_snapshot(doc)


def _log_if_edited(doc):
	if _is_meaningful_edit(doc):
		_audit_comment(doc, _("Edited"))


def on_submit(doc, method=None):
	_audit_comment(doc, _("Created"))


def on_cancel(doc, method=None):
	_audit_comment(doc, _("Cancelled"))


def on_update(doc, method=None):
	# Draft save / submit also hit on_update. Submitted edits use
	# before_update_after_submit (ERPNext reloads in on_update_after_submit).
	_log_if_edited(doc)


def before_update_after_submit(doc, method=None):
	"""Submitted Save — run before ERPNext reload() wipes the change snapshot."""
	_log_if_edited(doc)
