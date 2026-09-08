frappe.ui.form.on("Delivery Note", {
	refresh(frm) {
		if (!should_customize_sales_order_picker(frm)) return;
		setTimeout(() => {
			frm.remove_custom_button(__("Sales Order"), __("Get Items From"));
			add_sales_order_button_with_item_reference(frm);
		}, 0);
		set_lengthpieces(frm);
	},
	onload(frm) {
		if (frm.doc.__islocal && Array.isArray(frm.doc.items)) {
			(frm.doc.items || []).forEach((row) => {
				// `so_detail` is the link to the Sales Order Item row
				if (row.so_detail && (!row.lengthpieces_so || !row.length_sizeso)) {
					frappe.call({
						method: "madhav.api.get_so_item_pieces_and_length",
						args: {
							so_detail: row.so_detail,
						},
						callback: (r) => {
							if (!r.message) return;

							frappe.model.set_value(
								row.doctype,
								row.name,
								"lengthpieces_so",
								r.message.pieces
							);
							frappe.model.set_value(
								row.doctype,
								row.name,
								"length_sizeso",
								r.message.length_size
							);
							frappe.model.set_value(
								row.doctype,
								row.name,
								"quantityso",
								r.message.qty
							);
						},
					});
				}
			});
		}
	},
	onload_post_render: function (frm) {
		set_lengthpieces(frm);
	},

	// before_save: function(frm) {
	//     update_totals(frm);
	// },
});
frappe.ui.form.on('Delivery Note Item', {
	batch_no(frm, cdt, cdn) {
		let d = locals[cdt][cdn];

		// Bundle rows carry batches on Serial/Batch Bundle entries only.
		if (!d.batch_no || d.serial_and_batch_bundle) return;

		// fetch pieces from Batch only once
		frappe.db.get_value(
			"Batch",
			d.batch_no,
			"pieces",
			function (r) {
				if (r && r.pieces != null) {
					frappe.model.set_value(
						cdt,
						cdn,
						"pieces",
						r.pieces
					);
				}
			}
		);
	},
	item_code(frm, cdt, cdn) {
		let d = locals[cdt][cdn];
		if (!d.item_code) return;

		frappe.db.get_value("Item", d.item_code, "weight_per_meter", (r) => {
			if (r && r.weight_per_meter) {
				frappe.model.set_value(cdt, cdn, "section_weight", r.weight_per_meter);
				calculate_qty(cdt, cdn);
			}
		});
	},
	pieces(frm, cdt, cdn) {
		calculate_qty(cdt, cdn);
	},
	average_length(frm, cdt, cdn) {
		calculate_qty(cdt, cdn);
	},
	section_weight(frm, cdt, cdn) {
		calculate_qty(cdt, cdn);
	}
});

function calculate_qty(cdt, cdn) {
	let row = locals[cdt][cdn];
	// Reserved / Deliver-as-Qty rows: physical Qty comes from reservation weight.
	// Do not rebuild Qty from pieces×length×section_weight (that drifts 0.445→0.443).
	if (
		(row.against_sales_order || row.against_sales_invoice) &&
		(cint(row.custom_deliver_as_qty) || row.serial_and_batch_bundle)
	) {
		return;
	}

	let pieces = flt(row.pieces);
	let avg_len = flt(row.average_length || row.length_size);
	let section_weight = flt(row.section_weight);

	let qty = (pieces * avg_len * section_weight) / 1000;

	if (qty > 0) {
		frappe.model.set_value(cdt, cdn, "qty", flt(qty, 4));
	}
}

function set_lengthpieces(frm) {
	(frm.doc.items || []).forEach(row => {
		if (row.pieces && !row.lengthpieces_so) {
			row.lengthpieces_so = row.pieces;
			row.average_length = row.length_size;
		}
	});
	frm.refresh_field("items");
}

function add_sales_order_button_with_item_reference(frm) {
	frm.add_custom_button(
		__("Sales Order"),
		function () {
			open_sales_order_items_selector_for_delivery_note(frm);
		},
		__("Get Items From")
	);
}

