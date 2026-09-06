frappe.ui.form.on("Sales Invoice", {
   cost_center: function(frm) {
        update_taxes_fields(frm);
    },
    branch: function(frm) {
        update_taxes_fields(frm);
    },
    validate(frm){
        update_taxes_fields(frm);
    },
    refresh(frm) {
    if (frm.doc.docstatus === 1 && frm.doc.deliver_as_qty !== undefined) {
        frm.add_custom_button(__("Delivery Note"), function () {
            frappe.model.open_mapped_doc({
                method: "madhav.doc_events.delivery_note.make_delivery_note_from_si",
                frm: frm,
            });
        }, __("Create"));
    }
},
});
function update_taxes_fields(frm) {
    if (!frm.doc.taxes) return;

    frm.doc.taxes.forEach(row => {
        if (frm.doc.cost_center) {
            row.cost_center = frm.doc.cost_center;
        }

        // If you have branch field in taxes table
        if (frm.doc.branch && row.branch !== undefined) {
            row.branch = frm.doc.branch;
        }
    });

    frm.refresh_field('taxes');
}