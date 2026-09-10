import frappe
from frappe.model.naming import make_autoname
import re

def autoname(doc, method):
    """
    Custom autoname for Batch:
    - Uses company-based prefix (MUBT / MSBT)
    - If reference batch is from cutting plan, create C-series per parent batch
    - Uses reference_detail_no to link to correct row
    """
    # Set company-based naming series
    if doc.reference_doctype and doc.reference_name:
        company = frappe.db.get_value(doc.reference_doctype, doc.reference_name, "company")
        company_key = " ".join((company or "").split()).upper()
        is_stelco = company_key == "MADHAV STELCO PRIVATE LIMITED"
        if company_key == "MADHAV UDYOG PRIVATE LIMITED":
            doc.naming_series = "MUBT.-"
        elif is_stelco:
            doc.naming_series = "MSBT.-"

        # Handle Stock Entry reference
        if doc.reference_doctype == "Stock Entry":
            stock_entry = frappe.get_doc("Stock Entry", doc.reference_name)

            # Special case: FG Free Length Transfer cum Cutting Entry → MSFGYY-0000001 style
            if (
                stock_entry.stock_entry_type
                == "FG Free Length Transfer cum Cutting Entry"
                and is_stelco
            ):
                posting_date = getattr(stock_entry, "posting_date", None)
                if posting_date:
                    year_suffix = str(posting_date.year)[-2:]
                else:
                    year_suffix = frappe.utils.now_datetime().strftime("%y")

                # Use standard series pattern with a dot before hashes
                # to generate names like MSFG25-0000001
                series_prefix = f"MSFG{year_suffix}-."
                doc.name = make_autoname(series_prefix + "#######")
                doc.batch_id = doc.name
                return

            # Check if from Cutting Plan & if reference_detail_no exists
            if getattr(stock_entry, 'cutting_plan_reference', None) and getattr(doc, "reference_detail_no", None):
                sed = frappe.get_doc("Stock Entry Detail", doc.reference_detail_no)

                if sed.reference_parent_batch:
                    # Generate next cut number for THIS parent batch
                    next_num = get_next_cut_number(sed.reference_parent_batch)
                    doc.name = f"{sed.reference_parent_batch}-C-{str(next_num)}"
                    doc.batch_id = doc.name
                    return

        # Fallback if not from cutting plan
        doc.name = make_autoname(doc.naming_series or "BATCH-.#####")
        doc.batch_id = doc.name

    else:
        # Absolute fallback if no reference set at all
        doc.name = make_autoname("BATCH-.#####")
        doc.batch_id = doc.name


def get_next_cut_number(reference_batch):
    """
    Finds the next cut number for the given reference batch.
    """
    existing_batches = frappe.get_all(
        "Batch",
        filters={"batch_id": ["like", f"{reference_batch}-C-%"]},
        pluck="batch_id"
    )
    max_num = 0
    for b in existing_batches:
        match = re.search(r"-C-(\d+)$", b)
        if match:
            num = int(match.group(1))
            if num > max_num:
                max_num = num
    return max_num + 1

def before_insert(self,method):
    self.manufacturing_date = frappe.db.get_value(self.reference_doctype,self.reference_name,"posting_date")