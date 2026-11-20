# Copyright (c) 2026, Nextech Systems and contributors
# For license information, please see license.txt

"""Website chat channel.

Security model (v2): visitor_id is a signed, expiring credential, separate
from the conversation record name. Legacy record-name tokens are rejected.

Original channel background: visitor_id was a server-minted token (not a phone
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

import secrets

import frappe
from nexchat.web_security import (mint_token, token_conversation, visitor_request,
    details, page_url as clean_page_url, staff_doc, publish, inspect_token)
from frappe.utils import now_datetime

from nexchat.locks import sync_conversation, claim_on_reply

DOCTYPE = "Web Conversation"
MAX_MESSAGE_LEN = 4096

# guest abuse protection: simple per-visitor rate limit, mirrors the
# per_minute pattern already used for outbound WhatsApp sends in api.py
RATE_LIMIT_PER_MINUTE = 20


def _create_conversation(display_name=None, email=None, phone=None, page_url=None):
	"""Internal constructor: every call has already passed creation rate limits."""
	if any(value is not None for value in (display_name, email, phone)):
		display_name, email, phone = details(display_name, email, phone)
	else:
		display_name = email = phone = ""
	visitor_id = secrets.token_hex(16)
	conv = frappe.get_doc({
		"doctype": DOCTYPE, "visitor_id": visitor_id,
		"display_name": display_name, "email": email, "phone": phone,
		"page_url": clean_page_url(page_url), "status": "Open", "handover_requested": 0,
	}).insert(ignore_permissions=True)
	sync_conversation(DOCTYPE, conv.name)
	return {"visitor_id": mint_token(conv.name), "requires_details": False, "status": "Open"}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def start_conversation(page_url=None, display_name=None, email=None, phone=None):
	visitor_request("start")
	return _create_conversation(display_name, email, phone, page_url)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def start_new_conversation(visitor_id, page_url=None):
	"""A separate chat, reusing details only from a credential the visitor holds.
	No customer, lead, agent assignment or transcript is copied to the new chat.
	The previous chat is preserved; starting a new chat does not end it.
	"""
	visitor_request("start")
	conv = _get_conversation(visitor_id)
	if _complete(conv):
		return _create_conversation(conv.display_name, conv.email, conv.phone, page_url)
	return _create_conversation(page_url=page_url)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def end_conversation(visitor_id):
	visitor_request("end", visitor_id)
	conv = _get_conversation(visitor_id, for_update=True)
	if conv.status == "Open":
		conv.db_set({"status": "Resolved", "unread_count": 0, "sla_state": "OK",
			"reminded_at": None}, update_modified=True)
		sync_conversation(DOCTYPE, conv.name)
		publish("nexchat_message", {"source_doctype": DOCTYPE, "conversation": conv.name,
			"direction": "Meta", "preview": "", "status": "Resolved"}, after_commit=True)
	return {"ok": True, "status": conv.status, "ended": conv.status != "Open"}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def recent_chats(visitor_ids):
	"""Summaries only for a bounded list of valid, unexpired browser credentials.
	There is deliberately no lookup by contact details or conversation record ID.
	"""
	visitor_request("history")
	if not isinstance(visitor_ids, list) or len(visitor_ids) > 8:
		frappe.throw("Please supply at most eight chat credentials")
	if any(not isinstance(token, str) or len(token) > 1024 for token in visitor_ids):
		frappe.throw("Invalid chat credentials")
	chats = []
	seen = set()
	for index, token in enumerate(visitor_ids):
		try:
			name = inspect_token(token)
		except ValueError:
			continue
		if name in seen or not frappe.db.exists(DOCTYPE, name):
			continue
		seen.add(name)
		conv = frappe.get_doc(DOCTYPE, name)
		chats.append({"index": index, "status": conv.status,
			"started_at": conv.creation, "last_activity_at": conv.modified,
			"preview": (conv.get("last_message_preview") or "")[:160]})
	return {"chats": chats}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def resume(visitor_id):
	visitor_request("resume", visitor_id)
	conv = _get_conversation(visitor_id)
	return {"requires_details": _requires_details(conv), "status": conv.status, "ended": conv.status != "Open"}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def identify(visitor_id, display_name=None, email=None, phone=None):
	visitor_request("identify", visitor_id)
	conv = _get_conversation(visitor_id, for_update=True)
	if conv.status != "Open":
		return {"ok": False, "status": conv.status, "ended": True}
	name, email, phone = details(display_name, email, phone)
	conv.db_set({"display_name": name, "email": email, "phone": phone}, update_modified=False)
	sync_conversation(DOCTYPE, conv.name)
	if _handover_pending(conv):
		from nexchat.ai_reply import _send_escalation_ack
		_send_escalation_ack(DOCTYPE, conv)
	return {"ok": True, "status": conv.status}


def _complete(conv):
	return bool(conv.get("display_name") and conv.get("email") and conv.get("phone"))


def _handover_pending(conv):
	return bool(conv.get("handover_requested") or conv.get("ai_ack_sent") or conv.get("assigned_agent"))


def _requires_details(conv):
	return conv.status == "Open" and _handover_pending(conv) and not _complete(conv)


def _request_handover(conversation):
	"""Persist under the same row lock used for sends, claims and end-chat."""
	conv = _lock_conversation(conversation)
	if conv.status == "Open" and not conv.get("handover_requested"):
		conv.db_set({"handover_requested": 1}, update_modified=True)
		sync_conversation(DOCTYPE, conv.name)
	return conv


@frappe.whitelist(allow_guest=True, methods=["POST"])
def request_handover(visitor_id):
	visitor_request("handover", visitor_id)
	conv = _get_conversation(visitor_id)
	conv = _request_handover(conv.name)
	if conv.status == "Open" and _complete(conv):
		from nexchat.ai_reply import _send_escalation_ack
		_send_escalation_ack(DOCTYPE, conv)
	return {"ok": True, "requires_details": _requires_details(conv),
		"status": conv.status, "ended": conv.status != "Open"}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def send_from_widget(visitor_id, message, page_url=None):
	"""Visitor sends a message. Guest-callable - visitor_id is the bearer
	token, message is free text from an anonymous source, so treat both
	as untrusted input."""
	visitor_request("send", visitor_id)
	conv = _get_conversation(visitor_id, for_update=True)
	if conv.status != "Open":
		return {"ok": False, "status": conv.status, "ended": True}
	if _requires_details(conv):
		return {"ok": False, "requires_details": True, "status": conv.status}
	if not isinstance(message, str):
		frappe.throw("Message must be text")
	message = message.strip()
	if not message:
		frappe.throw("Message is empty")
	if len(message) > MAX_MESSAGE_LEN:
		frappe.throw(f"Message exceeds {MAX_MESSAGE_LEN} characters")

	_enforce_rate_limit(conv.name)

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
		updates.setdefault("page_url", conv.page_url or clean_page_url(page_url))
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

	publish(
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


@frappe.whitelist(allow_guest=True, methods=["POST"])
def poll_messages(visitor_id, after=None):
	"""Widget polls this every few seconds for new messages (including
	agent replies). Simpler and more robust than guest realtime/socketio
	for a first version - revisit if polling load becomes a problem."""
	visitor_request("poll", visitor_id)
	conv = _get_conversation(visitor_id)
	fields = ["name", "direction", "content_type", "message", "timestamp"]
	filters = {"conversation": conv.name}
	messages = []
	if after:
		if not isinstance(after, str) or len(after) > 140:
			frappe.throw("Invalid message cursor")
		last_seen = frappe.db.get_value("Web Chat Message", {"name": after, "conversation": conv.name}, "timestamp")
		if not last_seen:
			frappe.throw("Invalid message cursor")
		# Page across timestamp ties without replaying or dropping messages.
		messages = frappe.get_all("Web Chat Message", filters={
			"conversation": conv.name, "timestamp": last_seen, "name": (">", after)},
			fields=fields, order_by="timestamp asc, name asc", limit_page_length=50)
		filters["timestamp"] = (">", last_seen)
	if len(messages) < 50:
		messages += frappe.get_all("Web Chat Message", filters=filters, fields=fields,
			order_by="timestamp asc, name asc", limit_page_length=50-len(messages))
	return {"messages": messages, "status": conv.status, "requires_details": _requires_details(conv), "ended": conv.status != "Open"}


@frappe.whitelist(methods=["POST"])
def send_reply(conversation, message, by_bot=False, is_acknowledgment=False):
	"""Staff API. Bot flags are reserved for private background calls."""
	if frappe.utils.cint(by_bot) or frappe.utils.cint(is_acknowledgment):
		frappe.throw("Not permitted", frappe.PermissionError)
	staff_doc(DOCTYPE, conversation, "write")
	return _send_reply(conversation, message, by_bot=False, is_acknowledgment=False)


def _send_reply(conversation, message, by_bot=False, is_acknowledgment=False):
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
	if not by_bot:
		staff_doc(DOCTYPE, conversation, "write")
	message = (message or "").strip()
	if not message:
		frappe.throw("Message is empty")
	if len(message) > MAX_MESSAGE_LEN:
		frappe.throw(f"Message exceeds {MAX_MESSAGE_LEN} characters")

	conv = _lock_conversation(conversation)
	if conv.status != "Open":
		if by_bot:
			return None
		frappe.throw("This chat has ended. The visitor must start a new chat.")
	if by_bot and (conv.get("assigned_agent") or (_handover_pending(conv) and not is_acknowledgment)):
		return None
	if by_bot and is_acknowledgment and not _complete(conv):
		return None
	if not by_bot:
		conv = _request_handover(conv.name)
		if not _complete(conv):
			return {"ok": False, "requires_details": True}
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

	publish(
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
	staff_doc(DOCTYPE, conversation, "write")
	if not frappe.has_permission(DOCTYPE, "write"):
		frappe.throw("Not permitted", frappe.PermissionError)
	frappe.db.set_value(DOCTYPE, conversation, "unread_count", 0, update_modified=False)
	sync_conversation(DOCTYPE, conversation)
	return True


def _lock_conversation(name):
	# A current locking read avoids an old transaction snapshot reopening a chat
	# or delivering a generated reply after end-chat/agent takeover has committed.
	rows = frappe.db.sql("SELECT * FROM `tabWeb Conversation` WHERE name=%s FOR UPDATE",
		(name,), as_dict=True)
	if not rows:
		frappe.throw("Chat session expired. Please start a new chat.", frappe.PermissionError)
	conv = frappe.get_doc(DOCTYPE, name)
	conv.update(rows[0])
	return conv


def _get_conversation(visitor_id, for_update=False):
	name = token_conversation(visitor_id)
	if for_update:
		return _lock_conversation(name)
	if not frappe.db.exists(DOCTYPE, name):
		frappe.throw("Chat session expired. Please start a new chat.", frappe.PermissionError)
	return frappe.get_doc(DOCTYPE, name)


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


def audit_chat_controls():
	"""Read-only deployment check for bench execute; not a public API."""
	field = frappe.get_meta(DOCTYPE).get_field("status")
	options = (field.options or "").split("\n") if field else []
	issues = []
	if "Open" not in options or "Resolved" not in options:
		issues.append("Web Conversation status must support Open and Resolved")
	if not frappe.get_meta(DOCTYPE).has_field("handover_requested"):
		issues.append("Run bench migrate to install handover_requested")
	return {"scope": "website chat controls readiness", "controls_version": 2, "anonymous_ai_chat": True,
		"handover_field_available": bool(frappe.get_meta(DOCTYPE).has_field("handover_requested")),
		"recent_chat_limit": 8, "resolved_status_supported": "Resolved" in options,
		"issues": issues}
