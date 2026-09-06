import frappe
from frappe import _
from frappe.utils import get_url_to_form
from frappe.utils import nowdate, flt, cint, cstr, now_datetime

def on_cancel(self,method):
    if self.stock_entry_type == "Manufacture" and self.work_order:
        for row in self.items:
            if row.is_finished_item:
                doc = frappe.get_doc("Work Order",self.work_order)
                doc.db_set("completed_pcs",max(0, doc.completed_pcs - row.pieces))
                doc.db_set("pending_pcs",doc.pieces - doc.completed_pcs)
                doc.db_set("pending_qty",doc.qty - row.qty)

def validate(doc, method):
    """Recalculate rates/amounts safely for this app.

    - Uses standard ERPNext APIs when available
    - Falls back to proportional allocation for multiple finished rows
    """
    try:
        calculate_rate_and_amount(doc)
    except Exception:
        # Keep non-blocking if any upstream difference exists
        pass

def _get_row_quantity(row):
    """Return custom 'quantity' if present, else fall back to standard 'qty'."""
    return flt(getattr(row, "quantity", getattr(row, "qty", 0)))

# def _safe_get_item_field(item_code, fieldname, default=None):
# 	"""Safely read Item field; return default if column doesn't exist or value is None."""
# 	try:
# 		# frappe.db.has_column returns True/False; fallback to try/except if unavailable
# 		if hasattr(frappe.db, "has_column") and not frappe.db.has_column("Item", fieldname):
# 			return default
# 		val = frappe.db.get_value("Item", item_code, fieldname)
# 		return val if val is not None else default
# 	except Exception:
# 		return default
	
def calculate_rate_and_amount(doc, force=False, update_finished_item_rate=True, raise_error_if_no_rate=True):
    """Minimal, app-safe valuation calculation.

    - Uses standard StockEntry APIs when available
    - Handles multiple finished items by proportional allocation using a robust measure
    """
    # Ensure base rates are set by ERPNext (handles incoming/outgoing values and defaults)
    if hasattr(doc, "set_basic_rate"):
        doc.set_basic_rate(force, raise_error_if_no_rate=raise_error_if_no_rate)

    finished_rows = [r for r in (doc.items or []) if getattr(r, "t_warehouse", None) and flt(getattr(r, "qty", 0))]

    if doc.purpose in ["Manufacture", "Repack"] and len(finished_rows) > 1:
        calculate_multiple_repack_valuation(doc)
    else:
        # Default ERPNext cost distribution for single finished item
        if hasattr(doc, "distribute_additional_costs"):
            doc.distribute_additional_costs()

    # Final recomputations using ERPNext APIs
    if hasattr(doc, "update_valuation_rate"):
        doc.update_valuation_rate()
    if hasattr(doc, "set_total_incoming_outgoing_value"):
        doc.set_total_incoming_outgoing_value()
    if hasattr(doc, "set_total_amount"):
        doc.set_total_amount()

# def price_to_rate(self):
# 	for item in self.items:
# 		maintain_as_is_stock = _safe_get_item_field(item.item_code, 'maintain_as_is_stock', 0)
# 		concentration = item.concentration or 100	
# 		if item.basic_rate:
# 			if maintain_as_is_stock:
# 				item.price = flt(item.basic_rate)*100/concentration
# 			else:
# 				item.price = flt(item.basic_rate)

# def update_valuation_price(self):
# 	for item in self.items:
# 		maintain_as_is_stock = _safe_get_item_field(item.item_code, 'maintain_as_is_stock', 0)
# 		concentration = item.concentration or 100
# 		if maintain_as_is_stock:
# 			item.valuation_price = item.valuation_rate * 100 / concentration
# 		else:
# 			item.valuation_price = item.valuation_rate

def calculate_multiple_repack_valuation(doc):
    """Allocate outgoing value and additional costs proportionally across multiple finished rows.

    Measure defaults to `quantity` field if present else `qty` to support diverse schemas.
    """
    total_additional_costs = sum([flt(getattr(t, "amount", 0)) for t in (getattr(doc, "additional_costs", []) or [])])
    if not getattr(doc, "items", None):
        return

    # Compute totals
    total_outgoing_value = 0.0
    finished_measure_total = 0.0
    for row in doc.items:
        if getattr(row, "s_warehouse", None):
            total_outgoing_value += flt(getattr(row, "basic_amount", 0))
        if getattr(row, "t_warehouse", None):
            finished_measure_total += _get_row_quantity(row)

    if finished_measure_total <= 0:
        frappe.throw("Cannot allocate costs: total finished measure is zero.")

    # Allocate to finished rows
    for row in doc.items:
        if getattr(row, "t_warehouse", None):
            measure = _get_row_quantity(row)
            basic_amount = flt(total_outgoing_value) * flt(measure) / flt(finished_measure_total)
            additional_cost = flt(total_additional_costs) * flt(measure) / flt(finished_measure_total)
            row.basic_amount = basic_amount
            row.additional_cost = additional_cost
            qty_val = flt(getattr(row, "qty", 0))
            row.basic_rate = flt(basic_amount / qty_val) if qty_val else 0.0

