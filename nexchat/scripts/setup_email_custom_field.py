import frappe


def run():
	if frappe.db.exists("Custom Field", "Communication-custom_is_bot_reply"):
		print("Custom Field already exists - nothing to do")
		return

	frappe.get_doc(
		{
			"doctype": "Custom Field",
			"dt": "Communication",
			"fieldname": "custom_is_bot_reply",
			"label": "Sent by AI Bot",
			"fieldtype": "Check",
			"default": "0",
			"read_only": 1,
			"insert_after": "sent_or_received",
			"description": "Set by NexChat's email channel when this outgoing email was an automatic AI reply, not a human agent.",
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()
	print("Custom Field created: Communication.custom_is_bot_reply")
