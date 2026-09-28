import frappe
import os


def run():
	site_path = frappe.utils.get_site_path()

	files = frappe.get_all(
		"File",
		filters={"is_private": 1, "is_folder": 0},
		fields=["name", "file_name", "file_url", "attached_to_doctype", "attached_to_name"],
	)

	print(f"Checking {len(files)} private files for missing physical copies...")

	missing = []
	checked = 0
	for f in files:
		if not f.file_url or not f.file_url.startswith("/private/files/"):
			continue
		rel_path = f.file_url[len("/private/") :]  # -> files/<name>
		full_path = os.path.join(site_path, "private", rel_path)
		checked += 1
		if not os.path.exists(full_path):
			missing.append(f)
		if checked % 2000 == 0:
			print(f"  ...checked {checked}/{len(files)}, {len(missing)} missing so far")

	print(f"\nDone. Checked {checked} private files.")
	print(f"MISSING: {len(missing)}")
	print()
	for f in missing[:50]:
		print(f"  {f.name} | {f.file_name} | attached to {f.attached_to_doctype or '(none)'}: {f.attached_to_name or ''}")

	if len(missing) > 50:
		print(f"  ...and {len(missing) - 50} more")

	# Save the full list for the recovery step, so we don't need to re-scan
	import json
	out_path = os.path.join(site_path, "private", "backups", "missing_private_files.json")
	with open(out_path, "w") as fh:
		json.dump([dict(f) for f in missing], fh, default=str, indent=2)
	print(f"\nFull list saved to: {out_path}")
