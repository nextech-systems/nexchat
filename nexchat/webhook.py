# Copyright (c) 2026, Nextech Systems and contributors
# For license information, please see license.txt

"""Incoming webhook receiver for the OpenWA gateway.

Every payload is logged raw to WA Event Log before any parsing, so no
event is ever lost to a parser bug. Authentication accepts either the
custom X-Nexchat-Token header (set at webhook registration) or an HMAC
SHA-256 signature of the raw body using the shared webhook secret.
"""

import hashlib
import hmac
import json
import re
from datetime import datetime

import frappe

ACK_MAP = {
	-1: "Failed",
	0: "Pending",
	1: "Sent",
	2: "Delivered",
	3: "Read",
	4: "Read",
}

SIGNATURE_HEADERS = (
	"X-Signature",
	"X-Hub-Signature-256",
	"X-Webhook-Signature",
	"X-Openwa-Signature",
)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle():
	"""Entry point: verify, log raw, then process."""
	raw = frappe.request.get_data() or b""

	if not _verify_request(raw):
		frappe.local.response.http_status_code = 401
		return {"ok": False, "error": "unauthorized"}

	try:
		data = json.loads(raw.decode("utf-8") or "{}")
	except (ValueError, UnicodeDecodeError):
		data = {"_undecodable": True, "_raw": raw.decode("utf-8", errors="replace")}

	event_type = (
		data.get("event")
		or data.get("type")
		or data.get("eventType")
		or data.get("name")
		or "unknown"
	)

	log = frappe.get_doc(
		{
			"doctype": "WA Event Log",
			"event_type": event_type,
			"payload": json.dumps(data, indent=1, default=str),
		}
	).insert(ignore_permissions=True)

	try:
		_process_event(event_type, data)
		log.db_set("processed", 1, update_modified=False)
	except Exception:
		log.db_set("error", frappe.get_traceback()[-400:], update_modified=False)
		frappe.log_error(frappe.get_traceback(), "NexChat Webhook")

	return {"ok": True}


def _verify_request(raw):
	"""Accept if the custom token header OR an HMAC signature matches."""
	settings = frappe.get_single("NexChat Settings")
	secret = settings.get_password("webhook_secret") or ""
	if not secret:
		return False

	token = frappe.request.headers.get("X-Nexchat-Token") or ""
	if token and hmac.compare_digest(token, secret):
		return True

	digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
	for header in SIGNATURE_HEADERS:
		sig = frappe.request.headers.get(header) or ""
		if not sig:
			continue
		for candidate in (digest, "sha256=" + digest):
			if hmac.compare_digest(sig, candidate):
				return True
	return False


def _process_event(event_type, data):
	if event_type in ("message.received", "message.sent"):
		_handle_message(event_type, data)
	elif event_type == "message.ack":
		_handle_ack(data)
	elif event_type == "session.status":
		frappe.publish_realtime(
			"nexchat_session_status",
			{"data": _unwrap(data)},
			after_commit=True,
		)
	# all other events: raw log only, nothing to do yet


def _unwrap(data):
	"""Gateway payloads may nest the object under data/payload/message."""
	for key in ("data", "payload", "message", "body"):
		inner = data.get(key)
		if isinstance(inner, dict):
			return inner
	return data


def _handle_message(event_type, data):
	m = _unwrap(data)
	incoming = event_type == "message.received"

	# whatsapp-web.js puts our own sends in message.received with fromMe=true
	if incoming and m.get("fromMe"):
		incoming = False

	chat_id = m.get("chatId") or (m.get("from") if incoming else m.get("to")) or ""
	if not chat_id or "@g.us" in chat_id:
		return  # group chats out of scope for now
	if not any(s in chat_id for s in ("@c.us", "@s.whatsapp.net", "@lid")):
		return  # status broadcasts etc.

	phone = re.sub(r"\D", "", chat_id.split("@")[0])
	if not phone:
		return

	message_id = _extract_message_id(m)
	if message_id and frappe.db.exists("WA Chat Message", {"message_id": message_id}):
		return  # webhook retry, already stored

	body = m.get("body") or m.get("text") or m.get("caption") or ""
	content_type = m.get("type") or "text"
	if content_type == "chat":
		content_type = "text"
	allowed_types = (
		"text", "image", "video", "audio", "document",
		"sticker", "location", "contact", "reaction",
	)
	if content_type not in allowed_types:
		content_type = "other"

	timestamp = _extract_timestamp(m)
	conversation = _get_or_create_conversation(phone, m, incoming, chat_id)

	msg = frappe.get_doc(
		{
			"doctype": "WA Chat Message",
			"conversation": conversation.name,
			"direction": "Incoming" if incoming else "Outgoing",
			"content_type": content_type,
			"message": body,
			"message_id": message_id,
			"chat_id": chat_id,
			"timestamp": timestamp,
			"ack_status": "" if incoming else "Sent",
		}
	).insert(ignore_permissions=True)

	updates = {"last_message_preview": (body or f"[{content_type}]")[:140]}
	if chat_id and conversation.get("chat_id") != chat_id and incoming:
		updates["chat_id"] = chat_id
	if incoming:
		updates["last_incoming_at"] = timestamp
		updates["unread_count"] = (conversation.unread_count or 0) + 1
		updates["status"] = "Open"
	else:
		updates["last_outgoing_at"] = timestamp
	conversation.db_set(updates, update_modified=True)

	frappe.publish_realtime(
		"nexchat_message",
		{
			"conversation": conversation.name,
			"message": msg.name,
			"direction": msg.direction,
			"preview": updates["last_message_preview"],
			"display_name": conversation.display_name,
		},
		after_commit=True,
	)


def _handle_ack(data):
	m = _unwrap(data)
	message_id = _extract_message_id(m) or m.get("messageId") or ""
	if not message_id:
		return
	ack = m.get("ack")
	try:
		ack = int(ack)
	except (TypeError, ValueError):
		return
	status = ACK_MAP.get(ack)
	if not status:
		return
	name = frappe.db.get_value("WA Chat Message", {"message_id": message_id})
	if name:
		frappe.db.set_value("WA Chat Message", name, "ack_status", status, update_modified=False)
		frappe.publish_realtime(
			"nexchat_ack",
			{"message": name, "ack_status": status},
			after_commit=True,
		)


def _extract_message_id(m):
	mid = m.get("id") or m.get("messageId") or ""
	if isinstance(mid, dict):
		mid = mid.get("_serialized") or mid.get("id") or ""
	return str(mid)[:140]


def _extract_timestamp(m):
	t = m.get("timestamp") or m.get("t")
	try:
		t = int(t)
		if t > 10**12:  # milliseconds
			t = t // 1000
		return datetime.fromtimestamp(t)
	except (TypeError, ValueError, OSError, OverflowError):
		return frappe.utils.now_datetime()


def _get_or_create_conversation(phone, m, incoming, chat_id=""):
	if frappe.db.exists("WA Conversation", phone):
		return frappe.get_doc("WA Conversation", phone)

	display_name = ""
	if incoming:
		display_name = (
			m.get("notifyName")
			or m.get("pushName")
			or m.get("senderName")
			or ""
		)

	try:
		conv = frappe.get_doc(
			{
				"doctype": "WA Conversation",
				"phone_number": phone,
				"display_name": display_name,
				"chat_id": chat_id if incoming else "",
				"status": "Open",
			}
		)
		conv.insert(ignore_permissions=True)
		return conv
	except frappe.DuplicateEntryError:
		return frappe.get_doc("WA Conversation", phone)
