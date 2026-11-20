# Copyright (c) 2026, Nextech Systems and contributors
# For license information, please see license.txt

"""Conversation locking, agent availability, sticky routing and SLA escalation.

Lock model:
- soft lock: one assigned_agent at a time, set atomically on first reply
- last_agent persists across release, enabling sticky preference
- SLA scheduler (every minute) nudges then escalates unanswered threads

Channel-agnostic by design: every function takes a doctype (defaulting to
WA Conversation so existing WhatsApp call sites are unaffected) so a future
channel (e.g. Web Conversation) can reuse claim/release/SLA logic as long as
its doctype exposes the same assigned_agent/last_agent/sla_state/status/
last_incoming_at/last_outgoing_at fields WA Conversation already has. Add
its name to CHANNEL_DOCTYPES below to bring it into the SLA scheduler and
the Conversation Queue mirror.
"""

import frappe
from nexchat.web_security import staff_doc, publish
from frappe.utils import now_datetime, time_diff_in_seconds, get_url_to_form
from nexchat.nexchat.doctype.conversation_queue.conversation_queue import sync_queue

# doctype -> channel label used on the Conversation Queue mirror row.
# Add an entry here (and nowhere else in this file) when a new channel's
# conversation doctype is ready to share this locking/SLA machinery.
CHANNEL_DOCTYPES = {
	"WA Conversation": "WhatsApp",
	"Web Conversation": "Website",
}


# ----- agent availability -------------------------------------------------

def _agent_row(user):
	if not frappe.db.exists("NexChat Agent", user):
		frappe.get_doc(
			{"doctype": "NexChat Agent", "user": user, "availability": "Available"}
		).insert(ignore_permissions=True)
	return frappe.get_doc("NexChat Agent", user)


@frappe.whitelist()
def set_availability(status):
	"""Set the current user's availability. Going Away/Off releases their
	open claims so customers are not stranded behind an absent agent."""
	if status not in ("Available", "Away", "Off"):
		frappe.throw("Invalid status")
	row = _agent_row(frappe.session.user)
	row.db_set({"availability": status, "last_seen": now_datetime()}, update_modified=False)
	if status in ("Away", "Off"):
		_release_all_for(frappe.session.user)
	return status


@frappe.whitelist()
def heartbeat():
	"""Lightweight presence ping from the open chat page."""
	row = _agent_row(frappe.session.user)
	row.db_set("last_seen", now_datetime(), update_modified=False)
	return row.availability


def is_available(user):
	if not user:
		return False
	status = frappe.db.get_value("NexChat Agent", user, "availability")
	return status == "Available"


def _release_all_for(user):
	"""Release every open claim this user holds, across ALL registered
	channels - not just WhatsApp - so going Away/Off never strands a
	customer on any channel behind an absent agent."""
	for doctype in CHANNEL_DOCTYPES:
		for name in frappe.get_all(
			doctype,
			filters={"assigned_agent": user, "status": "Open"},
			pluck="name",
		):
			frappe.db.set_value(
				doctype, name, "assigned_agent", None, update_modified=False
			)
			_sync(doctype, name)
	frappe.db.commit()


# ----- claim / release ----------------------------------------------------

def _check_channel_doctype(doctype):
	"""doctype reaches these functions from whitelisted (client-callable)
	methods, so it must be checked against a fixed allowlist before any use
	in raw SQL - never trust it as a literal table-name fragment."""
	if doctype not in CHANNEL_DOCTYPES:
		frappe.throw(f"Unknown conversation channel doctype: {doctype}")


def _prepare_web_claim(doctype, conversation):
	if doctype != "Web Conversation":
		return True
	from nexchat.website import _request_handover, _complete
	conv = _request_handover(conversation)
	if conv.status != "Open":
		frappe.throw("This chat has ended. The visitor must start a new chat.")
	return _complete(conv)


@frappe.whitelist()
def claim(conversation, doctype="WA Conversation"):
	"""Atomically claim an unassigned conversation. Returns the winning agent.
	If already claimed by someone else, raises with their name."""
	_check_channel_doctype(doctype)
	staff_doc(doctype, conversation, "write")
	if not _prepare_web_claim(doctype, conversation):
		return {"requires_details": True}

	me = frappe.session.user
	# atomic: only set if currently unassigned
	frappe.db.sql(
		f"""
		UPDATE `tab{doctype}`
		SET assigned_agent = %(me)s, claimed_at = %(now)s, last_agent = %(me)s
		WHERE name = %(name)s AND IFNULL(assigned_agent, '') = ''
		""",
		{"me": me, "now": now_datetime(), "name": conversation},
	)
	frappe.db.commit()

	current = frappe.db.get_value(doctype, conversation, "assigned_agent")
	if current != me:
		owner = frappe.get_cached_value("User", current, "full_name") or current
		frappe.throw(f"Already claimed by {owner}")
	_sync(doctype, conversation)
	publish("nexchat_lock", {"conversation": conversation, "source_doctype": doctype, "agent": me}, after_commit=True)
	return me


