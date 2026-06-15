# Copyright (c) 2026, Nextech Systems and contributors
# For license information, please see license.txt

"""Server-side enforcement of session-scoped conversation visibility.

A user sees a conversation if and only if they have a User Permission for
that conversation's session. Users with no session permission at all are
treated as unrestricted (supervisors, admins, single-queue shops) so the
feature is opt-in: scoping only kicks in once you start assigning coverage.
"""

import frappe


def conversation_query(user):
	"""permission_query_conditions hook for WA Conversation."""
	if not user:
		user = frappe.session.user
	if user == "Administrator":
		return ""

	roles = frappe.get_roles(user)
	if "System Manager" in roles or "NexChat Supervisor" in roles:
		return ""

	permitted = frappe.get_all(
		"User Permission",
		filters={"user": user, "allow": "NexChat Session"},
		pluck="for_value",
	)
	if not permitted:
		return ""  # no scoping configured for this user -> see all

	values = ", ".join(frappe.db.escape(p) for p in permitted)
	return f"`tabWA Conversation`.`session` in ({values})"


def has_conversation_permission(doc, user=None, permission_type=None):
	"""permission hook for direct WA Conversation access (form, API get)."""
	if not user:
		user = frappe.session.user
	if user == "Administrator":
		return True

	roles = frappe.get_roles(user)
	if "System Manager" in roles or "NexChat Supervisor" in roles:
		return True

	permitted = frappe.get_all(
		"User Permission",
		filters={"user": user, "allow": "NexChat Session"},
		pluck="for_value",
	)
	if not permitted:
		return True

	session = getattr(doc, "session", None) or (
		doc.get("session") if isinstance(doc, dict) else None
	)
	return session in permitted


def permitted_sessions(user=None):
	"""Return a list of session names the user is restricted to, or None if
	unrestricted (supervisor/admin/no-coverage). Used by get_conversations
	since frappe.get_all does not auto-apply permission_query conditions."""
	if not user:
		user = frappe.session.user
	if user == "Administrator":
		return None
	roles = frappe.get_roles(user)
	if "System Manager" in roles or "NexChat Supervisor" in roles:
		return None
	permitted = frappe.get_all(
		"User Permission",
		filters={"user": user, "allow": "NexChat Session"},
		pluck="for_value",
	)
	return permitted or None
