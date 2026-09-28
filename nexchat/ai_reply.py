# Copyright (c) 2026, Nextech Systems and contributors
# For license information, please see license.txt

"""AI auto-reply engine.

Safety model, in order:
1. Deterministic gate BEFORE calling the LLM at all - master switch,
   conversation must be unclaimed (a human already engaged always wins),
   and the message must not match any escalation keyword. None of this
   depends on the model's own judgment, since LLMs are not reliable judges
   of their own uncertainty.
2. The LLM itself is instructed to reply with exactly "ESCALATE" when it
   isn't confident - caught and treated as "do nothing" here.
3. A second, independent check scans the model's own reply for hedging
   language as a backstop against the model ignoring instruction 2.

Bot replies deliberately skip the claim/lock flow (see api.send_message
and website.send_reply's by_bot parameter) so a conversation the bot
answered stays unassigned - a human can still pick it up normally if the
customer needs more help. Every failure here is caught and logged, never
raised, since this always runs from a background job triggered by normal
message receipt - a bug here must never break that.
"""

import frappe
import requests

DEFAULT_SYSTEM_PROMPT = (
	"You are a helpful, concise customer service assistant for a hardware and "
	"building supplies company. Only answer general questions: greetings, "
	"business hours, location, and what general categories of products the "
	"business sells. If the customer asks about specific stock levels, "
	"pricing, order status, payments, invoices, refunds, complaints, "
	"deliveries, quotations, or anything requiring account-specific "
	"information, do NOT attempt to answer it yourself - reply with exactly "
	"the single word ESCALATE and nothing else. Keep real answers under 3 "
	"sentences. Never invent a fact you are not certain of - if in doubt, "
	"reply ESCALATE."
)

DEFAULT_ESCALATION_KEYWORDS = [
	"price", "cost", "how much", "stock", "available", "availability",
	"order", "invoice", "payment", "paid", "pay", "refund", "complaint",
	"cancel", "return", "account", "balance", "urgent", "quote",
	"quotation", "delivery", "deliver", "receipt", "warranty", "broken",
	"damaged", "wrong item",
]

DEFAULT_ESCALATION_ACK = (
	"Thanks for reaching out! A team member will follow up with you shortly."
)

UNCERTAINTY_MARKERS = (
	"i don't know", "i do not know", "i'm not sure", "i am not sure",
	"not certain", "cannot help", "can't help", "unable to assist",
	"i'm unable", "i am unable", "please contact", "speak to a",
	"speak with a", "connect you with",
)


def _settings():
	return frappe.get_single("NexChat Settings")


def _escalation_keywords(settings):
	raw = settings.get("ai_escalation_keywords") or ""
	if raw.strip():
		return [k.strip().lower() for k in raw.split(",") if k.strip()]
	return DEFAULT_ESCALATION_KEYWORDS


def _ai_eligible(conv):
	"""Base eligibility, independent of message content: feature on,
	configured, and no human already owns this thread. Shared by the
	pre-LLM gate and the post-generation re-check so both agree on
	exactly what "eligible" means."""
	settings = _settings()
	if not settings.get("ai_enabled"):
		return False
	if not settings.get("ai_ollama_url"):
		return False
	if conv.get("assigned_agent"):
		return False
	return True


def should_attempt_auto_reply(conversation_doc, message_text):
	"""Deterministic, fast, pre-LLM gate. No network call happens unless
	this returns True."""
	if not _ai_eligible(conversation_doc):
		return False

	text = (message_text or "").strip().lower()
	if not text:
		return False

	settings = _settings()
	for kw in _escalation_keywords(settings):
		if kw in text:
			return False

	return True


