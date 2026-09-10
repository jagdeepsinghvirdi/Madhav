// Copyright (c) 2026, Finbyz pvt. ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Batch Wise Reservation Tool", {
	setup(frm) {
		frm.set_query("sales_order", () => {
			let filters = { docstatus: 1 };
			if (frm.doc.customer) {
				filters.customer = frm.doc.customer;
			}
			return { filters: filters };
		});
		madhav_load_tolerance_warehouse(frm);
	},
	refresh(frm) {
		if (frm.doc.docstatus == 1) {
			frm.trigger("render_batch_reserved");
		}
	},
	fetch_sales_order(frm) {
		if (frm.doc.docstatus == 0) {
			frm.trigger("fetch_sales_order_details");
		}
	},
	customer(frm) {
		frm.set_value("sales_order", "");
	},
	on_submit(frm) {
		frm.trigger("render_batch_reserved");
	},
	render_batch_reserved(frm) {
	frappe.call({
		method: "madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool.get_reserved_batches",
		args: {
			docname: frm.doc.name
		},
		callback(r) {
			let html;

			if (!r.message || !r.message.length) {
				html = `
					<div style="
						padding: 12px 16px;
						border-radius: 6px;
						background: #fff7ed;
						border: 1px solid #fed7aa;
						color: #9a3412;
						font-weight: 500;
					">
						No batches reserved.
					</div>
				`;
			} else {
				let rows = r.message;

				html = `
					<div style="
						border: 1px solid #dbe3ec;
						border-radius: 8px;
						overflow: hidden;
						background: #ffffff;
					">

						<div style="
							background: #eef4f9;
							color: #334155;
							padding: 10px 14px;
							font-size: 14px;
							font-weight: 600;
							border-bottom: 1px solid #dbe3ec;
						">
							Reserved Batches

							<span style="
								float: right;
								background: #dcfce7;
								color: #15803d;
								padding: 3px 9px;
								border-radius: 12px;
								font-size: 11px;
								font-weight: 600;
							">
								${rows.length} Batch${rows.length > 1 ? "es" : ""}
							</span>
						</div>

						<table style="
							width: 100%;
							border-collapse: collapse;
							font-size: 13px;
						">
							<thead>
								<tr style="background: #f8fafc;">
									<th style="
										padding: 9px 10px;
										text-align: left;
										border-bottom: 1px solid #e2e8f0;
										color: #475569;
									">
										Sales Order
									</th>

									<th style="
										padding: 9px 10px;
										text-align: left;
										border-bottom: 1px solid #e2e8f0;
										color: #475569;
									">
										Item
									</th>

									<th style="
										padding: 9px 10px;
										text-align: left;
										border-bottom: 1px solid #e2e8f0;
										color: #475569;
									">
										Batch
									</th>

									<th style="
										padding: 9px 10px;
										text-align: right;
										border-bottom: 1px solid #e2e8f0;
										color: #475569;
									">
										Reserved Qty
									</th>

									<th style="
										padding: 9px 10px;
										text-align: left;
										border-bottom: 1px solid #e2e8f0;
										color: #475569;
									">
										Warehouse
									</th>
								</tr>
							</thead>

							<tbody>
				`;

				rows.forEach((row, index) => {
					const bg = index % 2 === 0 ? "#ffffff" : "#fbfdff";

					html += `
						<tr style="background: ${bg};">

							<td style="
								padding: 9px 10px;
								border-bottom: 1px solid #eef2f6;
								color: #475569;
							">
								${frappe.utils.escape_html(row.sales_order || "")}
							</td>

							<td style="
								padding: 9px 10px;
								border-bottom: 1px solid #eef2f6;
								color: #475569;
							">
								${frappe.utils.escape_html(row.item_code || "")}
							</td>

							<td style="
								padding: 9px 10px;
								border-bottom: 1px solid #eef2f6;
							">
								<span style="
									background: #eff6ff;
									color: #2563eb;
									padding: 3px 8px;
									border-radius: 4px;
									font-weight: 600;
									font-size: 12px;
									border: 1px solid #dbeafe;
								">
									${frappe.utils.escape_html(row.batch_no || "")}
								</span>
							</td>

							<td style="
								padding: 9px 10px;
								border-bottom: 1px solid #eef2f6;
								text-align: right;
							">
								<span style="
									background: #f0fdf4;
									color: #15803d;
									padding: 3px 8px;
									border-radius: 4px;
									font-weight: 600;
									border: 1px solid #dcfce7;
								">
									${row.reserved_qty || 0}
								</span>
							</td>

							<td style="
								padding: 9px 10px;
								border-bottom: 1px solid #eef2f6;
								color: #64748b;
							">
								${frappe.utils.escape_html(row.warehouse || "")}
							</td>

						</tr>
					`;
				});

				html += `
							</tbody>
						</table>
					</div>
				`;
			}

			frm.fields_dict.batch_reserved.$wrapper.html(html);
		}
	});
},
	fetch_sales_order_details(frm) {
		// Collect filter values from the form
		const filters = {
			customer: frm.doc.customer,
			sales_order: frm.doc.sales_order,
			item_name: frm.doc.item_name,
			customer_po_no: frm.doc.customer_po_no,
			sales_order_date: frm.doc.sales_order_date,
		};

		// Check if at least one filter is provided
		const has_filters = Object.values(filters).some(val => val);
		if (!has_filters) {
			frappe.msgprint(__("Please provide at least one filter to fetch Sales Order items."));
			return;
		}

		frappe.call({
			method: "madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool.fetch_sales_order_items",
			args: {
				filters: filters
			},
			freeze: true,
			freeze_message: __("Fetching Sales Order items..."),
			callback: function (r) {
				if (r.message) {
					if (r.message.length === 0) {
						frappe.msgprint(__("No pending Sales Order items found matching the filters."));
						return;
					}

					// Clear existing rows
					frm.clear_table("pending_line_items");

					// Add fetched items to the child table
					r.message.forEach(item => {
						let row = frm.add_child("pending_line_items");
						row.sales_order = item.sales_order;
						row.sales_order_item = item.sales_order_item;
						row.item_code = item.item_code;
						row.item_name = item.item_name;
						row.qty = item.qty;
						row.pending_qty = item.pending_qty;
						row.pieces = item.pieces;
						row.length = item.length;
						row.section_weight = item.section_weight;
						row.reserve_qty = item.reserve_qty;
					});

					frm.refresh_field("pending_line_items");
					frappe.show_alert({
						message: __("{0} Sales Order items fetched successfully.", [r.message.length]),
						indicator: "green"
					});
				}
			}
		});
	},
});

