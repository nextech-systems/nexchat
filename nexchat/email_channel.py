# Copyright (c) 2026, Nextech Systems and contributors
# For license information, please see license.txt

"""Email channel.

Deliberately does NOT reinvent email fetching or a parallel message
doctype. Frappe's own Email Account (IMAP) already pulls incoming mail
into Communication records with proper threading, attachments, etc. -
that is mature, well-tested infrastructure and this module just bridges
it into the same Conversation Queue / NexChat Center machinery the other
two channels use.

One Email Conversation per SENDER ADDRESS, not per subject/thread -
matches WA Conversation (per phone number) and Web Conversation (per
visitor_id): one row is "this customer", not "this topic". Multiple
email threads from the same address all land in one conversation.

Scoped to a single dedicated support mailbox (NexChat Settings.
support_email_account) so this never touches Communications tied to
ordinary ERPNext email use elsewhere (invoices, quotations, etc.) - the
hook is a no-op until that setting is configured.
"""

import frappe
from nexchat.web_security import staff_doc, publish
from frappe.utils import now_datetime

from nexchat.locks import sync_conversation, claim_on_reply

DOCTYPE = "Email Conversation"


def on_communication_insert(doc, method=None):
	"""Registered in hooks.py against Communication's after_insert. Fires
	for every Communication in the system - the very first checks below
	must cheaply rule out the overwhelming majority that have nothing to
	do with NexChat before doing any real work."""
	try:
		if doc.communication_type != "Communication":
			return
		if doc.communication_medium != "Email":
			return
		if doc.sent_or_received != "Received":
			return
		if not doc.sender:
			return

		settings = frappe.get_single("NexChat Settings")
		support_account = settings.get("support_email_account")
		if not support_account:
			return  # feature not configured yet - safe no-op
		if doc.get("email_account") != support_account:
			return  # not our support mailbox - leave it alone entirely

		# Don't hijack a communication Frappe already threaded to something
		# else (shouldn't normally happen on a dedicated support mailbox,
		# but never overwrite an existing reference either way).
		if doc.reference_doctype and doc.reference_doctype != DOCTYPE:
			return

		sender_email = doc.sender.strip().lower()
		conv_name = frappe.db.get_value(DOCTYPE, {"email": sender_email})
		if conv_name:
			conv = frappe.get_doc(DOCTYPE, conv_name)
		else:
			conv = frappe.get_doc(
				{
					"doctype": DOCTYPE,
					"email": sender_email,
					"display_name": doc.sender_full_name or sender_email,
					"status": "Open",
				}
			).insert(ignore_permissions=True)

		if doc.reference_doctype != DOCTYPE or doc.reference_name != conv.name:
			doc.db_set(
				{"reference_doctype": DOCTYPE, "reference_name": conv.name},
				update_modified=False,
			)

		now = doc.communication_date or now_datetime()
		content_preview = frappe.utils.strip_html(doc.content or "")[:140]
		conv.db_set(
			{
				"last_incoming_at": now,
				"last_subject": (doc.subject or "")[:140],
				"last_message_preview": content_preview,
				"unread_count": (conv.unread_count or 0) + 1,
				"status": "Open",
				"ai_ack_sent": 0,  # a fresh incoming email is a fresh reason to ack again
			},
			update_modified=True,
		)
		sync_conversation(DOCTYPE, conv.name)

		publish(
			"nexchat_message",
			{
				"conversation": conv.name,
				"source_doctype": DOCTYPE,
				"message": doc.name,
				"direction": "Incoming",
				"preview": content_preview,
				"display_name": conv.display_name or sender_email,
			},
			after_commit=True,
		)

		frappe.enqueue(
			"nexchat.ai_reply.maybe_auto_reply",
			source_doctype=DOCTYPE,
			conversation_name=conv.name,
			message_text=frappe.utils.strip_html(doc.content or ""),
			queue="long",
			enqueue_after_commit=True,
		)
	except Exception:
		# Never let a bug here break Frappe's own email fetch pipeline -
		# that would be a much worse failure than NexChat missing one email.
		frappe.log_error(frappe.get_traceback(), "NexChat Email Channel")


@frappe.whitelist(methods=["POST"])
def send_email_reply(conversation, message, subject=None, by_bot=False, is_acknowledgment=False):
	"""Staff API. Bot flags are reserved for private background calls."""
	if frappe.utils.cint(by_bot) or frappe.utils.cint(is_acknowledgment):
		frappe.throw("Not permitted", frappe.PermissionError)
	staff_doc(DOCTYPE, conversation, "write")
	return _send_email_reply(conversation, message, subject=subject, by_bot=False, is_acknowledgment=False)


def _send_email_reply(conversation, message, subject=None, by_bot=False, is_acknowledgment=False):
	"""Agent (or AI) reply from NexChat Center. Creates the outgoing
	Communication explicitly rather than relying on frappe.sendmail's
	side effects, so it's threaded onto this Email Conversation the same
	deliberate way incoming mail is. is_acknowledgment=True skips
	last_outgoing_at, same convention as the other two channels - not
	counted as answering the customer, so the SLA timers keep running."""
	by_bot = frappe.utils.cint(by_bot)
	is_acknowledgment = frappe.utils.cint(is_acknowledgment)
	if not by_bot:
		staff_doc(DOCTYPE, conversation, "write")

	message = (message or "").strip()
	if not message:
		frappe.throw("Message is empty")

	conv = frappe.get_doc(DOCTYPE, conversation)
	if not by_bot:
		claim_on_reply(conv.name, frappe.session.user, doctype=DOCTYPE)

	settings = frappe.get_single("NexChat Settings")
	support_account = settings.get("support_email_account")
	if not support_account:
		frappe.throw("NexChat Settings: Support Email Account is not configured")

	subject = subject or (f"Re: {conv.last_subject}" if conv.last_subject else "Re: your message")

	comm = frappe.get_doc(
		{
			"doctype": "Communication",
			"communication_type": "Communication",
			"communication_medium": "Email",
			"sent_or_received": "Sent",
			"reference_doctype": DOCTYPE,
			"reference_name": conv.name,
			"subject": subject,
			"content": message,
			"recipients": conv.email,
			"email_account": support_account,
			"communication_date": now_datetime(),
		}
	)
	comm.insert(ignore_permissions=True)
	comm.db_set("custom_is_bot_reply", 1 if by_bot else 0, update_modified=False)
	# Actually sends the email via the configured Email Account's SMTP.
	comm.send_email()

	now = now_datetime()
	conv_updates = {"last_message_preview": message[:140]}
	if not is_acknowledgment:
		conv_updates["last_outgoing_at"] = now
	conv.db_set(conv_updates, update_modified=True)
	sync_conversation(DOCTYPE, conv.name)

	publish(
		"nexchat_message",
		{
			"conversation": conv.name,
			"source_doctype": DOCTYPE,
			"message": comm.name,
			"direction": "Outgoing",
			"preview": message[:140],
			"display_name": conv.display_name or conv.email,
			"is_bot_reply": bool(by_bot),
		},
		after_commit=True,
	)
	return comm.name


@frappe.whitelist()
def mark_read(conversation):
	staff_doc(DOCTYPE, conversation, "write")
	if not frappe.has_permission(DOCTYPE, "write"):
		frappe.throw("Not permitted", frappe.PermissionError)
	frappe.db.set_value(DOCTYPE, conversation, "unread_count", 0, update_modified=False)
	sync_conversation(DOCTYPE, conversation)
	return True
