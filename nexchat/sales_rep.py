import re

import frappe
from frappe import _


def _normalize_phone(value):
    if not value:
        return ""

    return re.sub(r"\D", "", value)


def _format_rep(doc):
    return {
        "rep_code": doc.rep_code,
        "rep_name": doc.rep_name,
        "employee": doc.employee,
        "erpnext_user": doc.erpnext_user,
        "sales_person": doc.sales_person,
        "pbx_extension": doc.pbx_extension,
        "mobile_number": doc.mobile_number,
        "active": bool(doc.active),
    }


@frappe.whitelist()
def resolve_sales_rep(
    pbx_extension=None,
    mobile_number=None,
    rep_code=None,
):
    filters = {
        "active": 1,
    }

    if pbx_extension:
        filters["pbx_extension"] = pbx_extension.strip()

    elif rep_code:
        filters["rep_code"] = rep_code.strip().upper()

    elif mobile_number:
        wanted = _normalize_phone(mobile_number)

        rows = frappe.get_all(
            "AI Sales Rep",
            filters={"active": 1},
            fields=["name", "mobile_number"],
        )

        for row in rows:
            if _normalize_phone(row.mobile_number) == wanted:
                doc = frappe.get_doc("AI Sales Rep", row.name)

                return {
                    "status": "resolved",
                    "sales_rep": _format_rep(doc),
                }

        return {
            "status": "not_found",
            "sales_rep": None,
        }

    else:
        frappe.throw(
            _("Provide PBX extension, mobile number, or rep code."),
            frappe.ValidationError,
        )

    name = frappe.db.get_value(
        "AI Sales Rep",
        filters,
        "name",
    )

    if not name:
        return {
            "status": "not_found",
            "sales_rep": None,
        }

    doc = frappe.get_doc(
        "AI Sales Rep",
        name,
    )

    return {
        "status": "resolved",
        "sales_rep": _format_rep(doc),
    }
