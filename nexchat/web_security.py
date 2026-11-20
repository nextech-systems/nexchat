"""NexChat hardening helpers. Visitor credentials are signed, expiring tokens.
No credentials belong in realtime events or in Conversation Queue identifiers.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlsplit

import frappe

SESSION_SECONDS = 7 * 24 * 3600
CHANNELS = ("WA Conversation", "Web Conversation", "Email Conversation")


def _deny(message="Chat session expired. Please start a new chat."):
    frappe.throw(message, frappe.PermissionError)


def _key():
    key = frappe.conf.get("nexchat_web_signing_key") or frappe.conf.get("encryption_key")
    if not isinstance(key, str) or not key:
        _deny("Chat is temporarily unavailable.")
    return hmac.new(key.encode(), b"nexchat-visitor-session-v2", hashlib.sha256).digest()


def mint_token(conversation):
    data = json.dumps({"v": 2, "c": conversation, "iat": int(time.time()),
                       "nonce": secrets.token_hex(16)}, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(data).rstrip(b"=").decode()
    sig = hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()
    return body + "." + sig


def inspect_token(token):
    """Validate a bearer credential; raise ValueError without response-side messages.
    Used when checking a bounded history list where expired entries are omitted.
    """
    if not isinstance(token, str) or not token or len(token) > 1024:
        raise ValueError("Invalid chat credential")
    try:
        body, sig = token.split(".")
        expected = hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise ValueError()
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        age = time.time() - data["iat"]
        if data.get("v") != 2 or age < -60 or age >= SESSION_SECONDS:
            raise ValueError()
        name = data["c"]
        if not isinstance(name, str) or not name or len(name) > 140:
            raise ValueError()
        return name
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
        raise ValueError("Invalid chat credential") from None


def token_conversation(token):
    try:
        return inspect_token(token)
    except ValueError:
        _deny()


def throttle(bucket, identity, limit, seconds=60):
    # Atomic Redis increment + expiration. Redis keys contain no IP or token text.
    digest = hashlib.sha256(str(identity).encode()).hexdigest()
    cache = frappe.cache()
    key = cache.make_key("nxc-v2:" + bucket + ":" + digest)
    count = cache.eval("""
        local n = redis.call('INCR', KEYS[1])
        if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
        return n
    """, 1, key, int(seconds))
    if int(count) > limit:
        frappe.throw("Too many chat requests. Please wait and try again.", frappe.ValidationError)


def visitor_request(action, token=None):
    ip = getattr(frappe.local, "request_ip", None) or "unknown"
    limits = {"start": (10, 3600), "identify": (30, 60), "resume": (120, 60),
              "send": (100, 60), "poll": (1200, 60),
              "end": (20, 60), "history": (30, 60), "handover": (20, 60)}
    limit, window = limits[action]
    throttle("ip-" + action, ip, limit, window)
    if action == "start":
        throttle("site-start", "site", 500, 3600)
    if action not in ("start", "history") and (not isinstance(token, str) or not token or len(token) > 1024):
        _deny()
    if token:
        throttle("token-" + action, token, 40 if action == "send" else 120)
    if action == "send":
        throttle("site-send", "site", 300)


def text(value, label, maximum, required=True):
    if value is None:
        value = ""
    if not isinstance(value, str):
        frappe.throw(label + " must be text")
    value = value.strip()
    if (required and not value) or len(value) > maximum or any(ord(c) < 32 for c in value):
        frappe.throw("Please enter a valid " + label.lower())
    return value


def details(name, email, phone):
    import re
    name = text(name, "Name", 140)
    email = text(email, "Email", 140)
    phone = text(phone, "Phone", 40)
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        frappe.throw("Please enter a valid email")
    digits = re.sub(r"\D", "", phone)
    if not 7 <= len(digits) <= 15 or not re.fullmatch(r"[+0-9().\-\s]+", phone):
        frappe.throw("Please enter a valid phone number")
    return name, email, phone


def page_url(value):
    # Store only the page origin/path; never query strings or fragments containing tokens.
    value = text(value, "Page URL", 2048, required=False)
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password:
            return ""
        return (parts.scheme + "://" + parts.netloc + parts.path)[:255]
    except ValueError:
        return ""


def staff_doc(doctype, name, permission="read", user=None):
    if doctype not in CHANNELS:
        frappe.throw("Unknown conversation channel")
    user = user or frappe.session.user
    if user == "Guest":
        _deny("Not permitted")
    doc = frappe.get_doc(doctype, name)
    if not frappe.has_permission(doctype, permission, doc=doc, user=user):
        _deny("Not permitted")
    return doc


def publish(event, message, after_commit=True):
    """Send sensitive events only to users allowed to read this conversation.
    Existing NexChat Center listeners use user rooms without any JS changes.
    """
    name = message.get("conversation")
    doctype = message.get("source_doctype")
    if doctype not in CHANNELS or not name:
        return  # Never fall back to a broadcast room.
    doc = frappe.get_doc(doctype, name)
    for user in frappe.get_all("User", filters={"enabled": 1, "user_type": "System User"}, pluck="name"):
        if user != "Guest" and frappe.has_permission(doctype, "read", doc=doc, user=user):
            frappe.publish_realtime(event, message, user=user, after_commit=after_commit)


def filter_rows(rows, user):
    """Queue read permission alone must not disclose every source document."""
    result = []
    for row in rows:
        doctype = row.get("source_doctype") or {
            "WhatsApp": "WA Conversation", "Website": "Web Conversation",
            "Email": "Email Conversation"}.get(row.get("channel"))
        name = row.get("source_name")
        if doctype not in CHANNELS or not name or not frappe.db.exists(doctype, name):
            continue
        doc = frappe.get_doc(doctype, name)
        if user != "Guest" and frappe.has_permission(doctype, "read", doc=doc, user=user):
            result.append(row)
    return result


def audit_configuration():
    """Read-only bench check. Deliberately not whitelisted and prints no secrets."""
    problems = []
    if not (frappe.conf.get("nexchat_web_signing_key") or frappe.conf.get("encryption_key")):
        problems.append("No session signing key is configured; visitor chat will fail closed")
    protected = ("Web Conversation", "Web Chat Message", "WA Conversation",
                 "WA Chat Message", "Email Conversation", "Conversation Queue",
                 "NexChat Settings", "NexChat Session")
    for doctype in protected:
        if not frappe.db.exists("DocType", doctype):
            problems.append("Missing DocType: " + doctype)
            continue
        for perm in frappe.get_meta(doctype).permissions:
            if perm.role == "Guest" and any(perm.get(k) for k in
                    ("read", "write", "create", "delete", "submit", "cancel", "export", "report", "share", "print", "select")):
                problems.append("Guest permissions must be removed from " + doctype)
    if frappe.conf.get("ignore_csrf"):
        problems.append("Global ignore_csrf is enabled; investigate before public deployment")
    if frappe.conf.get("developer_mode"):
        problems.append("developer_mode is enabled; disable on production")
    cors = frappe.conf.get("allow_cors", [])
    if cors == "*" or (isinstance(cors, (list, tuple)) and "*" in cors):
        problems.append("CORS allows every origin; restrict to the website origins")
    return {"scope": "configuration checks only; not a full penetration test",
            "session_lifetime_days": SESSION_SECONDS // 86400,
            "signing_key_available": bool(frappe.conf.get("nexchat_web_signing_key") or frappe.conf.get("encryption_key")),
            "issues": problems}
