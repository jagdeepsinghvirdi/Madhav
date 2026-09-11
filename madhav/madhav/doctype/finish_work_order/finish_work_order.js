// Copyright (c) 2026, Finbyz pvt. ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Finish Work Order", {
    setup(frm) {
        // Apply custom formatters directly to the child docfields
        frm.ignore_doctypes_on_cancel_all = ["Work Order"];
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
            ["Pending Work Orders", "item", "item_name", "Item"],
            ["Pending Work Orders", "party_name", "party", "Customer"],
            ["Raw Material Items", "item_code", "item_name", "Item"],
            ["Scrap Items", "item", "item_name", "Item"]
        ].forEach(([dt, field, title, link]) => {
            set_df_grid_formatter(dt, field, title, link);
        });
    },

    refresh(frm) {
        set_unplanned_checkbox_lock(frm);
        set_batch_query(frm);
        apply_work_order_filters(frm);
        set_warehouse_filter(frm, "pending_work_orders", "target_warehouse");
        set_warehouse_filter(frm, "raw_materials", "source_warehouse");

        fetch_missing_titles(frm);

        frm.refresh_field("pending_work_orders");
        frm.refresh_field("raw_materials");
        frm.refresh_field("scrap_items");

        // ── FIX: hijack the button so only a real mouse-click sets the flag ──
        frm._fetch_btn_clicked = false;

        const $btn = frm.get_field("fetch_pending_work_orders").$input;
        if ($btn && $btn.length) {
            $btn.off("mousedown.fetch_guard click.fetch_guard");

            // mousedown fires before Frappe's click handler
            $btn.on("mousedown.fetch_guard", function () {
                frm._fetch_btn_clicked = true;
                // safety reset in case something swallows the click
                setTimeout(() => { frm._fetch_btn_clicked = false; }, 1000);
            });
        }
    },

    onload(frm) {
        apply_work_order_filters(frm);
    },

    date(frm) {
        apply_work_order_filters(frm);
    },

    company(frm) {
        set_warehouse_filter(frm, "pending_work_orders", "target_warehouse");
        set_warehouse_filter(frm, "raw_materials", "source_warehouse");
    },

    fetch_pending_work_orders(frm) {
        // ── FIX: block every call that didn't come from a real button click ──
        if (!frm._fetch_btn_clicked) return;
        frm._fetch_btn_clicked = false;
        // ─────────────────────────────────────────────────────────────────────

        if (!frm.doc.company) {
            frappe.throw(__('Company field is mandatory'));
            return;
        }

        frappe.call({
            method: "madhav.api.populate_pending_work_orders",
            args: {
                filters: {
                    date: frm.doc.date,
                    item_name: frm.doc.item_name,
                    wo_number: frm.doc.wo_number,
                    sales_order: frm.doc.sales_order,
                    company: frm.doc.company,
                    current_doc: frm.doc.name || ""
                }
            },
            freeze: true,

            callback(r) {
                if (!r.exc && r.message) {
                    frm.clear_table("pending_work_orders");

                    r.message.forEach(wo => {
                        let row = frm.add_child("pending_work_orders");

                        row.work_order = wo.name;
                        row.party_name = wo.customer_name;
                        row.party = wo.customer;
                        row.customer_name = wo.customer_name;
                        row.quality_required = wo.quality_required;

                        row.sales_order_qty = wo.qty;

                        row.target_warehouse = wo.fg_warehouse;

                        row.item = wo.production_item;
                        row.item_name = wo.item_name;
                        row.assorted_length = wo.assorted_length;

                        row.stock_uom = wo.stock_uom;

                        row.grade = wo.item_name;

                        row.pieces = wo.pending_pcs;

                        row.length_size = wo.length;
                        row.actual_average_length = wo.length;

                        row.ready_pieces = wo.pending_pcs;

                        row.variation = wo.variation_allowed;
                        row.remarks = wo.remarks;
                        row.po_no = wo.po_no;

                        row.actual_weight = wo.weight_per_meter;
                        row.standard_weight = wo.weight_per_meter;
                        row.actual_section_weight = wo.weight_per_meter;

                        row.qty = wo.qty - wo.produced_qty;
                        row.ready_qty = wo.qty - wo.produced_qty;
                        row.remaining_qty = wo.qty - wo.produced_qty;

                        row.sales_order = wo.sales_order;

                        if (wo.customer && wo.customer_name) {
                            frappe.utils.add_link_title(
                                "Customer",
                                wo.customer,
                                wo.customer_name
                            );
                        }
                    });

                    frm.refresh_field("pending_work_orders");
                    frm.dirty();
                    fetch_raw_materials_from_bom(frm);  

                    frappe.msgprint(
                        __("Fetched {0} Work Orders", [r.message.length])
                    );
                }
            }
        });
    }
});

