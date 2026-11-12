// Copyright (c) 2026, Finbyz pvt. ltd. and contributors
// For license information, please see license.txt

const PT_METHOD = "madhav.madhav.doctype.party_transfer.party_transfer";

frappe.ui.form.on("Party Transfer", {
	setup(frm) {
		frm.set_query("to_sales_order", () => ({
			filters: {
				docstatus: 1,
				status: ["not in", ["Closed", "Completed", "On Hold"]],
			},
		}));
	},

	refresh(frm) {
		if (frm.doc.docstatus === 0 && frm.doc.to_sales_order) {
			// Opened from a Sales Order: pick its line straight away if it has one.
			party_transfer_load_lines(frm, !frm.doc.to_sales_order_item);
		}
		frm.set_df_property("to_line", "read_only", frm.doc.docstatus !== 0);

		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(__("Reduce Work Orders"), () => {
				frappe.confirm(
					__(
						"Cancel Not Started Work Orders of {0} by the quantity it no longer needs, and remake them smaller where something is still needed?",
						[frm.doc.to_sales_order]
					),
					() => frm.call("reduce_work_orders").then(() => frm.reload_doc())
				);
			});
			frm.add_custom_button(__("Party Transfer Register"), () => {
				frappe.set_route("query-report", "Party Transfer Register", {
					party_transfer: frm.doc.name,
				});
			});
		}
	},

	to_sales_order(frm) {
		frm.set_value("to_line", "");
		frm.set_value("to_sales_order_item", "");
		frm.clear_table("items");
		frm.refresh_field("items");
		if (frm.doc.to_sales_order) {
			party_transfer_load_lines(frm, true);
		}
	},

	to_line(frm) {
		const map = frm.__pt_line_map || {};
		const so_item = map[frm.doc.to_line] || "";
		if (so_item === frm.doc.to_sales_order_item && frm.doc.items?.length) {
			return;
		}
		frm.set_value("to_sales_order_item", so_item);
		party_transfer_load_reservations(frm);
	},
});

frappe.ui.form.on("Party Transfer Item", {
	transfer_qty(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const qty = flt(row.transfer_qty);
		if (qty > flt(row.available_qty) + 0.0005) {
			frappe.msgprint(
				__("Row {0}: only {1} is reserved on {2}.", [row.idx, row.available_qty, row.from_sales_order])
			);
			frappe.model.set_value(cdt, cdn, "transfer_qty", row.available_qty);
			return;
		}
		let pieces = 0;
		if (flt(row.available_qty) > 0 && flt(row.available_pieces) > 0) {
			pieces =
				qty + 0.0005 >= flt(row.available_qty)
					? flt(row.available_pieces)
					: Math.round((flt(row.available_pieces) * qty) / flt(row.available_qty));
		}
		frappe.model.set_value(cdt, cdn, "transfer_pieces", pieces);
		party_transfer_set_totals(frm);
	},
	transfer_pieces(frm) {
		party_transfer_set_totals(frm);
	},
});

function party_transfer_set_totals(frm) {
	let qty = 0;
	let pieces = 0;
	(frm.doc.items || []).forEach((r) => {
		qty += flt(r.transfer_qty);
		pieces += flt(r.transfer_pieces);
	});
	frm.set_value("total_qty", flt(qty, 3));
	frm.set_value("total_pieces", pieces);
}

function party_transfer_load_lines(frm, auto_pick) {
	return frappe
		.call({ method: `${PT_METHOD}.get_order_lines`, args: { sales_order: frm.doc.to_sales_order } })
		.then((r) => {
			const lines = r.message || [];
			const map = {};
			const options = [""];
			lines.forEach((l) => {
				const pending = flt(l.stock_qty) - flt(l.delivered_qty) * (flt(l.conversion_factor) || 1);
				// Stored in a Select (max 140 chars), so keep it short.
				const label = __("#{0} {1} | pending {2} | reserved {3}{4}", [
					l.idx,
					l.item_code,
					flt(pending, 3),
					flt(l.stock_reserved_qty, 3),
					l.length_size ? __(" | len {0}", [l.length_size]) : "",
				]).slice(0, 140);
				map[label] = l.name;
				options.push(label);
			});
			frm.__pt_line_map = map;
			frm.set_df_property("to_line", "options", options.join("\n"));

			// Keep the current pick, or pick the only line / the routed line.
			const current = Object.keys(map).find((k) => map[k] === frm.doc.to_sales_order_item);
			if (current) {
				frm.doc.to_line = current;
				frm.refresh_field("to_line");
			} else if (auto_pick && lines.length === 1) {
				frm.set_value("to_line", options[1]);
			}
		});
}

function party_transfer_load_reservations(frm) {
	frm.clear_table("items");
	if (!frm.doc.to_sales_order_item) {
		frm.refresh_field("items");
		return;
	}
	frappe
		.call({
			method: `${PT_METHOD}.get_available_reservations`,
			args: {
				to_sales_order: frm.doc.to_sales_order,
				to_sales_order_item: frm.doc.to_sales_order_item,
			},
			freeze: true,
			freeze_message: __("Finding reserved stock on other orders..."),
		})
		.then((r) => {
			const rows = r.message || [];
			let skipped = 0;
			rows.forEach((d) => {
				if (!d.length_ok) {
					skipped += 1;
					return;
				}
				const row = frm.add_child("items");
				Object.assign(row, d, { transfer_qty: 0, transfer_pieces: 0 });
				delete row.length_ok;
			});
			frm.refresh_field("items");
			party_transfer_set_totals(frm);
			if (!frm.doc.items.length) {
				frappe.msgprint(
					skipped
						? __("No reserved stock of this item fits the order's length. {0} reservation(s) were left out because their length is outside the allowed range.", [skipped])
						: __("No other order has this item reserved.")
				);
			} else if (skipped) {
				frappe.show_alert({
					message: __("{0} reservation(s) left out: length outside this order's range.", [skipped]),
					indicator: "orange",
				});
			}
		});
}