def generate_reply(message_text, settings=None):
	"""Call Ollama. Returns the reply text, or None if the model escalated,
	errored, or its reply looks like hedging rather than a real answer."""
	settings = settings or _settings()
	url = (settings.get("ai_ollama_url") or "").rstrip("/")
	model = settings.get("ai_model") or "llama3.1:8b"
	system_prompt = settings.get("ai_system_prompt") or DEFAULT_SYSTEM_PROMPT
	max_chars = frappe.utils.cint(settings.get("ai_max_response_chars")) or 500

	if not url:
		return None

	try:
		resp = requests.post(
			f"{url}/api/generate",
			json={
				"model": model,
				"system": system_prompt,
				"prompt": message_text,
				"stream": False,
				"keep_alive": "30m",
			},
			timeout=120,
		)
		resp.raise_for_status()
		data = resp.json()
	except requests.exceptions.RequestException:
		frappe.log_error(frappe.get_traceback(), "NexChat AI Reply")
		return None

	reply = (data.get("response") or "").strip()
	if not reply:
		return None
	if "ESCALATE" in reply.upper():
		return None

	lowered = reply.lower()
	if any(marker in lowered for marker in UNCERTAINTY_MARKERS):
		return None

	return reply[:max_chars]


def _dispatch_send(source_doctype, conv, text, by_bot, is_acknowledgment):
	"""Single place that knows how to actually deliver a message on each
	channel - used by both the real AI reply and the canned escalation
	acknowledgment, so a fourth channel only needs one new branch here."""
	if source_doctype == "WA Conversation":
		from nexchat.api import send_message
		send_message(conv.name, text, by_bot=by_bot, is_acknowledgment=is_acknowledgment)
	elif source_doctype == "Web Conversation":
		from nexchat.website import send_reply
		send_reply(conv.name, text, by_bot=by_bot, is_acknowledgment=is_acknowledgment)
	elif source_doctype == "Email Conversation":
		from nexchat.email_channel import send_email_reply
		send_email_reply(conv.name, text, by_bot=by_bot, is_acknowledgment=is_acknowledgment)


def _send_escalation_ack(source_doctype, conv):
	"""One-time canned "someone will follow up" message for a genuinely
	escalated conversation - so the customer isn't left staring at
	silence. Reuses the real send path (so WhatsApp still actually
	relays it through the gateway, not just a DB row) but passes
	is_acknowledgment=True so last_outgoing_at is NOT updated - this is
	deliberately NOT treated as a real answer, so the SLA reminder/
	escalation timers in locks.py keep running exactly as if nothing
	had been sent, and a human still gets alerted on schedule."""
	if conv.get("ai_ack_sent"):
		return
	settings = _settings()
	ack_text = settings.get("ai_escalation_ack_message") or DEFAULT_ESCALATION_ACK

	_dispatch_send(source_doctype, conv, ack_text, by_bot=True, is_acknowledgment=True)

	frappe.db.set_value(source_doctype, conv.name, "ai_ack_sent", 1, update_modified=False)


def maybe_auto_reply(source_doctype, conversation_name, message_text):
	"""Entry point, called via frappe.enqueue right after a fresh incoming
	message is stored (WhatsApp, Website, or Email). Deliberately
	swallows every exception - a bug in the AI path must never surface
	as a failure of the actual message-receipt flow that triggered it."""
	try:
		if not frappe.db.exists(source_doctype, conversation_name):
			return
		conv = frappe.get_doc(source_doctype, conversation_name)

		text = (message_text or "").strip()
		if not text:
			return

		if not should_attempt_auto_reply(conv, message_text):
			# Covers both "feature off / already claimed" (no ack wanted)
			# and "matched an escalation keyword" (ack wanted) - only the
			# latter is still AI-eligible at this point.
			if _ai_eligible(conv):
				_send_escalation_ack(source_doctype, conv)
			return

		reply = generate_reply(message_text)
		if not reply:
			# Model itself escalated, errored, or timed out. Minutes may
			# have passed on a slow model call, so re-check eligibility
			# fresh before sending the ack - a human could have claimed
			# it in the meantime, in which case we stay out of the way.
			conv.reload()
			if _ai_eligible(conv):
				_send_escalation_ack(source_doctype, conv)
			return

		conv.reload()
		if not _ai_eligible(conv):
			return

		_dispatch_send(source_doctype, conv, reply, by_bot=True, is_acknowledgment=False)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "NexChat AI Reply")