function fetch_raw_materials_from_bom(frm) {
    let items = (frm.doc.pending_work_orders || [])
        .filter(row => row.item && row.ready_qty)
        .map(row => ({ item_code: row.item, qty: row.ready_qty }));

    if (!items.length) return;

    frappe.call({
        method: "madhav.madhav.doctype.finish_work_order.finish_work_order.get_bom_raw_materials_for_items",
        args: { items: items },
        callback(r) {
            if (!r.exc && r.message) {
                frm.clear_table("raw_materials");
                r.message.forEach(rm => {
                    let row = frm.add_child("raw_materials");
                    row.item_code = rm.item_code;
                    row.qty = rm.qty;
                });
                frm.refresh_field("raw_materials");
                frm.dirty();
            }
        }
    });
}

frappe.ui.form.on('Raw Material Items', {
    item_code: function(frm, cdt, cdn) {
        frappe.model.set_value(cdt, cdn, 'batch_no', '');
        set_batch_query(frm, cdt, cdn);
        fetch_batch_qty(frm, cdt, cdn);
    },
    source_warehouse: function(frm, cdt, cdn) {
        fetch_batch_qty(frm, cdt, cdn);
        set_batch_query(frm, cdt, cdn);
    },

    batch_no(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        if (!row.batch_no) return;

        frappe.db.get_value(
            'Batch',
            row.batch_no,
            ['pieces', 'average_length', 'section_weight', 'reference_doctype', 'reference_name']
        ).then((res) => {
            let data = res.message;
            if (!data) return;

            frappe.model.set_value(cdt, cdn, 'pieces', data.pieces);
            frappe.model.set_value(cdt, cdn, 'length', data.average_length);
            frappe.model.set_value(cdt, cdn, 'section_weight', data.section_weight);

            if (
                data.reference_doctype === "Purchase Receipt" &&
                data.reference_name
            ) {
                frappe.db.get_value(
                    "Purchase Receipt",
                    data.reference_name,
                    ["supplier", "supplier_name"]
                ).then((pr) => {
                    let pr_data = pr.message;
                    if (!pr_data) return;

                    frappe.model.set_value(cdt, cdn, 'supplier', pr_data.supplier);
                    frappe.model.set_value(cdt, cdn, 'supplier_name', pr_data.supplier_name);
                });
            }
        });

        fetch_batch_qty(frm, cdt, cdn);
    }
});

function set_batch_query(frm, cdt, cdn) {
    frm.fields_dict.raw_materials.grid.get_field("batch_no").get_query = function(doc, cdt, cdn) {
        let row = locals[cdt][cdn];

        return {
            query: "madhav.madhav.doctype.finish_work_order.finish_work_order.get_available_batches",
            filters: {
                item_code: row.item_code,
                warehouse: row.source_warehouse,
                supplier: row.supplier
            }
        };
    };
}

