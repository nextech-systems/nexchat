# Copyright (c) 2026, Nextech Systems and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class NexChatSession(Document):
	def validate(self):
		if self.is_default:
			# only one default at a time
			others = frappe.get_all(
				"NexChat Session",
				filters={"is_default": 1, "name": ["!=", self.name]},
				pluck="name",
			)
			for name in others:
				frappe.db.set_value("NexChat Session", name, "is_default", 0)
