# Copyright (c) 2026, Nextech Systems and contributors
# For license information, please see license.txt

"""Website chat channel.

Security model: visitor_id is a server-minted random token (not a phone
number, not client-chosen), returned once from start_conversation() and
then stored by the widget (cookie/localStorage) and sent back as a bearer
token on every subsequent call. Guessing one is infeasible (128 bits), but
anyone holding the token can read/write that conversation - same trust
model as a session cookie. Never accept a client-supplied visitor_id for
conversation creation; only ever look one up, never mint it from input.

Everything else deliberately mirrors webhook.py's WhatsApp flow so the
same locks.py / Conversation Queue machinery applies unchanged:
Web Conversation and Web Chat Message use the same field names as their
WA_* counterparts.
"""

import frappe
from frappe.utils import now_datetime

from nexchat.locks import sync_conversation, claim_on_reply

DOCTYPE = "Web Conversation"
MAX_MESSAGE_LEN = 4096

# guest abuse protection: simple per-visitor rate limit, mirrors the
# per_minute pattern already used for outbound WhatsApp sends in api.py
RATE_LIMIT_PER_MINUTE = 20


@frappe.whitelist(allow_guest=True, methods=["POST"])
def start_conversation(page_url=None, display_name=None, email=None, phone=None):
	"""Mint a new visitor_id and Web Conversation. Call this once per
	visitor (widget checks its cookie first and only calls this if empty).
	Pre-chat details are required by policy - the widget's own form
	enforces this, but we also validate here since client-side checks
	alone can't be trusted for a guest-facing endpoint."""
	display_name = (display_name or "").strip()
	email = (email or "").strip()
	phone = (phone or "").strip()
	if not display_name or not email or not phone:
		frappe.throw("Name, email, and phone are all required to start a chat")

	visitor_id = frappe.generate_hash(length=32)
	frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"visitor_id": visitor_id,
			"display_name": display_name[:140],
			"email": email[:140],
			"phone": phone[:40],
			"page_url": (page_url or "")[:255],
			"status": "Open",
		}
	).insert(ignore_permissions=True)
	sync_conversation(DOCTYPE, visitor_id)
	return {"visitor_id": visitor_id}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def identify(visitor_id, display_name=None, email=None, phone=None):
	"""Kept for compatibility / edge cases - normal flow now captures all
	three fields up front in start_conversation(). Still useful if an
	older conversation is missing one of these and needs a top-up."""
	conv = _get_conversation(visitor_id)
	updates = {}
	if display_name:
		updates["display_name"] = display_name[:140]
	if email:
		updates["email"] = email[:140]
	if phone:
		updates["phone"] = phone[:40]
	if updates:
		conv.db_set(updates, update_modified=False)
		sync_conversation(DOCTYPE, conv.name)
	return {"ok": True}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def send_from_widget(visitor_id, message, page_url=None):
	"""Visitor sends a message. Guest-callable - visitor_id is the bearer
	token, message is free text from an anonymous source, so treat both
	as untrusted input."""
	conv = _get_conversation(visitor_id)
	if not (conv.display_name and conv.email and conv.phone):
		frappe.throw("Please provide your name, email, and phone before chatting")
	message = (message or "").strip()
	if not message:
		frappe.throw("Message is empty")
	if len(message) > MAX_MESSAGE_LEN:
		frappe.throw(f"Message exceeds {MAX_MESSAGE_LEN} characters")

	_enforce_rate_limit(visitor_id)

	now = now_datetime()
	msg = frappe.get_doc(
		{
			"doctype": "Web Chat Message",
			"conversation": conv.name,
			"direction": "Incoming",
			"content_type": "text",
			"message": message,
			"timestamp": now,
		}
	).insert(ignore_permissions=True)

	updates = {
		"last_message_preview": message[:140],
		"last_incoming_at": now,
		"unread_count": (conv.unread_count or 0) + 1,
		"status": "Open",
	}
	if page_url:
		updates.setdefault("page_url", conv.page_url or page_url[:255])
	conv.db_set(updates, update_modified=True)
	sync_conversation(DOCTYPE, conv.name)

	frappe.enqueue(
		"nexchat.ai_reply.maybe_auto_reply",
		source_doctype=DOCTYPE,
		conversation_name=conv.name,
		message_text=message,
		queue="long",
		enqueue_after_commit=True,
	)

	frappe.publish_realtime(
		"nexchat_message",
		{
			"conversation": conv.name,
			"source_doctype": DOCTYPE,
			"message": msg.name,
			"direction": "Incoming",
			"preview": updates["last_message_preview"],
			"display_name": conv.display_name or "Website visitor",
		},
		after_commit=True,
	)
	return {"ok": True, "message": msg.name}


