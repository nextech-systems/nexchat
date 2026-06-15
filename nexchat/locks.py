# Copyright (c) 2026, Nextech Systems and contributors
# For license information, please see license.txt

"""Conversation locking, agent availability, sticky routing and SLA escalation.

Lock model:
- soft lock: one assigned_agent at a time, set atomically on first reply
- last_agent persists across release, enabling sticky preference
- SLA scheduler (every minute) nudges then escalates unanswered threads
"""

import frappe
from frappe.utils import now_datetime, time_diff_in_seconds, get_url_to_form


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
	for name in frappe.get_all(
		"WA Conversation",
		filters={"assigned_agent": user, "status": "Open"},
		pluck="name",
	):
		frappe.db.set_value(
			"WA Conversation", name, "assigned_agent", None, update_modified=False
		)
	frappe.db.commit()


# ----- claim / release ----------------------------------------------------

@frappe.whitelist()
def claim(conversation):
	"""Atomically claim an unassigned conversation. Returns the winning agent.
	If already claimed by someone else, raises with their name."""
	if not frappe.has_permission("WA Conversation", "write"):
		frappe.throw("Not permitted", frappe.PermissionError)

	me = frappe.session.user
	# atomic: only set if currently unassigned
	updated = frappe.db.sql(
		"""
		UPDATE `tabWA Conversation`
		SET assigned_agent = %(me)s, claimed_at = %(now)s, last_agent = %(me)s
		WHERE name = %(name)s AND IFNULL(assigned_agent, '') = ''
		""",
		{"me": me, "now": now_datetime(), "name": conversation},
	)
	frappe.db.commit()

	current = frappe.db.get_value("WA Conversation", conversation, "assigned_agent")
	if current != me:
		owner = frappe.get_cached_value("User", current, "full_name") or current
		frappe.throw(f"Already claimed by {owner}")
	frappe.publish_realtime("nexchat_lock", {"conversation": conversation, "agent": me}, after_commit=True)
	return me


@frappe.whitelist()
def release(conversation):
	"""Release a claim. last_agent is preserved for sticky routing."""
	if not frappe.has_permission("WA Conversation", "write"):
		frappe.throw("Not permitted", frappe.PermissionError)
	frappe.db.set_value(
		"WA Conversation", conversation, "assigned_agent", None, update_modified=False
	)
	frappe.publish_realtime("nexchat_lock", {"conversation": conversation, "agent": None}, after_commit=True)
	return True


@frappe.whitelist()
def take_over(conversation):
	"""Supervisor override: claim regardless of current lock."""
	roles = frappe.get_roles(frappe.session.user)
	if "NexChat Supervisor" not in roles and "System Manager" not in roles:
		frappe.throw("Supervisor only", frappe.PermissionError)
	me = frappe.session.user
	frappe.db.set_value(
		"WA Conversation",
		conversation,
		{"assigned_agent": me, "claimed_at": now_datetime(), "last_agent": me, "sla_state": "OK"},
		update_modified=False,
	)
	frappe.publish_realtime("nexchat_lock", {"conversation": conversation, "agent": me}, after_commit=True)
	return me


def claim_on_reply(conversation, user):
	"""Called from send_message: first reply claims if unassigned."""
	current = frappe.db.get_value("WA Conversation", conversation, "assigned_agent")
	if not current:
		frappe.db.sql(
			"""
			UPDATE `tabWA Conversation`
			SET assigned_agent = %(u)s, claimed_at = %(now)s, last_agent = %(u)s
			WHERE name = %(name)s AND IFNULL(assigned_agent, '') = ''
			""",
			{"u": user, "now": now_datetime(), "name": conversation},
		)
	else:
		# replying always refreshes last_agent
		frappe.db.set_value(
			"WA Conversation", conversation, "last_agent", user, update_modified=False
		)


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

	# candidates: open, customer waiting (last_incoming_at later than last_outgoing_at)
	candidates = frappe.get_all(
		"WA Conversation",
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
			_escalate(c, settings, waited)
		elif waited >= first and c.sla_state == "OK":
			_remind(c, now)


def _remind(c, now):
	frappe.db.set_value(
		"WA Conversation", c.name,
		{"sla_state": "Reminded", "reminded_at": now},
		update_modified=False,
	)
	target = c.assigned_agent or c.last_agent
	if target:
		_notify(
			target,
			f"WhatsApp waiting: {c.display_name or c.name}",
			f"A customer message has been waiting over the reminder threshold.",
			c.name,
		)
	frappe.db.commit()


def _escalate(c, settings, waited):
	# lift the lock so anyone can take it
	frappe.db.set_value(
		"WA Conversation", c.name,
		{"sla_state": "Escalated", "assigned_agent": None},
		update_modified=False,
	)
	# notify supervisors in-app + email
	supervisor_role = settings.supervisor_role or "NexChat Supervisor"
	supervisors = _role_users(supervisor_role)
	subject = f"NexChat escalation: {c.display_name or c.name} unanswered {int(waited)}m"
	msg = (
		f"Conversation {c.display_name or c.name} has gone unanswered for "
		f"{int(waited)} minutes and has been released from "
		f"{c.assigned_agent or c.last_agent or 'no agent'}.<br><br>"
		f"<a href='{get_url_to_form('WA Conversation', c.name)}'>Open conversation</a>"
	)
	for sup in supervisors:
		_notify(sup, subject, msg, c.name)
	if supervisors:
		try:
			frappe.sendmail(recipients=supervisors, subject=subject, message=msg)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "NexChat escalation email")
	frappe.publish_realtime("nexchat_escalation", {"conversation": c.name}, after_commit=True)
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


def _notify(user, subject, message, conversation):
	try:
		notif = frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": user,
				"type": "Alert",
				"subject": subject,
				"email_content": message,
				"document_type": "WA Conversation",
				"document_name": conversation,
			}
		)
		notif.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "NexChat notify")


# ----- sticky routing helper (used by webhook on incoming) ---------------

def sticky_target(conversation_name):
	"""Return the last agent if they're available, else None.
	The conversation surfaces in their queue first during the grace window."""
	last_agent = frappe.db.get_value("WA Conversation", conversation_name, "last_agent")
	if last_agent and is_available(last_agent):
		return last_agent
	return None