function should_customize_sales_order_picker(frm) {
	return (
		!frm.doc.is_return &&
		(frm.doc.status !== "Closed" || frm.is_new()) &&
		frm.has_perm("write") &&
		frappe.model.can_read("Sales Order") &&
		frm.doc.docstatus === 0
	);
}



function open_sales_order_items_selector_for_delivery_note(frm) {
	const filters = {
		docstatus: 1,
		status: ["not in", ["Closed", "On Hold"]],
		per_delivered: ["<", 99.99],
		company: frm.doc.company,
		customer: frm.doc.customer,
	};

	const state = {
		rows: [],
		selected_children: new Set(),
	};

	const d = new frappe.ui.Dialog({
		title: __("Select Sales Order Items"),
		size: "extra-large",
		fields: [
			{
				fieldtype: "Link",
				fieldname: "sales_order_filter",
				label: __("Name"),
				options: "Sales Order",
			},
			{
				fieldtype: "Column Break",
			},
			{
				fieldtype: "Link",
				fieldname: "customer_filter",
				label: __("Customer"),
				options: "Customer",
				default: frm.doc.customer || "",
			},
			{
				fieldtype: "Column Break",
			},
			{
				fieldtype: "Data",
				fieldname: "item_filter",
				label: __("Item Name"),
			},
			{
				fieldtype: "Section Break",
			},
			{
				fieldtype: "Data",
				fieldname: "po_no",
				label: __("Customer Po No."),
			},
			{
				fieldtype: "Column Break",
			},
			{
				fieldtype: "Select",
				fieldname: "assorted_length",
				label: __("Assorted Length"),
				options: ["", "T/L", "F/L"],
			},
			{
				fieldtype: "Section Break",
			},
			{
				fieldtype: "Float",
				fieldname: "min_length",
				label: __("Min Length"),
			},
			{
				fieldtype: "Column Break",
			},
			{
				fieldtype: "Float",
				fieldname: "max_length",
				label: __("Max Length"),
			},
			{
				fieldtype: "Section Break",
			},
			{
				fieldtype: "Check",
				fieldname: "show_items",
				label: __("Select Sales Order Items"),
				default: 1,
			},
			{
				fieldtype: "Column Break",
			},
			{
				fieldtype: "Check",
				fieldname: "show_all_items",
				label: __("Show All Items"),
				default: 0,
			},
			{
				fieldtype: "Section Break",
			},
			{
				fieldtype: "HTML",
				fieldname: "filter_area",
			},
			{
				fieldtype: "Section Break",
			},
			{
				fieldtype: "HTML",
				fieldname: "items_html",
			},
		],
		primary_action_label: __("Get Selected Items"),
		primary_action: () => {
			const filtered_children = Array.from(state.selected_children);
			if (!filtered_children.length) {
				frappe.msgprint(__("Please select items to fetch."));
				return;
			}

			// Get unique parent SOs for selected items
			const source_names = [
				...new Set(
					state.rows
						.filter((row) => state.selected_children.has(row.name))
						.map((row) => row.parent)
				),
			];

			d.hide();

			if (Array.isArray(frm.doc.items) && frm.doc.items.length && !frm.doc.items[0].item_code) {
				frm.doc.items.splice(0, 1);
			}

			// ✅ Loop over all source SOs
			const calls = source_names.map(so_name => {
				return frappe.call({
					method: "madhav.doc_events.delivery_note.make_delivery_note_custom",
					args: {
						source_name: so_name,
						kwargs: {
							selected_sre: filtered_children,  // SO Item row names
							for_reserved_stock: true,
						},
					},
				});
			});

			Promise.all(calls.map(c => c.then ? c : Promise.resolve(c))).then(results => {
				results.forEach(r => {
					if (r && r.message && r.message.items) {
						r.message.items.forEach(item => {
							const new_row = frm.add_child("items");
							delete item.idx;   // ✅ remove server idx so Frappe assigns correct one
							delete item.name;  // ✅ remove server name to avoid conflicts
							Object.assign(new_row, item);
							Object.assign(new_row, item);
						});
					}
				});
				frm.refresh_field("items");
				frappe.model.sync && frappe.model.sync(results[0]?.message);
				frm.refresh();
			});
		},
	});

	d.show();

	frappe.model.with_doctype("Sales Order", () => {
		const filter_group = new frappe.ui.FilterGroup({
			parent: d.get_field("filter_area").$wrapper,
			doctype: "Sales Order",
			on_change: () => load_rows(),
		});

		// Fix for filter removal not triggering on_change correctly in some environments
		d.get_field("filter_area").$wrapper.on("click", ".remove-filter, .clear-filters", () => {
			setTimeout(() => {
				filter_group.update_filters();
				load_rows();
			}, 100);
		});


		d.get_field("sales_order_filter").$input.on(
			"change input awesomplete-selectcomplete",
			frappe.utils.debounce(() => render_rows(), 300)
		);
		d.get_field("item_filter").$input.on(
			"change input awesomplete-selectcomplete",
			frappe.utils.debounce(() => render_rows(), 300)
		);
		d.get_field("customer_filter").$input.on(
			"change input awesomplete-selectcomplete",
			frappe.utils.debounce(() => render_rows(), 300)
		);
		d.get_field("po_no").$input.on("change input", frappe.utils.debounce(() => load_rows(), 400));
		d.get_field("assorted_length").$input.on("change", () => render_rows());
		d.get_field("min_length").$input.on("input", () => render_rows());
		d.get_field("max_length").$input.on("input", () => render_rows());
		d.get_field("show_items").$input.on("change", () => render_rows());
		d.get_field("show_all_items").$input.on("change", () => render_rows());

		load_rows();

		// Expose for debugging if needed
		d.filter_group = filter_group;

		function load_rows() {
			const wrapper = d.get_field("items_html").$wrapper;
			wrapper.html(`<div class="text-muted" style="padding: 8px 0;">${__("Loading Sales Order items...")}</div>`);

			const dynamic_filters = filter_group.get_filters();
			const po_no = d.get_value("po_no");
			const current_filters = {
				...filters,
				dynamic_filters: filter_group.get_filters() || [],
				 ...(po_no ? { po_no } : {}),  
			};

			frappe.call({
				method: "madhav.doc_events.delivery_note.get_sales_order_items_for_selector",
				args: { filters: current_filters },
				callback: (r) => {
					state.rows = (r && r.message) || [];
					render_rows();
				},
			});
		}
	});

	return d;

	function render_rows() {
		const show_items = d.get_value("show_items");
		const show_all_items = d.get_value("show_all_items");
		const item_filter = d.get_value("item_filter");
		const customer_filter = d.get_value("customer_filter");
		const min_length = flt(d.get_value("min_length"));
		const max_length = flt(d.get_value("max_length"));
		const so_filter = d.get_value("sales_order_filter");
		const po_no = d.get_value("po_no");
		const assorted_length = d.get_value("assorted_length");

		let filtered_rows = state.rows.filter((row) => {
			if (!show_all_items && flt(row.reserved_qty) <= 0 ) return false;
			if (customer_filter && row.customer !== customer_filter) return false;
			if (assorted_length && row.assorted_length !== assorted_length) return false;  // ← add
			if (item_filter && !(
				(row.item_code || "").toLowerCase().includes(item_filter.toLowerCase()) ||
				(row.item_name || "").toLowerCase().includes(item_filter.toLowerCase())
			)) return false;

			const row_length = flt(row.length_size !== undefined ? row.length_size : row.length);
			if (min_length > 0 || max_length > 0) {
				if (row_length <= 0) return false; // hide rows with no length when filter is active
				if (min_length > 0 && row_length < min_length) return false;
				if (max_length > 0 && row_length > max_length) return false;
			}
			if (so_filter && row.parent !== so_filter) return false;
			return true;
		});

		const wrapper = d.get_field("items_html").$wrapper;

		if (!filtered_rows.length) {
			wrapper.html(`<div class="text-muted" style="padding:8px 0;">${__("No data found.")}</div>`);
			return;
		}

		let html = "";
		if (show_items) {
			html = render_items_table(filtered_rows);
		} else {
			html = render_sos_table(filtered_rows);
		}

		wrapper.html(html);
		setup_table_events(wrapper, show_items, filtered_rows);
	}

	function render_items_table(rows) {
		let last_so = null;
		let color_band = 0;

		const rows_html = rows.map((row) => {
			if (row.parent !== last_so) {
				if (last_so !== null) color_band = color_band ? 0 : 1;
				last_so = row.parent;
			}
			const bg_color = color_band ? "#f0faff" : "#fff";
			const checked = state.selected_children.has(row.name) ? "checked" : "";

			return `
				<tr style="background-color:${bg_color}">
					<td><input type="checkbox" class="selector-check" data-name="${frappe.utils.escape_html(row.name)}" ${checked}></td>
					<td>${frappe.utils.escape_html(row.parent || "")}</td>
					<td>${frappe.utils.escape_html(row.transaction_date || "")}</td>
					<td>${frappe.utils.escape_html(row.item_code || "")}</td>
					<td>${frappe.utils.escape_html(row.item_name || "")}</td>
					<td class="text-right">${format_number(flt(row.qty || 0))}</td>
					<td class="text-right">${format_number(flt(row.length_size ?? row.length ?? 0))}</td>
					<td class="text-right">${format_number(flt(row.pieces || 0))}</td>
					<td class="text-right">${format_number(flt(row.reserved_qty || 0))}</td>
					<td class="text-right">${format_number(flt(row.section_weight || 0))}</td>
					<td>${frappe.utils.escape_html(row.po_no || "")}</td>
        			<td>${frappe.utils.escape_html(row.assorted_length || "")}</td>
				</tr>`;
		}).join("");

		const filter_input = (col) =>
			`<td style="padding:4px;"><input type="text" class="col-filter" data-col="${col}" placeholder="Search..." style="width:100%;padding:3px 6px;border:1px solid #ccc;border-radius:4px;font-size:12px;box-sizing:border-box;"></td>`;

		const filter_number_input = (col) =>
			`<td style="padding:4px;"><input type="text" class="col-filter col-filter-number" data-col="${col}" placeholder="Search..." style="width:100%;padding:3px 6px;border:1px solid #ccc;border-radius:4px;font-size:12px;box-sizing:border-box;text-align:right;"></td>`;

		return `
			${get_table_header(rows.length)}
			<div style="max-height:420px;overflow:auto;border:1px solid var(--border-color);border-radius:6px;">
				<table class="table table-bordered" style="margin-bottom:0;min-width:1200px;">
					<thead style="position:sticky;top:0;z-index:1;background-color:#fff;">
						<tr>
							<th style="width:40px;"></th>
							<th>${__("Sales Order")}</th>
							<th>${__("Date")}</th>
							<th>${__("Item Code")}</th>
							<th>${__("Item Name")}</th>
							<th class="text-right">${__("SO Qty")}</th>
							<th class="text-right">${__("Length")}</th>
							<th class="text-right">${__("Pieces")}</th>
							<th class="text-right">${__("Reserved Qty")}</th>
							<th class="text-right">${__("Section Weight")}</th>
							<th>${__("PO No")}</th>
    						<th>${__("Assorted Length")}</th>
						</tr>
						<tr style="background-color:#f5f5f5;position:sticky;top:37px;z-index:1;">
							<td style="padding:4px;"></td>
							${filter_input("parent")}
							${filter_input("transaction_date")}
							${filter_input("item_code")}
							${filter_input("item_name")}
							${filter_number_input("qty")}
							${filter_number_input("length_size")}
							${filter_number_input("pieces")}
							${filter_number_input("reserved_qty")}
							${filter_number_input("section_weight")}
							${filter_input("po_no")}
    						${filter_input("assorted_length")}
						</tr>
					</thead>
					<tbody>${rows_html}</tbody>
				</table>
			</div>`;
	}

	function render_sos_table(rows) {
		const grouped = {};
		rows.forEach(row => {
			if (!grouped[row.parent]) {
				grouped[row.parent] = {
					name: row.parent,
					date: row.transaction_date,
					customer: row.customer,
					items: []
				};
			}
			grouped[row.parent].items.push(row);
		});

		const sos_html = Object.values(grouped).map(so => {
			const all_selected = so.items.every(item => state.selected_children.has(item.name));
			const checked = all_selected ? "checked" : "";

			return `
				<tr>
					<td><input type="checkbox" class="so-selector-check" data-parent="${frappe.utils.escape_html(so.name)}" ${checked}></td>
					<td>${frappe.utils.escape_html(so.date || "")}</td>
					<td>${frappe.utils.escape_html(so.name || "")}</td>
				</tr>`;
		}).join("");

		return `
			${get_table_header(Object.keys(grouped).length)}
			<div style="max-height:420px;overflow:auto;border:1px solid var(--border-color);border-radius:6px;">
				<table class="table table-bordered" style="margin-bottom:0;">
					<thead>
						<tr>
							<th style="width:40px;"></th>
							<th>${__("Date")}</th>
							<th>${__("Sales Order")}</th>
						</tr>
					</thead>
					<tbody>${sos_html}</tbody>
				</table>
			</div>`;
	}

	function get_table_header(row_count) {
		return `
			<div style="display:flex;justify-content:space-between;align-items:center;margin:8px 0;">
				<label style="margin:0;display:flex;align-items:center;gap:6px;">
					<input type="checkbox" class="check-all-visible">
					${__("Select all visible")}
				</label>
				<div class="text-muted">${__("Rows")}: ${row_count || ""}</div>
			</div>`;
	}

	// function setup_table_events(wrapper, show_items, rows) {
	// 	if (show_items) {
	// 		wrapper.find(".selector-check").on("change", function () {
	// 			const row_name = this.getAttribute("data-name");
	// 			if (this.checked) state.selected_children.add(row_name);
	// 			else state.selected_children.delete(row_name);
	// 		});
	// 	} else {
	// 		wrapper.find(".so-selector-check").on("change", function () {
	// 			const parent = this.getAttribute("data-parent");
	// 			const so_items = rows.filter(r => r.parent === parent);
	// 			so_items.forEach(item => {
	// 				if (this.checked) state.selected_children.add(item.name);
	// 				else state.selected_children.delete(item.name);
	// 			});
	// 		});
	// 	}

	// 	wrapper.find(".check-all-visible").on("change", function () {
	// 		const checked = this.checked;
	// 		const selector = show_items ? ".selector-check" : ".so-selector-check";
	// 		wrapper.find(selector).each(function () {
	// 			this.checked = checked;
	// 			$(this).trigger("change");
	// 		});
	// 	});
	// }
	function setup_table_events(wrapper, show_items, rows) {
		if (show_items) {
			wrapper.find(".selector-check").on("change", function () {
				const row_name = this.getAttribute("data-name");
				if (this.checked) state.selected_children.add(row_name);
				else state.selected_children.delete(row_name);
			});

			wrapper.find(".col-filter").on("input", function () {
				const col_filters = {};
				wrapper.find(".col-filter").each(function () {
					const col = this.getAttribute("data-col");
					const val = this.value.toLowerCase().trim();
					if (val) col_filters[col] = val;
				});

				wrapper.find("tbody tr").each(function () {
					const $row = $(this);
					const row_name = $row.find(".selector-check").data("name");
					const row_data = rows.find(r => r.name === row_name);
					if (!row_data) return;

					const visible = Object.entries(col_filters).every(([col, val]) => {
						// for number columns: get the actual value, handle length_size alias
						let cell_val;
						if (col === "length_size") {
							cell_val = String(flt(row_data.length_size ?? row_data.length ?? 0));
						} else {
							cell_val = String(row_data[col] !== undefined ? row_data[col] : "").toLowerCase();
						}
						return cell_val.includes(val);
					});

					$row.toggle(visible);
				});
			});

		} else {
			wrapper.find(".so-selector-check").on("change", function () {
				const parent = this.getAttribute("data-parent");
				const so_items = rows.filter(r => r.parent === parent);
				so_items.forEach(item => {
					if (this.checked) state.selected_children.add(item.name);
					else state.selected_children.delete(item.name);
				});
			});
		}

		wrapper.find(".check-all-visible").on("change", function () {
			const checked = this.checked;
			const selector = show_items ? ".selector-check" : ".so-selector-check";
			wrapper.find(selector).each(function () {
				this.checked = checked;
				$(this).trigger("change");
			});
		});
	}
}