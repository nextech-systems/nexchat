# Copyright (c) 2026, Nextech Systems and contributors
# For license information, please see license.txt

import re
import time

import requests
import frappe


def _default_session_id(settings):
	"""Preferred session: default+active record, else any active record,
	else the legacy session_id in settings."""
	sid = frappe.db.get_value(
		"NexChat Session", {"is_default": 1, "active": 1}
	) or frappe.db.get_value("NexChat Session", {"active": 1})
	return sid or settings.session_id


def _gateway(session=None):
	"""Return (settings, base_url, headers) for a session (or the default)."""
	settings = frappe.get_single("NexChat Settings")
	session_id = session or _default_session_id(settings)
	if not (settings.gateway_url and session_id):
		frappe.throw("NexChat Settings incomplete: set Gateway URL and add a Session")
	headers = {
		"X-Api-Key": settings.get_password("api_key") or "",
		"Content-Type": "application/json",
	}
	base = f"{settings.gateway_url}/api/sessions/{session_id}"
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


def _enforce_send_limits(settings, session):
	"""Server-side throttle protecting the number from ban-triggering bursts.
	Applies per session. A short gap is absorbed by sleeping; the per-minute
	cap raises so callers (UI, hooks) get clear backpressure."""
	per_minute = frappe.utils.cint(settings.get("max_per_minute"))
	gap = frappe.utils.cint(settings.get("min_send_gap"))
	session = session or ""

	if per_minute:
		count = frappe.db.sql(
			"""
			SELECT COUNT(*)
			FROM `tabWA Chat Message` m
			JOIN `tabWA Conversation` c ON m.conversation = c.name
			WHERE m.direction = 'Outgoing'
			  AND m.timestamp > DATE_SUB(NOW(), INTERVAL 60 SECOND)
			  AND IFNULL(c.session, '') = %s
			""",
			(session,),
		)[0][0]
		if count >= per_minute:
			frappe.throw(
				f"Sending limit reached ({per_minute}/minute) - wait a moment. "
				"This protects the WhatsApp number from being blocked."
			)

	if gap:
		last = frappe.db.sql(
			"""
			SELECT MAX(m.timestamp)
			FROM `tabWA Chat Message` m
			JOIN `tabWA Conversation` c ON m.conversation = c.name
			WHERE m.direction = 'Outgoing'
			  AND IFNULL(c.session, '') = %s
			""",
			(session,),
		)[0][0]
		if last:
			elapsed = (frappe.utils.now_datetime() - last).total_seconds()
			if elapsed < gap:
				time.sleep(min(gap - elapsed, 10))