# def cal_rate_for_finished_item(self):

# 	self.total_additional_costs = sum([flt(t.amount) for t in self.get("additional_costs")])
# 	work_order = frappe.get_doc("Work Order",self.work_order)
# 	bom_doc = frappe.get_doc("BOM",self.bom_no)
# 	is_multiple_finish = 0
# 	for d in self.items:
# 		if d.t_warehouse:
# 			is_multiple_finish +=1
# 	if is_multiple_finish > 1:
# 		# compute local total outgoing value to avoid relying on not-yet-set self.total_outgoing_value
# 		local_total_outgoing_value = 0.0
# 		for r in self.items:
# 			if r.s_warehouse:
# 				local_total_outgoing_value += flt(r.basic_amount)

# 		item_arr = list()
# 		item_map = dict()
# 		finished_list = []
# 		result = {}
# 		cal_yield = 0
# 		if self.purpose == 'Manufacture' and self.bom_no:
# 			for row in self.items:
# 				if row.t_warehouse:
# 					finished_list.append({row.item_code:_get_row_quantity(row)}) #create a list of dict of finished item
# 			for d in finished_list:
# 				for k in d.keys():
# 					result[k] = result.get(k, 0) + d[k] # create a dict of unique item 
						
# 			for d in self.items:
# 				if d.item_code not in item_arr:
# 					item_map.setdefault(d.item_code, {'quantity':0, 'qty':0, 'yield_weights':0})
				
# 				item_map[d.item_code]['quantity'] += _get_row_quantity(d)
# 				item_map[d.item_code]['qty'] += flt(d.qty)
# 				item_map[d.item_code]['yield_weights'] += flt(d.batch_yield) * _get_row_quantity(d)

# 				bom_cost_list = []
# 				if bom_doc.is_multiple_item:
# 					for bom_fi in bom_doc.multiple_finish_item:
# 						bom_cost_list.append({"item_code":bom_fi.item_code,"cost_ratio":bom_fi.cost_ratio})
# 				else:
# 					bom_cost_list.append({"item_code":bom_doc.item,"cost_ratio":100})
# 				if d.t_warehouse:
# 					for bom_cost_dict in bom_cost_list:
# 						if d.item_code == bom_cost_dict["item_code"]:
# 							measure = _get_row_quantity(d)
# 							den = flt(100 * flt(result.get(d.item_code) or 0))
# 							if not den:
# 								frappe.throw(f"Cannot allocate costs for item {d.item_code}: zero denominator.")
# 							d.db_set('basic_amount', flt(flt(local_total_outgoing_value * bom_cost_dict["cost_ratio"] * measure) / den))
# 							d.db_set('additional_cost', flt(flt(self.total_additional_costs * bom_cost_dict["cost_ratio"] * measure) / den))
# 							d.db_set('amount',flt(d.basic_amount + d.additional_cost))
# 							d.db_set('basic_rate', flt(d.basic_amount / flt(d.qty)) if flt(d.qty) else 0.0)
# 							d.db_set('valuation_rate', flt(d.amount / flt(d.qty)) if flt(d.qty) else 0.0)

# 					item_yield = 0.0
# 					if self.based_on and item_map.get(self.based_on) and item_map[self.based_on]['quantity'] > 0:
# 						item_yield = item_map[self.based_on]['yield_weights'] / item_map[self.based_on]['quantity']

# 					based_on_qty_ratio = _get_row_quantity(d) / (self.fg_completed_quantity or self.fg_completed_qty or 1)
# 					if self.based_on:
# 						# if item_yield:
# 						# 	d.batch_yield = flt((d.qty * d.concentration * item_yield) / (100*flt(item_map[self.based_on]['quantity']*finish_items.bom_qty_ratio/100)))
# 						# else:
# 						d.batch_yield = flt((d.qty * d.concentration) / (100*flt((item_map[self.based_on]['quantity'] or 1)*flt(based_on_qty_ratio)/100)))

# 					# total_incoming_amount += flt(d.amount)

