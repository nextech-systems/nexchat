import frappe
import json
import os
from datetime import datetime

DOCTYPES_TO_WIPE = ["WA Chat Message", "WA Event Log", "WA Conversation"]
BATCH_SIZE = 200

HEAVY_FIELDTYPES = {"Long Text", "Code", "Text Editor", "JSON", "HTML Editor"}

_BACKUP_PATH_MARKER = "nexchat_wipe_backup_path.txt"


def _safe_fields(doctype, skip_heavy):
	meta = frappe.get_meta(doctype)
	# frappe.model.no_value_fields is the framework's own list of field
	# types that are layout-only (Column Break, Section Break, etc.) and
	# have no backing database column - selecting them in SQL fails.
	from frappe.model import no_value_fields

	fields = ["name", "creation", "modified"]
	for df in meta.fields:
		if df.fieldtype in no_value_fields:
			continue
		if skip_heavy and df.fieldtype in HEAVY_FIELDTYPES:
			continue
		if df.fieldname not in fields:
			fields.append(df.fieldname)
	return fields


def _backup_doctype(doctype, backup_dir, skip_heavy=False):
	fields = _safe_fields(doctype, skip_heavy)
	out_path = os.path.join(backup_dir, f"{doctype.replace(' ', '_')}.jsonl")
	total = frappe.db.count(doctype)
	written = 0

	with open(out_path, "w") as f:
		start = 0
		while True:
			batch = frappe.get_all(
				doctype, fields=fields, limit_start=start, limit_page_length=BATCH_SIZE, order_by="creation asc"
			)
			if not batch:
				break
			for row in batch:
				f.write(json.dumps(row, default=str))
				f.write("\n")
			written += len(batch)
			start += BATCH_SIZE
			if written % 1000 == 0 or written == total:
				print(f"    ...{written}/{total}")

	note = " (payload/long-text fields excluded)" if skip_heavy else ""
	print(f"  {doctype}: {written} rows -> {out_path}{note}")
	return written


def backup():
	backup_dir = os.path.join(
		frappe.utils.get_site_path(), "private", "backups",
		f"nexchat_wipe_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
	)
	os.makedirs(backup_dir, exist_ok=True)
	print(f"Backing up to: {backup_dir}")

	counts = {}
	counts["WA Chat Message"] = _backup_doctype("WA Chat Message", backup_dir, skip_heavy=False)
	counts["WA Event Log"] = _backup_doctype("WA Event Log", backup_dir, skip_heavy=True)
	counts["WA Conversation"] = _backup_doctype("WA Conversation", backup_dir, skip_heavy=False)

	cq_fields = _safe_fields("Conversation Queue", skip_heavy=False)
	cq_rows = frappe.get_all("Conversation Queue", filters={"channel": "WhatsApp"}, fields=cq_fields)
	counts["Conversation Queue (WhatsApp)"] = len(cq_rows)
	with open(os.path.join(backup_dir, "Conversation_Queue_WhatsApp.jsonl"), "w") as f:
		for row in cq_rows:
			f.write(json.dumps(row, default=str))
			f.write("\n")
	print(f"  Conversation Queue (WhatsApp): {len(cq_rows)} rows")

	marker_path = os.path.join(frappe.utils.get_site_path(), "private", "backups", _BACKUP_PATH_MARKER)
	with open(marker_path, "w") as f:
		f.write(backup_dir)

	print()
	print(f"Backup complete. Total records: {sum(counts.values())}")
	print("Note: WA Event Log backup excludes raw payload/long-text fields to stay memory-safe;")
	print("the actual message content is fully preserved in WA Chat Message.")
	print("Nothing deleted. Review the backup, then run wipe() when ready.")
	return backup_dir


def wipe():
	marker_path = os.path.join(frappe.utils.get_site_path(), "private", "backups", _BACKUP_PATH_MARKER)
	if not os.path.exists(marker_path):
		print("No backup found (backup() has not been run yet). Refusing to wipe without a backup.")
		print("Run: bench --site <site> execute nexchat.scripts.wipe_whatsapp_data.backup")
		return

	with open(marker_path) as f:
		backup_dir = f.read().strip()
	print(f"Using backup at: {backup_dir}")

	frappe.db.delete("WA Chat Message")
	frappe.db.delete("WA Event Log")
	frappe.db.delete("Conversation Queue", {"channel": "WhatsApp"})
	frappe.db.delete("WA Conversation")
	frappe.db.commit()

	print("Deleted.")
	for doctype in DOCTYPES_TO_WIPE:
		remaining = frappe.db.count(doctype)
		print(f"  {doctype}: {remaining} remaining (should be 0)")
	remaining_cq = frappe.db.count("Conversation Queue", {"channel": "WhatsApp"})
	print(f"  Conversation Queue (WhatsApp): {remaining_cq} remaining (should be 0)")
	print()
	print(f"Backup remains on disk at: {backup_dir}")