@frappe.whitelist()
def send_message(conversation, message, by_bot=False, is_acknowledgment=False):
	"""Send a text message into an existing conversation. Returns the
	WA Chat Message name on success. by_bot=True is for the AI auto-reply
	path only (nexchat.ai_reply) - it skips the claim/lock flow entirely,
	so a bot-answered conversation stays unassigned for a human to pick
	up if the customer needs more, and is never callable by a whitelisted
	client request without going through frappe.has_permission below.
	is_acknowledgment=True (canned "someone will follow up" message) does
	everything a real reply does EXCEPT update last_outgoing_at - this is
	deliberately not counted as answering the customer, so the SLA
	reminder/escalation timers in locks.py keep running normally."""
	by_bot = frappe.utils.cint(by_bot)
	is_acknowledgment = frappe.utils.cint(is_acknowledgment)
	if not by_bot and not frappe.has_permission("WA Conversation", "write"):
		frappe.throw("Not permitted", frappe.PermissionError)
	message = (message or "").strip()
	if not message:
		frappe.throw("Message is empty")
	if len(message) > 4096:
		frappe.throw("Message exceeds 4096 characters")

	conv = frappe.get_doc("WA Conversation", conversation)

	if not by_bot:
		from nexchat.locks import claim_on_reply
		claim_on_reply(conv.name, frappe.session.user)

	chat_id = conv.chat_id
	if not chat_id:
		chat_id = frappe.db.get_value(
			"WA Chat Message",
			{"conversation": conv.name, "direction": "Incoming"},
			"chat_id",
			order_by="timestamp desc",
		) or f"{conv.phone_number}@c.us"

	_settings, base, headers = _gateway(conv.session or None)
	_enforce_send_limits(_settings, conv.session)
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
			"sent_by": None if by_bot else frappe.session.user,
			"is_bot_reply": 1 if by_bot else 0,
		}
	).insert(ignore_permissions=True)

	conv_updates = {"last_message_preview": message[:140]}
	if not is_acknowledgment:
		conv_updates["last_outgoing_at"] = now
	conv.db_set(conv_updates, update_modified=True)

	frappe.publish_realtime(
		"nexchat_message",
		{
			"conversation": conv.name,
			"message": msg.name,
			"direction": "Outgoing",
			"preview": message[:140],
			"display_name": conv.display_name,
			"is_bot_reply": bool(by_bot),
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


def _scope_rows_by_session(rows, user):
	"""Session-based read scoping only meaningfully applies to WhatsApp
	(Website has no gateway-session concept). Conversation Queue doesn't
	carry session itself, so this is enforced in Python against the WA
	rows only - Website rows always pass through untouched."""
	from nexchat.permissions import permitted_sessions
	sess = permitted_sessions(user)
	if sess is None:
		return rows
	wa_allowed = set(
		frappe.get_all("WA Conversation", filters={"session": ["in", sess]}, pluck="name")
	)
	return [r for r in rows if r.get("channel") != "WhatsApp" or r.get("source_name") in wa_allowed]


@frappe.whitelist()
def get_conversations(search=None, tab="all"):
	"""Unified conversation list for the chat center across every channel
	(WhatsApp + Website), newest activity first. Backed by Conversation
	Queue, a read-only mirror kept in sync by each channel's own handler -
	each channel's own doctype (WA Conversation / Web Conversation) stays
	the real source of truth for locking, CRM links and messages."""
	if not frappe.has_permission("Conversation Queue", "read"):
		frappe.throw("Not permitted", frappe.PermissionError)

	filters = {}
	or_filters = None
	me = frappe.session.user

	if search:
		search = f"%{search}%"
		or_filters = [
			["display_name", "like", search],
			["customer", "like", search],
			["source_name", "like", search],
		]
	if tab == "mine":
		filters["assigned_agent"] = me
	elif tab == "unassigned":
		filters["assigned_agent"] = ["in", ["", None]]
	# "all" applies no assignment filter

	rows = frappe.get_all(
		"Conversation Queue",
		filters=filters,
		or_filters=or_filters,
		fields=[
			"channel", "source_doctype", "source_name", "display_name",
			"status", "unread_count", "last_message_preview", "modified",
			"customer", "contact", "lead", "assigned_agent", "last_agent",
			"sla_state", "last_incoming_at", "last_outgoing_at",
		],
		order_by="modified desc",
		limit_page_length=200,
	)
	rows = _scope_rows_by_session(rows, me)

	# WhatsApp-only extras the frontend already displays: resolved_number
	# and the session label. Website rows leave these blank.
	wa_names = [r["source_name"] for r in rows if r["channel"] == "WhatsApp"]
	wa_extra = {}
	if wa_names:
		wa_extra = {
			w.name: w
			for w in frappe.get_all(
				"WA Conversation",
				filters={"name": ["in", wa_names]},
				fields=["name", "resolved_number", "phone_number", "session"],
			)
		}
	session_labels = {
		s.name: s.session_label
		for s in frappe.get_all("NexChat Session", fields=["name", "session_label"])
	}
	agent_names = {}

	convs = []
	for r in rows:
		c = dict(r)
		# keep "name" as the identifier the frontend already sends back on
		# every follow-up call (load_messages, send, lock_action, ...)
		c["name"] = r["source_name"]
		extra = wa_extra.get(r["source_name"], {})
		c["resolved_number"] = extra.get("resolved_number") or extra.get("phone_number")
		c["session_label"] = session_labels.get(extra.get("session")) or ""

		c["sticky_mine"] = bool(not c.get("assigned_agent") and c.get("last_agent") == me)
		c["waiting"] = bool(
			c.get("last_incoming_at")
			and (not c.get("last_outgoing_at") or c["last_outgoing_at"] < c["last_incoming_at"])
		)
		if c.get("assigned_agent") and c["assigned_agent"] not in agent_names:
			agent_names[c["assigned_agent"]] = frappe.get_cached_value(
				"User", c["assigned_agent"], "full_name"
			) or c["assigned_agent"]
		c["assigned_agent_name"] = agent_names.get(c.get("assigned_agent"), "")
		convs.append(c)

	convs.sort(key=lambda c: (not c["sticky_mine"], not c["waiting"]))
	return convs


CHANNEL_MESSAGE_DOCTYPE = {
	"WA Conversation": "WA Chat Message",
	"Web Conversation": "Web Chat Message",
}

# All conversation doctypes NexChat Center knows how to open - wider than
# CHANNEL_MESSAGE_DOCTYPE because Email Conversation doesn't have a
# dedicated message doctype of its own (it uses Frappe's native
# Communication instead, see get_messages below).
VALID_SOURCE_DOCTYPES = ("WA Conversation", "Web Conversation", "Email Conversation")


@frappe.whitelist()
def get_messages(conversation, source_doctype="WA Conversation", limit=50):
	"""Last N messages of a conversation, oldest first. Works for every
	channel - source_doctype picks which pair of doctypes to query, or
	for Email specifically, queries Communication and reshapes it into
	the same shape the other two channels already return."""
	if source_doctype not in VALID_SOURCE_DOCTYPES:
		frappe.throw("Unknown channel")
	if not frappe.has_permission(source_doctype, "read"):
		frappe.throw("Not permitted", frappe.PermissionError)
	if not frappe.db.exists(source_doctype, conversation):
		frappe.throw(f"Conversation not found: {source_doctype} / {conversation}")

	limit_n = min(frappe.utils.cint(limit) or 50, 200)

	if source_doctype == "Email Conversation":
		comms = frappe.get_all(
			"Communication",
			filters={"reference_doctype": source_doctype, "reference_name": conversation},
			fields=["name", "sent_or_received", "content", "communication_date", "custom_is_bot_reply"],
			order_by="communication_date desc",
			limit_page_length=limit_n,
		)
		msgs = [
			{
				"name": c.name,
				"direction": "Incoming" if c.sent_or_received == "Received" else "Outgoing",
				"content_type": "text",
				"message": frappe.utils.strip_html(c.content or ""),
				"media_file": None,
				"timestamp": c.communication_date,
				"sent_by": None,
				"is_bot_reply": c.custom_is_bot_reply,
			}
			for c in comms
		]
		return list(reversed(msgs))

	message_doctype = CHANNEL_MESSAGE_DOCTYPE.get(source_doctype)
	fields = ["name", "direction", "content_type", "message", "media_file", "timestamp", "sent_by", "is_bot_reply"]
	if source_doctype == "WA Conversation":
		fields.append("ack_status")

	msgs = frappe.get_all(
		message_doctype,
		filters={"conversation": conversation},
		fields=fields,
		order_by="timestamp desc",
		limit_page_length=limit_n,
	)
	return list(reversed(msgs))


@frappe.whitelist()
def mark_read(conversation, source_doctype="WA Conversation"):
	"""Reset the unread counter when an agent opens a thread - any channel."""
	if source_doctype not in VALID_SOURCE_DOCTYPES:
		frappe.throw("Unknown channel")
	if not frappe.has_permission(source_doctype, "write"):
		frappe.throw("Not permitted", frappe.PermissionError)
	if frappe.db.exists(source_doctype, conversation):
		frappe.db.set_value(
			source_doctype, conversation, "unread_count", 0,
			update_modified=False,
		)
		from nexchat.locks import sync_conversation
		sync_conversation(source_doctype, conversation)
	return True


@frappe.whitelist()
def workload_counts():
	"""Top-bar counts: mine, unassigned, overdue (escalated) - across every
	channel, session scoped the same way get_conversations is."""
	if not frappe.has_permission("Conversation Queue", "read"):
		frappe.throw("Not permitted", frappe.PermissionError)
	me = frappe.session.user

	rows = frappe.get_all(
		"Conversation Queue",
		filters={"status": "Open"},
		fields=["channel", "source_name", "assigned_agent", "sla_state"],
	)
	rows = _scope_rows_by_session(rows, me)

	mine = sum(1 for r in rows if r["assigned_agent"] == me)
	unassigned = sum(1 for r in rows if not r["assigned_agent"])
	overdue = sum(1 for r in rows if r["sla_state"] == "Escalated")
	return {"mine": mine, "unassigned": unassigned, "overdue": overdue}


@frappe.whitelist()
def customer_summary(conversation, source_doctype="WA Conversation"):
	"""Light inline summary for the right column. Heavy detail stays on the
	customer form (we just deep-link there). Works for every channel -
	all three doctypes share the customer/contact/lead/display_name fields."""
	if source_doctype not in VALID_SOURCE_DOCTYPES:
		frappe.throw("Unknown channel")
	if not frappe.has_permission(source_doctype, "read"):
		frappe.throw("Not permitted", frappe.PermissionError)
	conv = frappe.get_doc(source_doctype, conversation)
	out = {
		"customer": conv.customer,
		"contact": conv.contact,
		"lead": conv.lead,
		"display_name": conv.display_name,
		"resolved_number": getattr(conv, "resolved_number", None) or getattr(conv, "phone_number", None),
	}
	if not conv.customer:
		return out

	cust = frappe.get_cached_doc("Customer", conv.customer)
	out["customer_name"] = cust.customer_name

	# outstanding balance via GL (party balance)
	try:
		from erpnext.accounts.utils import get_balance_on
		out["balance"] = get_balance_on(
			party_type="Customer", party=conv.customer
		)
	except Exception:
		out["balance"] = None

	out["open_orders"] = frappe.db.count(
		"Sales Order",
		{"customer": conv.customer, "docstatus": 1, "status": ["not in", ["Closed", "Completed"]]},
	)
	last_so = frappe.get_all(
		"Sales Order",
		filters={"customer": conv.customer, "docstatus": 1},
		fields=["transaction_date"],
		order_by="transaction_date desc",
		limit_page_length=1,
	)
	out["last_order_date"] = last_so[0].transaction_date if last_so else None
	return out
