# Copyright (c) 2026, Nextech Systems and contributors
# For license information, please see license.txt

"""CRM linking for NexChat conversations.

Matching strategy (built for real-world MACL data):
- normalize both sides to bare digits
- compare on the LAST 7 DIGITS (the local number), which survives
  country-code and formatting differences (876-402-5061 vs 18764025061)
- values shorter than 7 digits or all zeros are junk and never match
- auto-link only on an unambiguous single Contact match; multiple
  candidates are left for a human via the Link dialog
- @lid privacy conversations are resolved to a real number through the
  gateway's contacts endpoint, cached in resolved_number
"""

import re

import requests
import frappe

from nexchat.api import _gateway, normalize_number


def _digits(value):
	return re.sub(r"\D", "", value or "")


def _usable(phone):
	d = _digits(phone)
	return d if len(d) >= 7 and set(d) != {"0"} else ""


def resolve_real_number(conv):
	"""Best known real phone number for a conversation, or ''.
	Resolves @lid addresses via the gateway once and caches the result."""
	if conv.resolved_number:
		return conv.resolved_number

	chat_id = conv.chat_id or ""
	if "@lid" not in chat_id:
		return conv.phone_number

	try:
		_settings, base, headers = _gateway(conv.session or None)
		resp = requests.get(f"{base}/contacts/{chat_id}", headers=headers, timeout=8)
		resp.raise_for_status()
		payload = resp.json() or {}
		resolved_id = payload.get("id") or ""
		digits = _digits(resolved_id.split("@")[0])
		if _usable(digits):
			updates = {"resolved_number": digits}
			book_name = (payload.get("name") or "").strip()
			if book_name and not conv.display_name:
				updates["display_name"] = book_name
			conv.db_set(updates, update_modified=False)
			return digits
	except (requests.exceptions.RequestException, ValueError):
		pass
	return ""


def find_matches(phone):
	"""Match a canonical-digit phone against Contacts, Leads and Customers
	on the last 7 digits. Returns dict of candidate lists."""
	phone = _usable(phone)
	if not phone:
		return {"contacts": [], "leads": [], "customers": []}
	last7 = phone[-7:]
	like = f"%{last7}"

	contacts = frappe.db.sql(
		"""
		SELECT name, first_name, last_name, mobile_no, phone
		FROM `tabContact`
		WHERE REGEXP_REPLACE(IFNULL(mobile_no, ''), '[^0-9]', '') LIKE %(p)s
		   OR REGEXP_REPLACE(IFNULL(phone, ''), '[^0-9]', '') LIKE %(p)s
		LIMIT 6
		""",
		{"p": like},
		as_dict=True,
	)
	leads = frappe.db.sql(
		"""
		SELECT name, lead_name, company_name, mobile_no, phone
		FROM `tabLead`
		WHERE REGEXP_REPLACE(IFNULL(mobile_no, ''), '[^0-9]', '') LIKE %(p)s
		   OR REGEXP_REPLACE(IFNULL(phone, ''), '[^0-9]', '') LIKE %(p)s
		LIMIT 6
		""",
		{"p": like},
		as_dict=True,
	)
	customers = frappe.db.sql(
		"""
		SELECT name, customer_name, mobile_no
		FROM `tabCustomer`
		WHERE REGEXP_REPLACE(IFNULL(mobile_no, ''), '[^0-9]', '') LIKE %(p)s
		LIMIT 6
		""",
		{"p": like},
		as_dict=True,
	)
	return {"contacts": contacts, "leads": leads, "customers": customers}


def customer_for_contact(contact_name):
	return frappe.db.get_value(
		"Dynamic Link",
		{
			"parenttype": "Contact",
			"parent": contact_name,
			"link_doctype": "Customer",
		},
		"link_name",
	)


def try_auto_link(conversation_name):
	"""Background job: link a new conversation when the match is unambiguous."""
	try:
		conv = frappe.get_doc("WA Conversation", conversation_name)
	except frappe.DoesNotExistError:
		return
	if conv.customer or conv.contact or conv.lead:
		return

	real = resolve_real_number(conv)
	norm = normalize_number(real) or _usable(real)
	if not norm:
		return

	matches = find_matches(norm)
	updates = {}
	contacts = matches["contacts"]
	if len(contacts) == 1:
		c = contacts[0]
		updates["contact"] = c.name
		customer = customer_for_contact(c.name)
		if customer:
			updates["customer"] = customer
		if not conv.display_name:
			full = " ".join(filter(None, [c.first_name, c.last_name])).strip()
			if full:
				updates["display_name"] = full
	elif not contacts and len(matches["customers"]) == 1:
		updates["customer"] = matches["customers"][0].name
	elif not contacts and len(matches["leads"]) == 1:
		updates["lead"] = matches["leads"][0].name

	if updates:
		conv.db_set(updates, update_modified=False)
		frappe.publish_realtime(
			"nexchat_message",
			{"conversation": conv.name, "direction": "Meta", "preview": ""},
			after_commit=True,
		)


@frappe.whitelist()
def get_link_options(conversation):
	"""Candidates + resolved number for the Link dialog."""
	if not frappe.has_permission("WA Conversation", "read"):
		frappe.throw("Not permitted", frappe.PermissionError)
	conv = frappe.get_doc("WA Conversation", conversation)
	real = resolve_real_number(conv)
	norm = normalize_number(real) or _usable(real)
	matches = find_matches(norm) if norm else {"contacts": [], "leads": [], "customers": []}
	matches["resolved_number"] = norm
	return matches


@frappe.whitelist()
def link_conversation(conversation, doctype, docname):
	"""Manually link a conversation to a Customer, Contact or Lead."""
	if not frappe.has_permission("WA Conversation", "write"):
		frappe.throw("Not permitted", frappe.PermissionError)
	if doctype not in ("Customer", "Contact", "Lead"):
		frappe.throw("Invalid doctype")
	if not frappe.db.exists(doctype, docname):
		frappe.throw(f"{doctype} {docname} not found")

	conv = frappe.get_doc("WA Conversation", conversation)
	updates = {doctype.lower(): docname}
	if doctype == "Contact":
		customer = customer_for_contact(docname)
		if customer and not conv.customer:
			updates["customer"] = customer
	conv.db_set(updates, update_modified=False)
	return updates


@frappe.whitelist()
def unlink_conversation(conversation):
	if not frappe.has_permission("WA Conversation", "write"):
		frappe.throw("Not permitted", frappe.PermissionError)
	frappe.db.set_value(
		"WA Conversation",
		conversation,
		{"customer": None, "contact": None, "lead": None},
		update_modified=False,
	)
	return True


@frappe.whitelist()
def create_lead(conversation, lead_name=None):
	"""Create a Lead prefilled from the conversation and link it."""
	if not frappe.has_permission("WA Conversation", "write"):
		frappe.throw("Not permitted", frappe.PermissionError)
	if not frappe.has_permission("Lead", "create"):
		frappe.throw("You need Lead create permission", frappe.PermissionError)

	conv = frappe.get_doc("WA Conversation", conversation)
	if conv.lead:
		frappe.throw(f"Already linked to Lead {conv.lead}")

	real = resolve_real_number(conv)
	norm = normalize_number(real) or _usable(real) or conv.phone_number

	lead = frappe.get_doc(
		{
			"doctype": "Lead",
			"lead_name": (lead_name or conv.display_name or norm).strip(),
			"mobile_no": norm,
		}
	)
	lead.insert()
	conv.db_set({"lead": lead.name}, update_modified=False)
	return lead.name
