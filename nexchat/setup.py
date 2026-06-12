# Copyright (c) 2026, Nextech Systems and contributors
# For license information, please see license.txt

import frappe

ROLES = ("NexChat Agent", "NexChat Supervisor")


def ensure_roles():
	"""Create NexChat roles if missing. Wired to after_migrate, so a plain
	`bench migrate` provisions any site, including fresh installs."""
	for role in ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc(
				{"doctype": "Role", "role_name": role, "desk_access": 1}
			).insert(ignore_permissions=True)
	frappe.db.commit()
