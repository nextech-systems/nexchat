# Copyright (c) 2026, Nextech Systems and contributors
# For license information, please see license.txt

"""Channel-agnostic read index for the unified NexChat Center inbox.

This doctype is NOT the source of truth for locking/SLA - each channel's
own doctype (WA Conversation, Web Conversation, ...) keeps owning that,
exactly as WA Conversation already does via nexchat.locks. This table is
a mirror: every channel handler calls sync_queue() after it writes to its
own conversation record, and the NexChat Center UI queries this table for
a single unified list instead of querying each channel doctype separately.
"""

import frappe
from frappe.model.document import Document

# Fields mirrored from a source conversation doc onto its Conversation Queue
# row. Every channel's source doctype must expose these fieldnames (they
# already match WA Conversation's schema).
MIRROR_FIELDS = (
	"status",
	"sla_state",
	"contact",
	"customer",
	"lead",
	"assigned_agent",
	"last_agent",
	"last_incoming_at",
	"last_outgoing_at",
	"unread_count",
	"last_message_preview",
	"display_name",
)


class ConversationQueue(Document):
	def validate(self):
		existing = frappe.db.get_value(
			"Conversation Queue",
			{
				"source_doctype": self.source_doctype,
				"source_name": self.source_name,
				"name": ("!=", self.name),
			},
			"name",
		)
		if existing:
			frappe.throw(
				f"Conversation Queue row {existing} already exists for "
				f"{self.source_doctype} {self.source_name}"
			)


def sync_queue(source_doctype, source_name, channel):
	"""Upsert the Conversation Queue mirror row for one conversation.

	Call this after any write to a channel's own conversation doc (new
	message, claim/release, SLA change, CRM link change). Cheap - one
	get_value lookup plus one write - safe to call often.
	"""
	source = frappe.db.get_value(
		source_doctype, source_name, list(MIRROR_FIELDS), as_dict=True
	)
	if not source:
		return

	existing = frappe.db.get_value(
		"Conversation Queue",
		{"source_doctype": source_doctype, "source_name": source_name},
		"name",
	)

	values = dict(source)
	values["channel"] = channel
	values["source_doctype"] = source_doctype
	values["source_name"] = source_name

	if existing:
		frappe.db.set_value("Conversation Queue", existing, values, update_modified=True)
		return existing

	doc = frappe.get_doc({"doctype": "Conversation Queue", **values})
	doc.insert(ignore_permissions=True)
	return doc.name
