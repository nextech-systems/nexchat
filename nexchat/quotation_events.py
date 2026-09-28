import frappe


def _get_sales_request_name(doc):
    return (
        doc.get("custom_nexchat_sales_request")
        or ""
    ).strip()


def before_validate_quotation(doc, method=None):
    sales_request = _get_sales_request_name(doc)

    if not sales_request:
        return

    if not frappe.db.exists(
        "Sales Request",
        sales_request,
    ):
        frappe.throw(
            f"Sales Request {sales_request} does not exist.",
            frappe.ValidationError,
        )

    request = frappe.db.get_value(
        "Sales Request",
        sales_request,
        [
            "sales_person",
            "customer",
        ],
        as_dict=True,
    )

    if not request or not request.sales_person:
        frappe.throw(
            "Sales Request does not have an ERPNext Sales Person.",
            frappe.ValidationError,
        )

    if not doc.sales_team:
        doc.set(
            "sales_team",
            [
                {
                    "sales_person": request.sales_person,
                    "allocated_percentage": 100,
                }
            ],
        )


def on_quotation_update(doc, method=None):
    sales_request = _get_sales_request_name(doc)

    if not sales_request:
        return

    # NexChat only links Draft Quotations.
    if doc.docstatus != 0:
        return

    if not frappe.db.exists(
        "Sales Request",
        sales_request,
    ):
        frappe.log_error(
            title="NexChat Quotation Link Error",
            message=(
                f"Quotation {doc.name} references missing "
                f"Sales Request {sales_request}"
            ),
        )
        return

    from nexchat.nexchat.doctype.sales_request.sales_request import (
        _link_draft_quotation_internal,
    )

    _link_draft_quotation_internal(
        sales_request=sales_request,
        quotation=doc.name,
    )
