import frappe
import os
import json


def run():
	site_path = "/home/erpnext/frappe-bench/sites/matthewsandclarke.ddns.net"

	db_files = frappe.get_all(
		"File",
		filters={"is_private": 1, "is_folder": 0},
		fields=["name", "file_url", "modified", "attached_to_doctype", "attached_to_name", "file_name"],
	)
	missing = []
	for f in db_files:
		if not f.file_url or not f.file_url.startswith("/private/files/"):
			continue
		rel_path = f.file_url[len("/private/"):]
		full_path = os.path.join(site_path, "private", rel_path)
		if not os.path.exists(full_path):
			missing.append(f)
	print(f"Total currently missing: {len(missing)}")

	# Newest first - a more recent backup is always preferred when a file
	# exists in multiple, since it's the freshest available copy.
	backup_lists = [
		("aug30", "/tmp/aug30_backup_file_list.txt"),
		("aug29", "/tmp/aug29_backup_file_list.txt"),
		("aug20", "/tmp/aug20_backup_file_list.txt"),
		("aug9", "/tmp/aug9_backup_file_list.txt"),
	]
	backup_sets = {}
	for name, path in backup_lists:
		if os.path.exists(path):
			with open(path) as fh:
				backup_sets[name] = set(line.strip() for line in fh)
			print(f"Loaded {name}: {len(backup_sets[name])} files in that backup")
		else:
			backup_sets[name] = set()
			print(f"WARNING: {path} not found, skipping {name}")

	recovery_plan = {name: [] for name, _ in backup_lists}
	still_missing = []
	for m in missing:
		basename = m["file_url"].replace("/private/files/", "", 1)
		found = False
		for name, _ in backup_lists:
			if basename in backup_sets[name]:
				recovery_plan[name].append(basename)
				found = True
				break
		if not found:
			still_missing.append(m)

	print()
	total_recoverable = 0
	for name, _ in backup_lists:
		count = len(recovery_plan[name])
		total_recoverable += count
		print(f"Recoverable from {name}: {count}")
	print(f"Total recoverable across all backups: {total_recoverable}")
	print(f"Still missing after all backups checked: {len(still_missing)}")

	out_path = "/home/erpnext/recovery_plan.json"
	with open(out_path, "w") as fh:
		json.dump(
			{
				"recovery_plan": recovery_plan,
				"still_missing": [dict(m) for m in still_missing],
			},
			fh,
			default=str,
			indent=2,
		)
	print(f"\nSaved recovery plan to: {out_path}")
