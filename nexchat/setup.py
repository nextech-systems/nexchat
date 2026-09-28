# Copyright (c) 2026, Nextech Systems and contributors
# For license information, please see license.txt

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


ROLES = ("NexChat Agent", "NexChat Supervisor")


def after_migrate():
        ensure_roles()
        ensure_default_session()
        ensure_sales_request_quotation_field()


def ensure_roles():
        for role in ROLES:
                if not frappe.db.exists("Role", role):
                        frappe.get_doc(
                                {"doctype": "Role", "role_name": role, "desk_access": 1}
                        ).insert(ignore_permissions=True)
        frappe.db.commit()


def ensure_default_session():
        """Migrate the legacy single session_id from Settings into a
        NexChat Session record, once."""
        if frappe.db.count("NexChat Session"):
                return

        settings = frappe.get_single("NexChat Settings")

        if not settings.session_id:
                return

        frappe.get_doc(
                {
                        "doctype": "NexChat Session",
                        "session_label": "Main",
                        "session_id": settings.session_id,
                        "is_default": 1,
                        "active": 1,
                }
        ).insert(ignore_permissions=True)

        frappe.db.commit()


def ensure_sales_request_quotation_field():
        custom_fields = {
                "Quotation": [
                        {
                                "fieldname": "custom_nexchat_sales_request",
                                "label": "Sales Request",
                                "fieldtype": "Link",
                                "options": "Sales Request",
                                "insert_after": "party_name",
                                "read_only": 0,
                                "hidden": 0,
                                "no_copy": 1,
                        }
                ]
        }

        create_custom_fields(
                custom_fields,
                update=True,
        )
