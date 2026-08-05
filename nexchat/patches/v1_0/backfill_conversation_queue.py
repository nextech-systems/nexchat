# Copyright (c) 2026, Nextech Systems and contributors
# For license information, please see license.txt

"""Backfill Conversation Queue rows for WA Conversation records that
existed before Conversation Queue was introduced, so the unified NexChat
Center inbox shows full history on first deploy instead of only new
activity going forward."""

import frappe

from nexchat.locks import sync_conversation


def execute():
	frappe.reload_doc("nexchat", "doctype", "conversation_queue")

	names = frappe.get_all("WA Conversation", pluck="name")
	for name in names:
		# skip if already mirrored (safe to re-run this patch)
		if frappe.db.exists(
			"Conversation Queue",
			{"source_doctype": "WA Conversation", "source_name": name},
		):
			continue
		sync_conversation("WA Conversation", name)

	frappe.db.commit()
