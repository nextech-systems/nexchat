# Copyright (c) 2026, Nextech Systems and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class NexChatSession(Document):
	def validate(self):
		if self.is_default:
			others = frappe.get_all(
				"NexChat Session",
				filters={"is_default": 1, "name": ["!=", self.name]},
				pluck="name",
			)
			for name in others:
				frappe.db.set_value("NexChat Session", name, "is_default", 0)

	def on_update(self):
		"""Sync the coverage table to User Permissions so each listed user
		can see this session's conversations, and removed users lose access.
		Visibility is enforced by the permission_query hook in permissions.py."""
		desired = {row.user for row in self.agents if row.user}

		existing = {
			p.user: p.name
			for p in frappe.get_all(
				"User Permission",
				filters={
					"allow": "NexChat Session",
					"for_value": self.name,
				},
				fields=["name", "user"],
			)
		}

		# add permissions for newly-listed users
		for user in desired - set(existing):
			frappe.get_doc(
				{
					"doctype": "User Permission",
					"user": user,
					"allow": "NexChat Session",
					"for_value": self.name,
					"apply_to_all_doctypes": 0,
					"applicable_for": "WA Conversation",
				}
			).insert(ignore_permissions=True)

		# remove permissions for users no longer listed
		for user, permname in existing.items():
			if user not in desired:
				frappe.delete_doc(
					"User Permission", permname, ignore_permissions=True, force=True
				)
