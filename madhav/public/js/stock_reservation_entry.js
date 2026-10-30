frappe.ui.form.on("Stock Reservation Entry", {
	onload(frm) {
		show_sre_activity_timeline(frm);
	},
	refresh(frm) {
		show_sre_activity_timeline(frm);
	},
});

function show_sre_activity_timeline(frm) {
	// ERPNext sets hide_toolbar on this doctype, which skips the Activity
	// footer. Always mount it so Created / Edited / Cancelled comments show,
	// even if the user's Desk "timeline" setting is off.
	if (frm.is_new() || frm.footer) {
		if (frm.footer) {
			frm.footer.refresh();
		}
		return;
	}
	if (typeof frappe.ui.form.Footer !== "function" || !frm.page || !frm.page.main) {
		return;
	}
	frm.footer = new frappe.ui.form.Footer({
		frm: frm,
		parent: $("<div>").appendTo(frm.page.main.parent()),
	});
	frm.footer.refresh();
}