@frappe.whitelist()
def release(conversation, doctype="WA Conversation"):
	"""Release a claim. last_agent is preserved for sticky routing."""
	_check_channel_doctype(doctype)
	staff_doc(doctype, conversation, "write")
	owner = frappe.db.get_value(doctype, conversation, "assigned_agent")
	roles = frappe.get_roles(frappe.session.user)
	if owner and owner != frappe.session.user and not {"NexChat Supervisor", "System Manager"}.intersection(roles):
		frappe.throw("Only the assigned agent or a supervisor may release this conversation", frappe.PermissionError)

	frappe.db.set_value(
		doctype, conversation, "assigned_agent", None, update_modified=False
	)
	_sync(doctype, conversation)
	publish("nexchat_lock", {"conversation": conversation, "source_doctype": doctype, "agent": None}, after_commit=True)
	return True


@frappe.whitelist()
def take_over(conversation, doctype="WA Conversation"):
	"""Supervisor override: claim regardless of current lock."""
	_check_channel_doctype(doctype)
	staff_doc(doctype, conversation, "write")
	roles = frappe.get_roles(frappe.session.user)
	if "NexChat Supervisor" not in roles and "System Manager" not in roles:
		frappe.throw("Supervisor only", frappe.PermissionError)
	if not _prepare_web_claim(doctype, conversation):
		return {"requires_details": True}
	me = frappe.session.user
	frappe.db.set_value(
		doctype,
		conversation,
		{"assigned_agent": me, "claimed_at": now_datetime(), "last_agent": me, "sla_state": "OK"},
		update_modified=False,
	)
	_sync(doctype, conversation)
	publish("nexchat_lock", {"conversation": conversation, "source_doctype": doctype, "agent": me}, after_commit=True)
	return me


def claim_on_reply(conversation, user, doctype="WA Conversation"):
	"""Called from send_message: first reply claims if unassigned.
	Not whitelisted directly, but still validated since it reaches raw SQL."""
	_check_channel_doctype(doctype)
	staff_doc(doctype, conversation, "write", user=user)
	if not _prepare_web_claim(doctype, conversation):
		frappe.throw("Waiting for the visitor to provide name, email and phone.")
	current = frappe.db.get_value(doctype, conversation, "assigned_agent")
	if not current:
		frappe.db.sql(
			f"""
			UPDATE `tab{doctype}`
			SET assigned_agent = %(u)s, claimed_at = %(now)s, last_agent = %(u)s
			WHERE name = %(name)s AND IFNULL(assigned_agent, '') = ''
			""",
			{"u": user, "now": now_datetime(), "name": conversation},
		)
	else:
		if current != user:
			frappe.throw("Claim or take over the conversation before replying", frappe.PermissionError)
		# replying always refreshes last_agent
		frappe.db.set_value(
			doctype, conversation, "last_agent", user, update_modified=False
		)
	if frappe.db.get_value(doctype, conversation, "assigned_agent") != user:
		frappe.throw("Conversation was claimed by another agent", frappe.PermissionError)

	_sync(doctype, conversation)


def sync_conversation(doctype, name):
	"""Mirror a conversation's current state onto its Conversation Queue row.
	Public entry point - call this from any channel's webhook/CRM code after
	writing to its own conversation doc. Best-effort: a mirror failure
	should never break the actual lock/SLA/CRM action that already
	committed, so exceptions are logged and swallowed."""
	channel = CHANNEL_DOCTYPES.get(doctype)
	if not channel:
		return
	try:
		sync_queue(doctype, name, channel)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "NexChat Conversation Queue sync")


# short internal alias used throughout this file
_sync = sync_conversation


# ----- SLA scheduler ------------------------------------------------------

