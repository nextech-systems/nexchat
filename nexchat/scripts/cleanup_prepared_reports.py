import frappe
import json


def run():
	with open(
		"/home/erpnext/frappe-bench/sites/matthewsandclarke.ddns.net/private/backups/missing_private_files.json"
	) as f:
		missing = json.load(f)

	reports = [m for m in missing if m.get("attached_to_doctype") == "Prepared Report"]
	print(f"Found {len(reports)} broken Prepared Report cache entries")

	deleted_files = 0
	deleted_reports = 0
	errors = []

	for r in reports:
		try:
			# Delete the stale File record pointing at the missing cache file
			if frappe.db.exists("File", r["name"]):
				frappe.delete_doc("File", r["name"], ignore_permissions=True, force=True)
				deleted_files += 1

			# Also delete the Prepared Report record itself - it's just a
			# cache entry, safe to remove entirely so the report re-runs
			# clean next time instead of trying to reference a dead file.
			if frappe.db.exists("Prepared Report", r["attached_to_name"]):
				frappe.delete_doc(
					"Prepared Report", r["attached_to_name"], ignore_permissions=True, force=True
				)
				deleted_reports += 1
		except Exception as e:
			errors.append((r["name"], str(e)))

	frappe.db.commit()

	print(f"Deleted File records: {deleted_files}")
	print(f"Deleted Prepared Report records: {deleted_reports}")
	print(f"Errors: {len(errors)}")
	for name, err in errors[:10]:
		print(" -", name, "|", err)
