function fill_customer_po(frm) {
    const pending = (frm.doc.sales_orders || []).filter((row) => {
        return row.sales_order && !row.po_no && !(frm._customer_po_fetched || {})[row.name];
    });
    if (!pending.length) {
        return;
    }

    frm._customer_po_fetched = frm._customer_po_fetched || {};
    pending.forEach((row) => {
        frm._customer_po_fetched[row.name] = true;
    });

    frappe.db.get_list("Sales Order", {
        filters: { name: ["in", pending.map((row) => row.sales_order)] },
        fields: ["name", "po_no"],
        limit: pending.length,
    }).then((orders) => {
        const po_by_so = {};
        (orders || []).forEach((order) => {
            po_by_so[order.name] = order.po_no || "";
        });
        pending.forEach((row) => {
            row.po_no = po_by_so[row.sales_order] || "";
        });
        frm.refresh_field("sales_orders");
    });
}

frappe.ui.form.on('Production Plan', {
    refresh: function(frm) {
        fill_customer_po(frm);
        if (frm.doc.docstatus === 1) {
            if (frm.doc.status !== "Completed") {
                let items = frm.events.get_items_for_work_order(frm);

				if (items?.length && frm.doc.status !== "Closed") {
                    frm.remove_custom_button('Work Order / Subcontract PO', 'Create');
                    frm.add_custom_button(
                        __("Work Order or Subcontract PO"),
                        () => {
                            frm.trigger("make_work_order_custom");
                            // console.log("triggered")
                        },
                        __("Create")
                    );
                }
            }
        }
        // Show button only if Production Plan is submitted
        if (frm.doc.docstatus === 1) {
            frm.add_custom_button('Create Cutting Plan', function() {
                let source_warehouse = frm.doc.source_warehouse;
                let target_warehouse = frm.doc.fg_warehouse;

                frappe.new_doc("Cutting Plan", {
                    production_plan: frm.doc.name,  // link back to Production Plan
                    company: frm.doc.company,
                    target_qty: frm.doc.total_planned_qty,  // use Production Plan qty field
                    default_source_warehouse: source_warehouse,
                    default_finished_goods_warehouse: target_warehouse,
                    date: frappe.datetime.now_datetime(),
                });

                // Wait for Cutting Plan form to open
                setTimeout(function() {
                    let cutting_plan_form = cur_frm;
                    if (cutting_plan_form && cutting_plan_form.doctype === 'Cutting Plan') {
                        cutting_plan_form.set_value('default_source_warehouse', source_warehouse);

                        // Map items from Production Plan child table
                        let production_plan_items = frm.doc.po_items || [];

                        production_plan_items.forEach(d => {
                            let row = cutting_plan_form.add_child('cut_plan_detail');
                            row.item_code = d.item_code;
                            row.source_warehouse = d.warehouse || source_warehouse;
                            row.qty = d.planned_qty || d.qty;
                        });

                        cutting_plan_form.refresh_field('cut_plan_detail');
                    }
                }, 1000);
            });
        }
    },
    get_items_for_work_order(frm) {
		let items = frm.doc.po_items;
		if (frm.doc.sub_assembly_items?.length) {
			items = [...items, ...frm.doc.sub_assembly_items];
		}

		let has_items =
			items.filter((item) => {
				if (item.planned_qty) {
					return item.planned_qty > item.ordered_qty;
				} else {
					return item.qty > (item.received_qty || item.ordered_qty);
				}
			}) || [];

		return has_items;
	},
    make_work_order_custom(frm) {
		frappe.call({
			method: "make_work_order",
			freeze: true,
			doc: frm.doc,
			callback: function () {
				frm.reload_doc();
                update_latest_work_order(frm);
			},
		});
	},
    get_items(frm) {
        // Wait for ERPNext to finish populating rows
        frappe.after_ajax(() => {
            populate_all_rows(frm);
        });
    },
    get_sales_orders(frm) {
        // Delay required because rows are added asynchronously
        setTimeout(() => {
            populate_customer_names(frm);
        }, 500);
    },
});
function update_latest_work_order(frm) {
    frappe.call({
        method: "madhav.api.update_latest_wo_from_pp", // server method
        args: {
            production_plan: frm.doc.name
        },
        freeze: true,
        callback: function () {
            frm.reload_doc();
            frappe.msgprint("✅ Work Order updated from Production Plan!");
        }
    });
}
function populate_customer_names(frm) {
    (frm.doc.sales_orders || []).forEach(row => {
        if (row.sales_order && !row.customer_name) {
            frappe.db.get_value(
                "Sales Order",
                row.sales_order,
                ["customer", "customer_name"],
                (r) => {
                    if (r) {
                        row.customer = r.customer;
                        row.customer_name = r.customer_name;
                        frm.refresh_field("sales_orders");
                    }
                }
            );
        }
    });
}

frappe.ui.form.on("Production Plan Item", {
    item_code: function (frm, cdt, cdn) {
        fetch_so_item_pcs(frm, cdt, cdn);
    },
});
function populate_all_rows(frm) {
    (frm.doc.po_items || []).forEach(row => {
        fetch_so_item_pcs(frm, row.doctype, row.name);
    });
}
function fetch_so_item_pcs(frm, cdt, cdn) {
    let row = locals[cdt][cdn];

    if (!row.sales_order || !row.item_code) return;

    frappe.call({
        method: "madhav.api.get_so_item_pcs",
        args: {
            sales_order: row.sales_order,
            item_code: row.item_code,
            sales_order_item: row.sales_order_item,
            planned_qty: row.planned_qty || 0,
            row_id: cdn
        },
        callback(r) {
            console.log(r)
            if (r.message) {
                const row_id = r.message.row_id;
    
                frappe.model.set_value(cdt, row_id, "length_size_m", r.message.length_size || 0);
                // frappe.model.set_value(cdt, row_id, "pieces", r.message.pieces || 0);
                frappe.model.set_value(cdt, row_id, "po_no", r.message.po_no || "");
                // frappe.model.set_value(cdt, row_id, "section_weight", r.message.total_weight || "");
                // frappe.model.set_value(cdt, row_id, "planned_qty", r.message.planned_qty || 0);
                frappe.model.set_value(cdt, row_id, "assorted_length", r.message.assorted_length || "");
                frappe.model.set_value(cdt, row_id, "pending_qty", r.message.planned_qty || 0);
                frappe.model.set_value(cdt, row_id, "customers_purchase_order", r.message.po_no || "");
                frappe.model.set_value(cdt, row_id, "customer", r.message.customer || "");
                frappe.model.set_value(cdt, row_id, "customer_name", r.message.customer_name || "");
            }
        }
    });
}

frappe.ui.form.on("Production Plan Sales Order", {
    sales_order(frm, cdt, cdn) {
        const row = locals[cdt][cdn];

        if (!row.sales_order) {
            row.customer_name = "";
            frm.refresh_field("sales_orders");
            return;
        }

        frappe.db.get_value(
            "Sales Order",
            row.sales_order,
            ["customer", "customer_name", "po_no"],
            (r) => {
                if (r) {
                    row.customer = r.customer;
                    row.customer_name = r.customer_name;
                    row.po_no = r.po_no || "";
                    frm.refresh_field("sales_orders");
                }
            }
        );
    }
});