def run_sla_checks():
	"""Scheduled every minute. Two stages keyed off the newest message being
	an unanswered incoming one: reminder, then escalation (lift lock + email)."""
	settings = frappe.get_single("NexChat Settings")
	if not settings.enable_sla:
		return

	first = frappe.utils.cint(settings.first_reminder_minutes) or 5
	escalate = frappe.utils.cint(settings.escalation_minutes) or 15
	now = now_datetime()

	# Runs across every registered channel doctype - WhatsApp today, more
	# later - each checked independently since they're separate tables.
	for doctype, channel in CHANNEL_DOCTYPES.items():
		# candidates: open, customer waiting (last_incoming_at later than last_outgoing_at)
		candidates = frappe.get_all(
			doctype,
			filters={"status": "Open"},
			fields=[
				"name", "display_name", "assigned_agent", "last_agent",
				"last_incoming_at", "last_outgoing_at", "sla_state", "reminded_at",
			],
		)
		for c in candidates:
			if not c.last_incoming_at:
				continue
			if c.last_outgoing_at and c.last_outgoing_at >= c.last_incoming_at:
				continue  # answered
			waited = time_diff_in_seconds(now, c.last_incoming_at) / 60.0

			if waited >= escalate and c.sla_state != "Escalated":
				_escalate(c, settings, waited, doctype, channel)
			elif waited >= first and c.sla_state == "OK":
				_remind(c, now, doctype)


def _remind(c, now, doctype="WA Conversation"):
	if doctype == "Web Conversation":
		from nexchat.website import _lock_conversation
		c = _lock_conversation(c.name)
		from nexchat.website import _complete, _handover_pending
		if c.status != "Open" or not _complete(c) or not _handover_pending(c):
			return
	frappe.db.set_value(
		doctype, c.name,
		{"sla_state": "Reminded", "reminded_at": now},
		update_modified=False,
	)
	_sync(doctype, c.name)
	target = c.assigned_agent or c.last_agent
	if target:
		_notify(
			target,
			f"{CHANNEL_DOCTYPES.get(doctype, doctype)} waiting: {c.display_name or c.name}",
			f"A customer message has been waiting over the reminder threshold.",
			c.name,
			doctype,
		)
	frappe.db.commit()


def _escalate(c, settings, waited, doctype="WA Conversation", channel=None):
	if doctype == "Web Conversation":
		from nexchat.website import _lock_conversation
		c = _lock_conversation(c.name)
		from nexchat.website import _complete, _handover_pending
		if c.status != "Open" or not _complete(c) or not _handover_pending(c):
			return
	channel = channel or CHANNEL_DOCTYPES.get(doctype, doctype)
	# lift the lock so anyone can take it
	frappe.db.set_value(
		doctype, c.name,
		{"sla_state": "Escalated", "assigned_agent": None},
		update_modified=False,
	)
	_sync(doctype, c.name)
	# notify supervisors in-app + email
	supervisor_role = settings.supervisor_role or "NexChat Supervisor"
	supervisors = _role_users(supervisor_role)
	subject = f"NexChat escalation ({channel}): {c.display_name or c.name} unanswered {int(waited)}m"
	msg = (
		f"Conversation {c.display_name or c.name} has gone unanswered for "
		f"{int(waited)} minutes and has been released from "
		f"{c.assigned_agent or c.last_agent or 'no agent'}.<br><br>"
		f"<a href='{get_url_to_form(doctype, c.name)}'>Open conversation</a>"
	)
	for sup in supervisors:
		_notify(sup, subject, msg, c.name, doctype)
	if supervisors:
		try:
			frappe.sendmail(recipients=supervisors, subject=subject, message=msg)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "NexChat escalation email")
	publish("nexchat_escalation", {"conversation": c.name, "source_doctype": doctype}, after_commit=True)
	frappe.db.commit()


def _role_users(role):
	return [
		u.parent
		for u in frappe.get_all(
			"Has Role",
			filters={"role": role, "parenttype": "User"},
			fields=["parent"],
		)
		if frappe.db.get_value("User", u.parent, "enabled")
	]


def _notify(user, subject, message, conversation, doctype="WA Conversation"):
	try:
		notif = frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": user,
				"type": "Alert",
				"subject": subject,
				"email_content": message,
				"document_type": doctype,
				"document_name": conversation,
			}
		)
		notif.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "NexChat notify")


# ----- sticky routing helper (used by webhook on incoming) ---------------

def sticky_target(conversation_name, doctype="WA Conversation"):
	"""Return the last agent if they're available, else None.
	The conversation surfaces in their queue first during the grace window."""
	_check_channel_doctype(doctype)
	last_agent = frappe.db.get_value(doctype, conversation_name, "last_agent")
	if last_agent and is_available(last_agent):
		return last_agent
	return None
