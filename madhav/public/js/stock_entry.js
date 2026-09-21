frappe.ui.form.on('Stock Entry', {
    cost_center: function (frm) {
        update_items_fields(frm);
    },
    branch: function (frm) {
        update_items_fields(frm);
    },
    
    // Handler you can call from your existing custom button:
    get_items_from_cut_plan: function(frm) {
        if (!frm.doc.work_order) {
            frappe.msgprint(__('Please set Work Order first'));
            return;
        }

        frappe.call({
            method: 'madhav.api.get_items_from_cut_plan',
            args: {
                work_order: frm.doc.work_order
            },
            freeze: true,
            callback: function(r) {
                const items = (r && r.message) || [];
                if (!items.length) {
                    frappe.msgprint(__('No matching Cut Plan Finish rows found'));
                    return;
                }

                // Clear existing items before appending new ones
                frm.clear_table('items');

                // Set default from_warehouse on the document
                frm.set_value('from_warehouse', 'Planned Cutting RM - MS');
                frm.set_value('to_warehouse', 'Semi Finished Goods - MS');

                items.forEach(function(d) {
                    frm.add_child('items', {
                        item_code: d.item_code,
                        qty: d.qty,
                        pieces: d.pieces,
                        average_length: d.average_length,
                        section_weight: d.section_weight,
                        lot_no: d.lot_no,
                        total_pcs: d.total_pcs,
                        fg_item: d.fg_item,
                        semi_fg_length: d.semi_fg_length,
                        work_order_reference: d.work_order_reference,
                        // t_warehouse: d.t_warehouse,
                        batch_no: d.batch_no,
                        use_serial_batch_fields: 1,
                        required_stock_in_pieces: 1,
                        stock_uom: d.stock_uom,
                        stock_qty: d.stock_qty,
                        transfer_qty: d.qty,
                        uom: d.uom,
                        conversion_factor: d.conversion_factor,
                        basic_rate: d.basic_rate
                    });
                });

                frm.refresh_field('items');
            }
        });
    },

    onload: function(frm) {
        
        if (frm.is_new() && frm.doc.company) {
            frm.trigger('company');
        }

        // Set filter for Reference Material Issue Entry
        frm.set_query('reference_material_issue_entry', function() {
            if (frm.doc.stock_entry_type === "Material Issue Return") {
                return {
                    filters: {
                        stock_entry_type: "Material Issue",
                        docstatus: 1,
                        company: frm.doc.company
                    }
                };
            }
        });
         //New logic: copy qty → required_qty in each row
        if (frm.is_new() && frm.doc.work_order && frm.doc.stock_entry_type === "Material Transfer for Manufacture") {
            (frm.doc.items || []).forEach(d => {
                frappe.model.set_value(d.doctype, d.name, 'required_qty', d.qty);
            });
        }
    },
    
    weight_received: function(frm) {
        update_totals(frm);
    },
    reference_material_issue_entry: function(frm) {
        if (!frm.doc.reference_material_issue_entry) return;

        frappe.call({
            method: "frappe.client.get",
            args: {
                doctype: "Stock Entry",
                name: frm.doc.reference_material_issue_entry
            },
            callback: function(r) {
                if (r.message && r.message.items) {
                    frm.clear_table("items");

                    r.message.items.forEach(function(item) {
                        const new_row = frm.add_child("items", {
                            item_code: item.item_code,
                            required_stock_in_pieces: item.required_stock_in_pieces,
                            qty: item.qty,
                            transfer_qty: item.qty,
                            uom: item.uom,
                            stock_uom: item.stock_uom,                           
                            average_length: item.average_length,
                            section_weight: item.section_weight,
                            basic_rate: item.basic_rate,
                            conversion_factor: item.conversion_factor,
                            stock_qty: item.stock_qty,
                            use_serial_batch_fields: 1,
                            original_qty: item.qty
                        });
                    });

                    frm.refresh_field("items");
                }
            }
        });
    },

    stock_entry_type: function(frm) {
        if (frm.doc.stock_entry_type === "Material Issue Return") {
            frappe.msgprint({
                title: "Mandatory Reference",
                message: "For 'Material Issue Return' type stock entry, it is mandatory to select the Reference Material Issue Entry.",
                indicator: "orange"
            });
        }
    }
});

frappe.ui.form.on('Stock Entry Detail', {
    item_code: function(frm, cdt, cdn) {
        
        if (frm.doc.stock_entry_type === "Material Transfer for Manufacture" || frm.doc.stock_entry_type === "Material Transfer") {
            // Small delay to ensure item_code is properly set
            setTimeout(() => {
                set_batch_filter(frm, cdt, cdn);
            }, 100);
        }
    },
    pieces: function (frm, cdt, cdn) { 
        
        update_totals(frm);
    },
    average_length: function (frm, cdt, cdn) {
        
        const child = locals[cdt][cdn];

        if (
            (frm.doc.stock_entry_type === "Material Transfer for Manufacture" ||
            frm.doc.stock_entry_type === "Material Transfer") &&
            child.average_length
        ) {
            set_batch_filter(frm, cdt, cdn);
        }

        update_totals(frm);
    },
    items_remove: function (frm, cdt, cdn) {
        update_totals(frm);
    },
    batch_no: function(frm, cdt, cdn) {
        
        const child = locals[cdt][cdn];
        const grid_row = frm.fields_dict["items"].grid.grid_rows_by_docname[cdn];

        if (grid_row) {
            // Make average_length read-only if batch_no is selected
            grid_row.toggle_editable("average_length", !child.batch_no);
        }

        if (frm.doc.stock_entry_type === "Repack") {
            fetch_batch_details_for_repack(frm, cdt, cdn);
        }
    }
});