function fetch_missing_titles(frm) {
    let missing_customers = [];
    let missing_items = [];

    const check_missing = (rows, link_field, title_field, missing_list) => {
        (rows || []).forEach(row => {
            if (row[link_field] && !row[title_field]) {
                missing_list.push(row[link_field]);
            }
        });
    };

    check_missing(frm.doc.pending_work_orders, "party_name", "party", missing_customers);
    check_missing(frm.doc.pending_work_orders, "item", "item_name", missing_items);
    check_missing(frm.doc.raw_materials, "item_code", "item_name", missing_items);
    check_missing(frm.doc.scrap_items, "item", "item_name", missing_items);

    missing_customers = [...new Set(missing_customers)];
    missing_items = [...new Set(missing_items)];

    if (missing_customers.length > 0) {
        frappe.call({
            method: "frappe.client.get_list",
            args: {
                doctype: "Customer",
                filters: { name: ["in", missing_customers] },
                fields: ["name", "customer_name"]
            },
            callback: function(r) {
                if (r.message) {
                    r.message.forEach(c => {
                        frappe.utils.add_link_title("Customer", c.name, c.customer_name);
                        (frm.doc.pending_work_orders || []).forEach(row => {
                            if (row.party_name === c.name) {
                                frappe.model.set_value(row.doctype, row.name, "party", c.customer_name);
                            }
                        });
                    });
                    frm.refresh_field("pending_work_orders");
                }
            }
        });
    }

    if (missing_items.length > 0) {
        frappe.call({
            method: "frappe.client.get_list",
            args: {
                doctype: "Item",
                filters: { name: ["in", missing_items] },
                fields: ["name", "item_name"]
            },
            callback: function(r) {
                if (r.message) {
                    r.message.forEach(i => {
                        frappe.utils.add_link_title("Item", i.name, i.item_name);

                        (frm.doc.pending_work_orders || []).forEach(row => {
                            if (row.item === i.name) {
                                frappe.model.set_value(row.doctype, row.name, "item_name", i.item_name);
                                frappe.model.set_value(row.doctype, row.name, "grade", i.item_name);
                            }
                        });

                        (frm.doc.raw_materials || []).forEach(row => {
                            if (row.item_code === i.name) {
                                frappe.model.set_value(row.doctype, row.name, "item_name", i.item_name);
                            }
                        });

                        (frm.doc.scrap_items || []).forEach(row => {
                            if (row.item === i.name) {
                                frappe.model.set_value(row.doctype, row.name, "item_name", i.item_name);
                            }
                        });
                    });
                    frm.refresh_field("pending_work_orders");
                    frm.refresh_field("raw_materials");
                    frm.refresh_field("scrap_items");
                }
            }
        });
    }
}

frappe.ui.form.on("Pending Work Orders", {
    ready_qty(frm, cdt, cdn) {
        let row = locals[cdt][cdn];

        frappe.db.get_single_value("Manufacturing Settings",
            "overproduction_percentage_for_work_order"
        ).then((percentage) => {
            percentage = flt(percentage) || 0;
            let multiplier = 1 + (percentage / 100);
            let max_allowed = flt(row.qty) * multiplier;

            if (flt(row.ready_qty) > max_allowed) {
                frappe.msgprint(
                    __('Ready Qty cannot be greater than Qty by more than {0}%', [percentage])
                );
                frappe.model.set_value(cdt, cdn, 'ready_qty', row.qty);
            }

            check_weight_variance(frm, cdt, cdn);
        });
    },

    ready_pieces(frm, cdt, cdn) {
        let row = locals[cdt][cdn];

        frappe.db.get_single_value("Manufacturing Settings",
            "overproduction_percentage_for_work_order"
        ).then((percentage) => {
            percentage = flt(percentage) || 0;
            let multiplier = 1 + (percentage / 100);
            let max_allowed = flt(row.pieces) * multiplier;

            if (flt(row.ready_pieces) > max_allowed) {
                frappe.msgprint(
                    __('Ready Pieces cannot be greater than Pieces by more than {0}%', [percentage])
                );
                frappe.model.set_value(cdt, cdn, 'ready_pieces', row.pieces);
            }

            check_weight_variance(frm, cdt, cdn);
        });
        frappe.model.set_value(cdt, cdn, "calculated_qty", row.ready_pieces * row.length_size * row.standard_weight / 1000);
    },

    length_size(frm, cdt, cdn) {
        check_weight_variance(frm, cdt, cdn);
    },

    make_it_unplanned(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        if (row.make_it_unplanned) {
            row.old_work_order = row.work_order;
            row.work_order = "";
            row.old_sales_order = row.sales_order;
            row.sales_order = "";
            row.variation = 1;
        } else {
            row.work_order = row.old_work_order || "";
            row.old_work_order = "";
            row.sales_order = row.old_sales_order;
            row.old_sales_order = "";
            row.variation = 0;
        }
        frm.refresh_field("pending_work_orders");
    },

    form_render(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        let grid = frm.fields_dict.pending_work_orders.grid;
        let grid_row = grid.grid_rows_by_docname[cdn];

        if (row.make_it_unplanned) {
            grid_row.toggle_editable("make_it_unplanned", false);
        }
    },

    pending_work_orders_add: function(frm, cdt, cdn) {
        frappe.model.set_value(cdt, cdn, 'target_warehouse', null);
    }
});