frappe.ui.form.on("Sales Order Pending Line Items", {
	fetch_batch(frm, cdt, cdn) {
		const row = locals[cdt][cdn];

		if (!row.item_code) {
			frappe.msgprint(__("Please select an Item Code first."));
			return;
		}

		const parent = frm.doc;
		const warehouse = parent.warehouse;

		if (!warehouse) {
			frappe.msgprint(__("Please select a Warehouse in the main form."));
			return;
		}

		frappe.call({
			method: "madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool.fetch_available_batches",
			args: {
				item_code: row.item_code,
				warehouse: warehouse,
				pending_qty: row.pending_qty || row.qty,
				reserve_qty: row.reserve_qty || 0
			},
			freeze: true,
			freeze_message: __("Fetching available batches..."),
			callback: function (r) {
				if (!r.message || !r.message.length) {
					frappe.msgprint(
						__("No available batches found for {0} in warehouse {1}.", [row.item_code, warehouse])
					);
					return;
				}

				frm.clear_table("available_batches");

				r.message.forEach(batch => {
					let d = frm.add_child("available_batches");

					d.batch = batch.batch;
					d.item_code = batch.item_code;
					d.pieces = batch.pieces;
					d.length = batch.length;
					d.section_weight = batch.section_weight;
					d.available_qty = batch.available_qty;
					// store the pending row reference for exact matching in Reserve
					d.sales_order_item = row.sales_order_item;
				});

				frm.refresh_field("available_batches");

				frappe.show_alert({
					message: __("{0} available batches fetched successfully.", [r.message.length]),
					indicator: "green"
				});
			}
		});
	}
});

frappe.ui.form.on("Available Stock Batches", {
	async reserve(frm, cdt, cdn) {
		let batch = locals[cdt][cdn];

		if (!frm.doc.pending_line_items || !frm.doc.pending_line_items.length) {
			frappe.msgprint(__("No pending line items found. Please fetch Sales Order items first."));
			return;
		}

		// Match pending line by sales_order_item (exact), fallback to item_code
		let pending = frm.doc.pending_line_items.find(r =>
			batch.sales_order_item
				? r.sales_order_item == batch.sales_order_item
				: r.item_code == batch.item_code
		);

		if (!pending) {
			frappe.msgprint(__("No matching pending line found for item {0}.", [batch.item_code]));
			return;
		}

		if (!frm.doc.warehouse) {
			frappe.msgprint(__("Please select a Warehouse in the main form before reserving."));
			return;
		}

		const tolerance_warehouse = await madhav_load_tolerance_warehouse(frm);

		frappe.call({
			method: "madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool.add_to_reservation_batches",
			args: {
				docname: frm.doc.name,
				// from pending_line_items
				sales_order: pending.sales_order || frm.doc.sales_order || "",
				sales_order_item: pending.sales_order_item,
				sales_order_item_qty: pending.qty,
				item_code: pending.item_code,
				item_name: pending.item_name,
				// from available_batches
				batch_no: batch.batch,
				reserved_qty: batch.available_qty,
				length: batch.length,
				pieces: batch.pieces,
				section_weight: batch.section_weight,
				// from form header
				warehouse: frm.doc.warehouse,
				posting_date: frm.doc.posting_date,
				is_tolerance:
					tolerance_warehouse && frm.doc.warehouse === tolerance_warehouse
						? 1
						: 0,
			},
			freeze: true,
			freeze_message: __("Adding batch to reservations..."),
			callback: function (r) {
				if (r.message) {
					frappe.show_alert({
						message: __("Batch {0} added to reservations.", [batch.batch]),
						indicator: "green"
					});
					frm.reload_doc();
				}
			}
		});
	}
});

function madhav_load_tolerance_warehouse(frm) {
	if (frm.__tolerance_warehouse_promise) {
		return frm.__tolerance_warehouse_promise;
	}
	frm.__tolerance_warehouse_promise = frappe
		.call({
			method:
				"madhav.madhav.doctype.batch_wise_reservation_tool.batch_wise_reservation_tool.get_tolerance_warehouse_api",
		})
		.then((r) => r.message || null);
	return frm.__tolerance_warehouse_promise;
}
