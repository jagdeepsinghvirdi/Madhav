import frappe
from frappe import _
from frappe.utils import cint, now


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


def on_submit(doc, method=None):
	_audit_comment(doc, _("Created"))


def on_cancel(doc, method=None):
	_audit_comment(doc, _("Cancelled"))


def on_update(doc, method=None):
	"""Log a genuine reserved-qty edit after submit — not DN delivery."""
	if cint(doc.docstatus) != 1:
		return
	if doc.flags.get("in_sre_audit_comment"):
		return
	if getattr(doc, "_action", None) == "submit":
		return
	if not doc.has_value_changed("reserved_qty"):
		return
	before = doc.get_doc_before_save()
	if before and cint(before.docstatus) == 0:
		return
	_audit_comment(doc, _("Edited"))