function check_weight_variance(frm, cdt, cdn) {
    let row = locals[cdt][cdn];

    if (!row || !row.ready_qty || !row.ready_pieces || !row.length_size || !row.standard_weight) {
        return;
    }

    let calculated = flt(row.ready_qty) * 1000 / (flt(row.ready_pieces) * flt(row.length_size));
    let max_allowed = flt(row.standard_weight) * 1.20;
    let min_allowed = flt(row.standard_weight) * 0.80;
    frappe.model.set_value(cdt, cdn, 'calculated_section_weight', calculated);

    let grid = frm.fields_dict["pending_work_orders"].grid;
    let grid_row = grid.grid_rows_by_docname[cdn];

    if (!grid_row || !grid_row.row) return;

    if (calculated > max_allowed || calculated < min_allowed) {
        $(grid_row.on_grid_fields[0].$input).css({ "background-color": "#fff0f0" });
        $(grid_row.on_grid_fields[1].$input).css({ "background-color": "#fff0f0" });
        $(grid_row.on_grid_fields[2].$input).css({ "background-color": "#fff0f0" });
        $(grid_row.on_grid_fields[3].$input).css({ "background-color": "#fff0f0" });
        $(grid_row.on_grid_fields[4].$input).css({ "background-color": "#fff0f0" });
        $(grid_row.on_grid_fields[5].$input).css({ "background-color": "#fff0f0" });
        $(grid_row.on_grid_fields[6].$input).css({ "background-color": "#fff0f0" });
        $(grid_row.on_grid_fields[7].$input).css({ "background-color": "#fff0f0" });
        $(grid_row.on_grid_fields[8].$input).css({ "background-color": "#fff0f0" });
        $(grid_row.row).css({ "background-color": "#fff0f0" });
    } else {
        $(grid_row.row).css({ "background-color": "", "border-left": "" });
    }
}

function set_warehouse_filter(frm, child_table, warehouse) {
    frm.set_query(warehouse, child_table, function(doc, cdt, cdn) {
        if (!frm.doc.company) return {};
        return {
            filters: { company: frm.doc.company }
        };
    });
}

function set_unplanned_checkbox_lock(frm) {
    let grid = frm.fields_dict.pending_work_orders?.grid;
    if (!grid) return;

    (frm.doc.pending_work_orders || []).forEach(row => {
        let grid_row = grid.grid_rows_by_docname[row.name];
        if (!grid_row) return;
        if (row.make_it_unplanned) {
            grid_row.toggle_editable("make_it_unplanned", false);
        }
    });
}

function fetch_batch_qty(frm, cdt, cdn) {
    let row = locals[cdt][cdn];

    if (row.source_warehouse && row.item_code && row.batch_no) {
        frappe.call({
            method: "erpnext.stock.doctype.batch.batch.get_batch_qty",
            args: {
                batch_no: row.batch_no,
                warehouse: row.source_warehouse,
                item_code: row.item_code
            },
            callback: function(r) {
                if (r.message) {
                    frappe.model.set_value(cdt, cdn, 'qty', r.message);
                }
            }
        });
    }
}

function apply_work_order_filters(frm) {
    if (!frm.doc.date) {
        set_empty_filters(frm);
        return;
    }

    frappe.call({
        method: 'frappe.client.get_list',
        args: {
            doctype: 'Work Order',
            filters: {
                creation: ['between', [
                    frm.doc.date + ' 00:00:00',
                    frm.doc.date + ' 23:59:59'
                ]]
            },
            fields: ['sales_order', 'name', 'customer'],
            limit_page_length: 0
        },
        callback(r) {
            if (!r.message) {
                set_empty_filters(frm);
                return;
            }

            let sales_orders = [...new Set(r.message.map(d => d.sales_order).filter(Boolean))];
            let wo_numbers   = [...new Set(r.message.map(d => d.name).filter(Boolean))];
            let wo_customers = [...new Set(r.message.map(d => d.customer).filter(Boolean))];

            frm.set_query('sales_order', () => ({
                filters: { name: ['in', sales_orders.length ? sales_orders : ['']] }
            }));

            frm.set_query('wo_number', () => ({
                filters: { name: ['in', wo_numbers.length ? wo_numbers : ['']] }
            }));

            frm.set_query('customer', () => ({
                filters: { name: ['in', wo_customers.length ? wo_customers : ['']] }
            }));
        }
    });
}

function set_empty_filters(frm) {
    frm.set_query('sales_order', () => ({ filters: { name: ['in', ['']] } }));
    frm.set_query('wo_number',   () => ({ filters: { name: ['in', ['']] } }));
    frm.set_query('customer',    () => ({ filters: { name: ['in', ['']] } }));
}