import frappe


def create_batch(self):

	from erpnext.stock.doctype.batch.batch import make_batch
	dct = {}

	if hasattr(self, 'voucher_detail_no'):
		if self.voucher_type == "Stock Entry":
			data = frappe.get_doc("Stock Entry Detail", self.voucher_detail_no)
		else:
			data = frappe.get_doc(f"{self.voucher_type} Item", self.voucher_detail_no)
			
		dct.update({
			"pieces": data.get("pieces"),
			"weight_received": data.get("qty"),
			"average_length": data.get("average_length"),
			"section_weight": data.get("section_weight"),
			"lot_no": data.get("lot_no") if data.get("lot_no") else None,
			"manufacturing_date":self.get("posting_date"),
			"reference_detail_no": self.voucher_detail_no
		})
		if self.voucher_type == "Purchase Receipt":
			data = frappe.get_doc(self.voucher_type,self.voucher_no)
		dct.update({
			"supplier": data.get("supplier"),
			
		})
	
	dct.update({
		"item": self.get("item_code"),
		"reference_doctype": self.get("voucher_type"),
		"reference_name": self.get("voucher_no"),
  		"manufacturing_date":self.get("posting_date"),
	})
	
	return make_batch(frappe._dict(dct))