function fetch_batch_details_for_repack(frm, cdt, cdn) {
    const child = locals[cdt][cdn];
    if (!child.batch_no) {
        return;
    }

    frappe.db.get_value(
        "Batch",
        child.batch_no,
        ["average_length", "section_weight", "pieces", "length_weight_in_kg"],
        (r) => {
            if (!r) {
                return;
            }

            const updates = {};
            if (flt(r.average_length)) {
                updates.average_length = flt(r.average_length);
            }
            if (flt(r.section_weight)) {
                updates.section_weight = flt(r.section_weight);
            }
            if (flt(r.pieces)) {
                updates.pieces = flt(r.pieces);
            }
            if (flt(r.length_weight_in_kg)) {
                updates.length_weight_in_kg = flt(r.length_weight_in_kg);
            }

            // Qty (tonne) from pieces × length × section weight, same as other SE types
            const pieces = flt(updates.pieces != null ? updates.pieces : child.pieces);
            const avg_len = flt(
                updates.average_length != null ? updates.average_length : child.average_length
            );
            const section_weight = flt(
                updates.section_weight != null ? updates.section_weight : child.section_weight
            );
            if (pieces && avg_len && section_weight) {
                updates.qty = flt((pieces * avg_len * section_weight) / 1000, 4);
            }

            Object.keys(updates).forEach((field) => {
                frappe.model.set_value(cdt, cdn, field, updates[field]);
            });
        }
    );
}

function set_batch_filter(frm, cdt, cdn) {
    // Set the query filter for batch_no field
    frm.set_query('batch_no', 'items', function(doc, cdt, cdn) {
        const child = locals[cdt][cdn];
        
        return {
            query: "madhav.api.get_filtered_batches",
            filters: {
                average_length: child.average_length,
                item_code: child.item_code,
                warehouse: child.s_warehouse,
                include_expired: 1
            }
        };
    });
}

function update_totals(frm) {
    
    if(frm.doc.stock_entry_type === "Material Receipt"){
                
        let total_length = 0;

    // Step 1: Calculate total_length_in_meter
    frm.doc.items.forEach(item => {
        let pieces = flt(item.pieces);
        let avg_len = flt(item.average_length);

        if (pieces && avg_len) {
            total_length += pieces * avg_len;
        }
    });

    frm.set_value("total_length_in_meter", total_length.toFixed(2));

    // Step 2: Calculate section_weight from weight_received (in tonnes → kg)
    let weight_received = flt(frm.doc.weight_received);  // in tonnes
    let weight_received_kg = weight_received * 1000;

    let section_weight = total_length > 0 ? weight_received_kg / total_length : 0;

    // Step 3: Update each row
    frm.doc.items.forEach(item => {
        let pieces = flt(item.pieces);
        let avg_len = flt(item.average_length);

        // Calculate accepted_qty and set values
        item.section_weight = section_weight.toFixed(2);

        let accepted_qty_kg = pieces * avg_len * section_weight;
        item.qty = (accepted_qty_kg / 1000).toFixed(4); // in tonnes

    });

        frm.refresh_field("items");
    }
    
    else if (frm.doc.stock_entry_type === "Material Transfer for Manufacture" || frm.doc.stock_entry_type === "Material Transfer") {
             
        frm.doc.items.forEach(item => {
            let pieces = flt(item.pieces);
            let avg_len = flt(item.average_length);
            let section_weight = flt(item.section_weight);

            let accepted_qty_kg = pieces * avg_len * section_weight;
            let qty = (accepted_qty_kg / 1000).toFixed(4);  // in tonnes

            frappe.model.set_value(item.doctype, item.name, "qty", qty);
            frappe.model.set_value(item.doctype, item.name, "pieces", pieces);
        });
    }

    else if (frm.doc.stock_entry_type === "Material Issue Return") {
        frm.doc.items.forEach(item => {
            let pieces = flt(item.pieces);
            let avg_len = flt(item.average_length);
            let section_weight = flt(item.section_weight);

            let accepted_qty_kg = pieces * avg_len * section_weight;
            let qty = (accepted_qty_kg / 1000).toFixed(4);

            if (item.original_qty && qty > flt(item.original_qty)) {
                frappe.msgprint({
                    title: "Quantity Exceeded",
                    message: `Qty for item <b>${item.item_code}</b> cannot exceed original reference qty: <b>${item.original_qty}</b>`,
                    indicator: "red"
                });

                // Optional: Reset pieces or qty
                frappe.model.set_value(item.doctype, item.name, "pieces", 0);
                frappe.model.set_value(item.doctype, item.name, "qty", flt(item.original_qty));
            } else {
                frappe.model.set_value(item.doctype, item.name, "qty", qty);
            }
        });
    }
}

function update_items_fields(frm) {
    if (!frm.doc.items) return;

    frm.doc.items.forEach(row => {

        if (frm.doc.cost_center) {
            frappe.model.set_value(row.doctype, row.name, "cost_center", frm.doc.cost_center);
        }

        if (frm.doc.branch) {
            frappe.model.set_value(row.doctype, row.name, "branch", frm.doc.branch);
        }

    });

    frm.refresh_field('items');
}