@frappe.whitelist(allow_guest=True)
def poll_messages(visitor_id, after=None):
	"""Widget polls this every few seconds for new messages (including
	agent replies). Simpler and more robust than guest realtime/socketio
	for a first version - revisit if polling load becomes a problem."""
	conv = _get_conversation(visitor_id)
	filters = {"conversation": conv.name}
	if after:
		# after = name of the last message the widget already has
		last_seen = frappe.db.get_value("Web Chat Message", after, "timestamp")
		if last_seen:
			filters["timestamp"] = (">", last_seen)

	messages = frappe.get_all(
		"Web Chat Message",
		filters=filters,
		fields=["name", "direction", "content_type", "message", "timestamp"],
		order_by="timestamp asc",
		limit_page_length=50,
	)
	return {"messages": messages, "status": conv.status}


@frappe.whitelist()
def send_reply(conversation, message, by_bot=False, is_acknowledgment=False):
	"""Agent reply from NexChat Center. Same shape as WA's send_message in
	api.py, minus the gateway call - there's no external transport here,
	the message just lands in Web Chat Message and the widget picks it up
	on its next poll. by_bot=True is for the AI auto-reply path only
	(nexchat.ai_reply) - skips the claim/lock flow so the conversation
	stays unassigned for a human to pick up if the customer needs more.
	is_acknowledgment=True (canned "someone will follow up" message) skips
	updating last_outgoing_at - not counted as answering the customer, so
	the SLA reminder/escalation timers in locks.py keep running normally."""
	by_bot = frappe.utils.cint(by_bot)
	is_acknowledgment = frappe.utils.cint(is_acknowledgment)
	if not by_bot and not frappe.has_permission(DOCTYPE, "write"):
		frappe.throw("Not permitted", frappe.PermissionError)
	message = (message or "").strip()
	if not message:
		frappe.throw("Message is empty")
	if len(message) > MAX_MESSAGE_LEN:
		frappe.throw(f"Message exceeds {MAX_MESSAGE_LEN} characters")

	conv = frappe.get_doc(DOCTYPE, conversation)
	if not by_bot:
		claim_on_reply(conv.name, frappe.session.user, doctype=DOCTYPE)

	now = now_datetime()
	msg = frappe.get_doc(
		{
			"doctype": "Web Chat Message",
			"conversation": conv.name,
			"direction": "Outgoing",
			"content_type": "text",
			"message": message,
			"timestamp": now,
			"sent_by": None if by_bot else frappe.session.user,
			"is_bot_reply": 1 if by_bot else 0,
		}
	).insert(ignore_permissions=True)

	conv_updates = {"last_message_preview": message[:140]}
	if not is_acknowledgment:
		conv_updates["last_outgoing_at"] = now
	conv.db_set(conv_updates, update_modified=True)
	sync_conversation(DOCTYPE, conv.name)

	frappe.publish_realtime(
		"nexchat_message",
		{
			"conversation": conv.name,
			"source_doctype": DOCTYPE,
			"message": msg.name,
			"direction": "Outgoing",
			"preview": message[:140],
			"display_name": conv.display_name or "Website visitor",
			"is_bot_reply": bool(by_bot),
		},
		after_commit=True,
	)
	return msg.name


@frappe.whitelist()
def mark_read(conversation):
	if not frappe.has_permission(DOCTYPE, "write"):
		frappe.throw("Not permitted", frappe.PermissionError)
	frappe.db.set_value(DOCTYPE, conversation, "unread_count", 0, update_modified=False)
	sync_conversation(DOCTYPE, conversation)
	return True


def _get_conversation(visitor_id):
	"""Look up an existing conversation only - never create one from a
	client-supplied visitor_id. If this throws, the widget's stored token
	is stale/invalid and it should call start_conversation() again."""
	if not visitor_id or not frappe.db.exists(DOCTYPE, visitor_id):
		frappe.throw("Unknown conversation", frappe.DoesNotExistError)
	return frappe.get_doc(DOCTYPE, visitor_id)


def _enforce_rate_limit(visitor_id):
	count = frappe.db.count(
		"Web Chat Message",
		filters={
			"conversation": visitor_id,
			"direction": "Incoming",
			"timestamp": (">", frappe.utils.add_to_date(now_datetime(), minutes=-1)),
		},
	)
	if count >= RATE_LIMIT_PER_MINUTE:
		frappe.throw("You're sending messages too quickly - please wait a moment.")
