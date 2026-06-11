# Copyright (c) 2026, Nextech Systems and contributors
# For license information, please see license.txt

import re

import requests
import frappe


def _gateway():
	"""Return (settings, base_url, headers) or throw if unconfigured."""
	settings = frappe.get_single("NexChat Settings")
	if not (settings.gateway_url and settings.session_id):
		frappe.throw("NexChat Settings incomplete: set Gateway URL and Session ID")
	headers = {
		"X-Api-Key": settings.get_password("api_key") or "",
		"Content-Type": "application/json",
	}
	base = f"{settings.gateway_url}/api/sessions/{settings.session_id}"
	return settings, base, headers


@frappe.whitelist()
def test_connection():
	"""Ping the OpenWA gateway and report session status."""
	frappe.only_for("System Manager")
	settings, base, headers = _gateway()

	try:
		resp = requests.get(base, headers=headers, timeout=5)
		resp.raise_for_status()
		data = resp.json()
		return {
			"status": data.get("status"),
			"phone": data.get("phone"),
			"name": data.get("name"),
		}
	except requests.exceptions.ConnectionError:
		frappe.throw("Cannot reach gateway - check URL and firewall")
	except requests.exceptions.HTTPError as e:
		frappe.throw(f"Gateway error: {e.response.status_code} - check API key and session ID")


def _resolve_chat_id(base, headers, chat_id):
	"""LID addresses are flaky to send to directly; resolve to the real
	@c.us address via the gateway's contacts endpoint when possible."""
	if "@lid" not in chat_id:
		return chat_id
	try:
		resp = requests.get(f"{base}/contacts/{chat_id}", headers=headers, timeout=8)
		resp.raise_for_status()
		resolved = (resp.json() or {}).get("id") or ""
		if resolved and "@" in resolved:
			return resolved
	except (requests.exceptions.RequestException, ValueError):
		pass
	return chat_id


@frappe.whitelist()
def send_message(conversation, message):
	"""Send a text message into an existing conversation. Returns the
	WA Chat Message name on success."""
	if not frappe.has_permission("WA Conversation", "write"):
		frappe.throw("Not permitted", frappe.PermissionError)
	message = (message or "").strip()
	if not message:
		frappe.throw("Message is empty")
	if len(message) > 4096:
		frappe.throw("Message exceeds 4096 characters")

	conv = frappe.get_doc("WA Conversation", conversation)
	chat_id = conv.chat_id
	if not chat_id:
		chat_id = frappe.db.get_value(
			"WA Chat Message",
			{"conversation": conv.name, "direction": "Incoming"},
			"chat_id",
			order_by="timestamp desc",
		) or f"{conv.phone_number}@c.us"

	_settings, base, headers = _gateway()
	chat_id = _resolve_chat_id(base, headers, chat_id)

	gateway_unconfirmed = False
	try:
		resp = requests.post(
			f"{base}/messages/send-text",
			headers=headers,
			json={"chatId": chat_id, "text": message},
			timeout=15,
		)
		resp.raise_for_status()
	except requests.exceptions.HTTPError as e:
		if e.response is not None and e.response.status_code == 500:
			# Known whatsapp-web.js "No LID for user" bug: the send executes
			# in the browser, then the library throws while serializing the
			# response. The message is almost always delivered. Record it,
			# never retry (a retry would double-message the customer).
			gateway_unconfirmed = True
			frappe.log_error(
				f"Gateway returned 500 for {chat_id}; recording as unconfirmed "
				"(known wweb.js LID bug, message likely delivered)",
				"NexChat Send Unconfirmed",
			)
		else:
			frappe.log_error(frappe.get_traceback(), "NexChat Send")
			frappe.throw("Gateway send failed - see Error Log")
	except requests.exceptions.RequestException:
		frappe.log_error(frappe.get_traceback(), "NexChat Send")
		frappe.throw("Gateway send failed - see Error Log")

	message_id = ""
	if not gateway_unconfirmed:
		try:
			data = resp.json()
			mid = data.get("id") or data.get("messageId") or ""
			if isinstance(mid, dict):
				mid = mid.get("_serialized") or mid.get("id") or ""
			message_id = str(mid)[:140]
		except ValueError:
			pass

	now = frappe.utils.now_datetime()
	msg = frappe.get_doc(
		{
			"doctype": "WA Chat Message",
			"conversation": conv.name,
			"direction": "Outgoing",
			"content_type": "text",
			"message": message,
			"message_id": message_id,
			"chat_id": chat_id,
			"timestamp": now,
			"ack_status": "Pending",
			"sent_by": frappe.session.user,
		}
	).insert(ignore_permissions=True)

	conv.db_set(
		{
			"last_outgoing_at": now,
			"last_message_preview": message[:140],
		},
		update_modified=True,
	)

	frappe.publish_realtime(
		"nexchat_message",
		{
			"conversation": conv.name,
			"message": msg.name,
			"direction": "Outgoing",
			"preview": message[:140],
			"display_name": conv.display_name,
		},
		after_commit=True,
	)
	return msg.name


@frappe.whitelist()
def send_to_number(number, message):
	"""Send to a raw phone number, creating the conversation if needed.
	For doc-event hooks and scripts; UI sends should use send_message."""
	if not frappe.has_permission("WA Conversation", "write"):
		frappe.throw("Not permitted", frappe.PermissionError)

	phone = normalize_number(number)
	if not phone:
		frappe.throw(f"Could not normalize number: {number}")

	if not frappe.db.exists("WA Conversation", phone):
		frappe.get_doc(
			{
				"doctype": "WA Conversation",
				"phone_number": phone,
				"status": "Open",
			}
		).insert(ignore_permissions=True)

	return send_message(phone, message)


def normalize_number(number):
	"""Normalize common Jamaican formats to bare digits with country code."""
	if not number:
		return None
	digits = re.sub(r"\D", "", str(number))
	if len(digits) == 7:
		digits = "1876" + digits
	elif len(digits) == 10 and digits.startswith(("876", "658")):
		digits = "1" + digits
	elif len(digits) >= 11:
		pass
	else:
		return None
	return digits
