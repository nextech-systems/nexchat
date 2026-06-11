# Copyright (c) 2026, Nextech Systems and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class NexChatSettings(Document):
	def validate(self):
		if self.gateway_url:
			self.gateway_url = self.gateway_url.rstrip("/")
		if self.enable_sla:
			if not self.first_reminder_minutes or self.first_reminder_minutes < 1:
				frappe.throw("First Reminder must be at least 1 minute")
			if not self.escalation_minutes or self.escalation_minutes <= self.first_reminder_minutes:
				frappe.throw("Escalate After must be greater than First Reminder")