# 			d.db_update()

					# first_item_ratio = abs(100-self.cost_ratio_of_second_item)
					# first_item_qty_ratio = abs(100-self.qty_ratio_of_second_item)
					
					# if d.item_code == frappe.db.get_value('Work Order',self.work_order,'production_item'):
					# 	d.db_set('basic_amount',flt(flt(self.total_outgoing_value*first_item_ratio*d.quantity)/flt(100*result[d.item_code])))
					# 	d.db_set('additional_cost',flt(flt(self.total_additional_costs*first_item_ratio*d.quantity)/flt(100*result[d.item_code])))
					# 	d.db_set('amount',flt(d.basic_amount + d.additional_cost))
					# 	d.db_set('basic_rate',flt(d.basic_amount/ d.qty))
					# 	d.db_set('valuation_rate',flt(d.amount/ d.qty))

					# 	if self.based_on:
					# 		d.batch_yield = flt(result[d.item_code] / flt(item_map[self.based_on]*first_item_qty_ratio/100))
						
					# if d.item_code == self.second_item:
					# 	d.db_set('basic_amount',flt(flt(self.total_outgoing_value*self.cost_ratio_of_second_item*d.quantity)/flt(100*result[d.item_code])))
					# 	d.db_set('additional_cost',flt(flt(self.total_additional_costs*self.cost_ratio_of_second_item*d.quantity)/flt(100*result[d.item_code])))
					# 	d.db_set('amount',flt(d.basic_amount + d.additional_cost))
					# 	d.db_set('basic_rate',flt(d.basic_amount/ d.qty))
					# 	d.db_set('valuation_rate',flt(d.amount/ d.qty))
						
					# 	if self.based_on:
					# 		d.batch_yield = flt(result[d.item_code] / flt(item_map[self.based_on]*self.qty_ratio_of_second_item/100))  # cost_ratio_of_second_item percent of sum of items of based_on item from map variable

def after_submit(doc,method):
    set_custom_supplier_from_batch(doc)

    if doc.stock_entry_type == "Material Receipt":
        create_batch_group(doc)
        
    if doc.stock_entry_type == "Material Transfer" and doc.get('cutting_plan_reference'):
        update_cutting_plan_workflow(doc.cutting_plan_reference, doc.name)
        update_source_warehouse(doc.cutting_plan_reference, doc.name)
        
def create_batch_group(doc):
    # Find all batches linked to this PR
    batch_list = frappe.get_all(
        "Batch",
        filters={
            "reference_doctype": "Stock Entry",
            "reference_name": doc.name
        },
        fields=["name","pieces","weight_received","average_length","section_weight"]
    )

    if not batch_list:
        frappe.msgprint("No batches found to group.")
        return

    # Create Batch Group
    batch_group = frappe.new_doc("Batch Group")
    batch_group.reference_doctype = "Stock Entry"
    batch_group.reference_document_name = doc.name
    batch_group.total_length_in_meter = doc.total_length_in_meter
    weight_received_kg = doc.weight_received * 1000
    batch_group.section_weight = round(weight_received_kg/doc.total_length_in_meter, 2)
    batch_group.section_weight = round(batch_group.section_weight/39.37, 2)

    for batch in batch_list:
        batch_group.append("batch_details", {
            "batch": batch.name,
            "lengthpieces": batch.pieces,
            "weight_received": batch.weight_received,
            "length_size": batch.average_length,
            "section_weight": batch.section_weight,
        })

    batch_group.save()
    for batch in batch_list:
        frappe.db.set_value("Batch", batch.name, "batch_group_reference", batch_group.name)

    
    frappe.msgprint(f"✅ Batch Group <a href='/app/batch-group/{batch_group.name}' target='_blank'><b>{batch_group.name}</b></a> created with {len(batch_list)} batches.")
    
def auto_calculation(doc, method):
    
    if doc.stock_entry_type != "Material Receipt":
        return
        
    total_length_qty = 0
    
    for item in doc.items:
        if item.item_code and item.pieces and item.average_length:
            # Step 1: qty × length
            try:
                qty = float(item.pieces)
                average_length = float(item.average_length)
            except (TypeError, ValueError):
                frappe.throw(f"Invalid qty or length for item {item.item_code}: qty={item.qty}, length={item.length}")

            total_length_qty += qty * average_length
            
    
    # Step 2: Get weight_per_meter from Item master (use first item)
    if doc.items:
        first_item_code = doc.items[0].item_code
        weight_str = frappe.db.get_value("Item", first_item_code, "weight_per_meter") or "0"
        try:
            weight_per_meter = float(weight_str)*39.37
        except ValueError:
            frappe.throw(f"Invalid weight_per_meter for item {first_item_code}: '{weight_str}'")

        # Step 3: total × weight_per_meter → in kg
        weight_in_kg = total_length_qty * weight_per_meter

        # Convert to tonnes
        doc.weight_demand = round(weight_in_kg / 1000, 4)  # 4 decimals for tonne accuracy
    else:
        doc.weight_demand = 0

