"""Private ntfy notifications for explicit NexChat assignments."""

import re
from urllib.parse import urlsplit

import frappe
import requests
from frappe.utils import get_datetime, get_url_to_form


def _destination(agent):
    settings = frappe.conf.get("nexchat_ntfy") or {}
    topic = (settings.get("topics") or {}).get(agent) or ""
    token = settings.get("token") or ""
    url = (settings.get("url") or "").rstrip("/")
    target = urlsplit(url)
    if (
        target.scheme != "https"
        or not target.hostname
        or target.username
        or target.password
        or target.query
        or target.fragment
        or not token
        or not re.fullmatch(r"[-_A-Za-z0-9]{1,64}", topic)
    ):
        return None
    return f"{url}/{topic}", token


def queue_assignment_alert(doctype, conversation, agent, claimed_at, after_commit=False):
    """Queue the delivery only for configured agents."""
    try:
        if not _destination(agent):
            return
        frappe.enqueue(
            send_assignment_alert,
            queue="short",
            enqueue_after_commit=after_commit,
            doctype=doctype,
            conversation=conversation,
            agent=agent,
            claimed_at=str(claimed_at),
        )
    except Exception:
        frappe.logger("nexchat").exception("Could not queue NexChat assignment alert")


def send_assignment_alert(doctype, conversation, agent, claimed_at):
    destination = _destination(agent)
    if not destination:
        return
    assignment = frappe.db.get_value(
        doctype, conversation, ["assigned_agent", "claimed_at"], as_dict=True
    )
    if (
        not assignment
        or assignment.assigned_agent != agent
        or not assignment.claimed_at
        or get_datetime(assignment.claimed_at) != get_datetime(claimed_at)
    ):
        return

    url, token = destination
    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Title": "NexChat assignment",
            "Click": get_url_to_form(doctype, conversation),
        },
        data="A chat has been assigned to you.",
        timeout=(3, 5),
        allow_redirects=False,
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"NexChat ntfy returned HTTP {response.status_code}")
