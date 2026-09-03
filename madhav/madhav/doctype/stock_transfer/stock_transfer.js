// Copyright (c) 2026, Finbyz pvt. ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Stock Transfer", {

    setup(frm) {

         const set_df_grid_formatter = (doctype, fieldname, title_field, link_doctype) => {
        let df = frappe.meta.get_docfield(doctype, fieldname);
        if (df) {
            df.formatter = function(value, df, options, doc) {
                if (!value) return value;

                let title = "";

                if (doc && title_field && doc[title_field]) {
                    title = doc[title_field];
                }

                if (!title) {
                    title = frappe.utils.get_link_title(link_doctype, value) || "";
                }

                return title && title !== value
                    ? `${value}: ${title}`
                    : value;
            };
        }
    };

    [
        ["Stock Transfer Item", "customer", "customer_name", "Customer"],
    ].forEach(([dt, field, title, link]) => {
        set_df_grid_formatter(dt, field, title, link);
    });

        frm.set_query("source_warehouse", function () {
            return {
                filters: { company: frm.doc.company }
            };
        });

        frm.set_query("target_warehouse", function () {
            return {
                filters: { company: frm.doc.company }
            };
        });
    },


    fetch_details(frm) {

        if (!frm.doc.source_warehouse) {
            frappe.throw("Please select Source Warehouse");
        }

        frappe.call({
            method: "madhav.madhav.doctype.stock_transfer.stock_transfer.get_batch_stock",
            args: {
                source_warehouse: frm.doc.source_warehouse,
                target_warehouse: frm.doc.target_warehouse,
                from_date: frm.doc.from_date,
                to_date: frm.doc.to_date,
                item_name: frm.doc.item_name
            },
            freeze: true,

            callback: function (r) {

                if (!r.message) return;

                frm.clear_table("transfer_item");

                r.message.forEach(d => {

                    let row = frm.add_child("transfer_item");

                    row.batch = d.batch_no;
                    row.item_code = d.item_code;
                    row.item_name = d.item_name;

                    row.pieces = d.pieces;
                    row.qty = d.qty;

                    row.length = d.average_length;
                    row.section_weight = d.section_weight;
                    row.source_document_type = d.reference_doctype;
                    row.source_document_name = d.reference_name;
                    row.source_warehouse = frm.doc.source_warehouse;
                    row.target_warehouse = frm.doc.target_warehouse;

                });

                frm.refresh_field("transfer_item");

                frappe.msgprint(`Fetched ${r.message.length} records`);
            }
        });
    }
});


let calculating = false;


frappe.ui.form.on("Stock Transfer Item", {

    async pieces(frm, cdt, cdn) {
        await recalculate_row(cdt, cdn, "pieces");
    },

    async qty(frm, cdt, cdn) {
        await recalculate_row(cdt, cdn, "qty");
    }

});


async function recalculate_row(cdt, cdn, changed_field) {

    if (calculating) return;

    calculating = true;

    try {

        let row = locals[cdt][cdn];

        const is_valid = await validate_batch_limit(cdt, cdn, changed_field);

        if (!is_valid) {
            return; // Exit without recalculation, finally will reset flag
        }

        // Re-fetch row after validation (values might have changed)
        row = locals[cdt][cdn];

        /* PIECES CHANGED
           Prefer keeping transferred Tonne qty when qty is already set.
           Deriving qty from pcs×length×section_weight overwrote 1T → ~0.788
           whenever Batch/Item section_weight did not match the physical
           pcs/length for that tonne transfer. */
        if (changed_field === "pieces") {

            if (row.length && flt(row.pieces) && flt(row.qty)) {
                let section_weight =
                    (flt(row.qty) * 1000) / (flt(row.pieces) * flt(row.length));

                frappe.model.set_value(
                    cdt,
                    cdn,
                    "section_weight",
                    flt(section_weight, 6),
                    null,
                    false
                );
            } else if (row.length && row.section_weight) {

                let qty = (flt(row.pieces) * flt(row.length) * flt(row.section_weight)) / 1000;

                frappe.model.set_value(
                    cdt,
                    cdn,
                    "qty",
                    flt(qty, 3),
                    null,
                    false
                );
            }
        }

        /* QTY CHANGED → Calculate PIECES */
        if (changed_field === "qty") {

            if (row.length && row.section_weight) {

                let pieces = (flt(row.qty) * 1000) / (flt(row.length) * flt(row.section_weight));

                frappe.model.set_value(
                    cdt,
                    cdn,
                    "pieces",
                    Math.round(pieces),
                    null,
                    false
                );
            }
        }

    } finally {

        setTimeout(() => {
            calculating = false;
        }, 100);
    }
}


/* ===============================
   BATCH LIMIT VALIDATION
================================ */

async function validate_batch_limit(cdt, cdn, fieldname) {

    let row = locals[cdt][cdn];

    if (!row.batch) return true;

    let batch;

    try {
        batch = await frappe.db.get_doc("Batch", row.batch);
    } catch (e) {
        console.error("Error fetching batch:", e);
        return true; // Allow if batch fetch fails
    }


    /* PIECES VALIDATION */

    if (fieldname === "pieces") {

        let entered = flt(row.pieces);
        let limit = flt(batch.pieces);

        if (entered > limit) {

            // fire_changes: false to prevent re-triggering
            frappe.model.set_value(
                cdt,
                cdn,
                "pieces",
                limit,
                null,   // callback
                false   // fire_changes = false
            );

            frappe.msgprint({
                title: "Batch Limit Reached",
                message: `Pieces cannot exceed Batch Pieces. Value reset to <b>${limit}</b>`,
                indicator: "orange"
            });

            return false;
        }
    }


    /* QTY VALIDATION */

    if (fieldname === "qty") {

        let entered = flt(row.qty);
        let limit = flt(batch.batch_qty);

        if (entered > limit) {

            // fire_changes: false to prevent re-triggering
            frappe.model.set_value(
                cdt,
                cdn,
                "qty",
                limit,
                null,   // callback
                false   // fire_changes = false
            );

            frappe.msgprint({
                title: "Batch Limit Reached",
                message: `Qty cannot exceed Batch Qty. Value reset to <b>${limit}</b>`,
                indicator: "orange"
            });

            return false;
        }
    }

    return true;
}