def validation_section_weight(doc, method):
    if doc.stock_entry_type != "Material Receipt":
        return
    
    # if not doc.items or not doc.weight_received or not doc.total_length_in_meter:
    #     return

    # item_code = doc.items[0].item_code
    # standard_section_weight = frappe.db.get_value("Item", item_code, "weight_per_meter")

    # if not standard_section_weight:
    #     frappe.throw(_("Standard section weight not found for item {0}").format(item_code))

    # try:
    #     weight_received_kg = float(doc.weight_received) * 1000
    #     received_section_weight = round(weight_received_kg/ doc.total_length_in_meter, 2)
    #     received_section_weight = round(received_section_weight/39.37, 2)
    # except ZeroDivisionError:
    #     frappe.throw(_("Total Length in Meter cannot be zero."))

    # # ±1.5% tolerance check
    # lower_bound = received_section_weight * 0.985
    # upper_bound = received_section_weight * 1.015

    # if float(standard_section_weight) < lower_bound or float(standard_section_weight) > upper_bound:
    #     frappe.throw(_(
    #         "Standard section weight for item {0} is outside ±1.5% of received section weight ({1}).\n"
    #         "This Stock Entry is subject to approval and cannot be submitted."
    #     ).format(item_code, received_section_weight))    

def update_cutting_plan_workflow(cutting_plan_name, stock_entry_name):
    from frappe.model.workflow import apply_workflow
    cutting_plan = frappe.get_doc("Cutting Plan", cutting_plan_name)
        
    if cutting_plan.workflow_state == "RM Allocation pending( Rm Not Allocated yet)":
            
            
            apply_workflow(cutting_plan, "Approve")
            
            cutting_plan.material_transfer_stock_entry = stock_entry_name
            cutting_plan.save(ignore_permissions=True)
            
            cutting_plan.add_comment(
                "Workflow", 
                f"Material Transfer {stock_entry_name} submitted - Status updated automatically"
            )
            
            frappe.msgprint(
                _("Cutting Plan {0} updated to 'RM Allocated' status").format(cutting_plan_name),
                alert=True
            )

def update_source_warehouse(cutting_plan_name, stock_entry_name):
        
    # Get the cutting plan document
    cutting_plan = frappe.get_doc("Cutting Plan", cutting_plan_name)
    
    # Get the stock entry document to fetch to_warehouse
    stock_entry = frappe.get_doc("Stock Entry", stock_entry_name)
    
    if cutting_plan.cut_plan_detail:
        for row in cutting_plan.cut_plan_detail:
            # Set source_warehouse as the to_warehouse from stock entry
            row.source_warehouse = stock_entry.to_warehouse
    
    # Save the cutting plan with updated source warehouses
    cutting_plan.save()
    
    frappe.msgprint(
        _("Source warehouse updated to {0} for all items in Cutting Plan {1}")
        .format(frappe.bold(stock_entry.to_warehouse), frappe.bold(cutting_plan_name))
    )

def set_custom_supplier_from_batch(doc):
    if doc.stock_entry_type != "Repack":
        return

    first_row = next((row for row in doc.items or [] if getattr(row, "batch_no", None)), None)
    if not first_row:
        return

    batch_info = frappe.db.get_value(
        "Batch",
        first_row.batch_no,
        ["reference_doctype", "reference_name"],
        as_dict=True,
    )

    if not batch_info or not batch_info.reference_name:
        return

    supplier = None
    ref_doctype = batch_info.reference_doctype
    ref_name = batch_info.reference_name

    if ref_doctype:
        if frappe.db.has_column(ref_doctype, "custom_supplier"):
            supplier = frappe.db.get_value(ref_doctype, ref_name, "custom_supplier")
        # if not supplier and frappe.db.has_column(ref_doctype, "supplier_name"):
        #     supplier = frappe.db.get_value(ref_doctype, ref_name, "supplier_name")
        if not supplier and frappe.db.has_column(ref_doctype, "supplier"):
            supplier = frappe.db.get_value(ref_doctype, ref_name, "supplier")

    if supplier:
        doc.db_set("custom_supplier", supplier, update_modified=False)

@frappe.whitelist()
def cancel_linked_psles(doc, method=None):
    # Run BEFORE frappe checks links — required for Stock Transfer rollback
    psles = frappe.get_all(
        "Piece Stock Ledger Entry",
        filters={"voucher_no": doc.name, "docstatus": 1},
        pluck="name",
    )
    for psle in psles:
        psle_doc = frappe.get_doc("Piece Stock Ledger Entry", psle)
        psle_doc.flags.ignore_permissions = True
        psle_doc.flags.ignore_links = True
        psle_doc.cancel()
