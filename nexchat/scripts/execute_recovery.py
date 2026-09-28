import frappe
import os
import shutil
import json


def run():
	site_path = "/home/erpnext/frappe-bench/sites/matthewsandclarke.ddns.net"
	live_dir = os.path.join(site_path, "private", "files")

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

	# Newest first - prefer the freshest available copy of each file
	backup_lists = [
		("aug30", "/tmp/aug30_backup_file_list.txt", None),
		("aug29", "/tmp/aug29_backup_file_list.txt", None),
		("aug20", "/tmp/aug20_backup_file_list.txt", "/home/erpnext/recovery_staging_aug20/matthewsandclarke.ddns.net/private/files"),
		("aug9", "/tmp/aug9_backup_file_list.txt", "/home/erpnext/recovery_staging/matthewsandclarke.ddns.net/private/files"),
	]

	backup_sets = {}
	for name, path, _ in backup_lists:
		if os.path.exists(path):
			with open(path) as fh:
				backup_sets[name] = set(line.strip() for line in fh)
		else:
			backup_sets[name] = set()

	recovery_plan = {name: [] for name, _, _ in backup_lists}
	still_missing = []
	for m in missing:
		basename = m["file_url"].replace("/private/files/", "", 1)
		found = False
		for name, _, _ in backup_lists:
			if basename in backup_sets[name]:
				recovery_plan[name].append(basename)
				found = True
				break
		if not found:
			still_missing.append(m)

	print()
	for name, _, _ in backup_lists:
		print(f"Recoverable from {name}: {len(recovery_plan[name])}")
	print(f"Still missing (need older backups not yet checked): {len(still_missing)}")

	# Only actually recover from backups we have a LOCAL extraction for
	# (aug20, aug9). aug30/aug29 matched 0 files last time - no need to
	# extract 20GB+ tars for zero benefit.
	total_copied = 0
	total_failed = 0
	for name, _, staging_dir in backup_lists:
		if not staging_dir or not recovery_plan[name]:
			continue
		print(f"\nRecovering {len(recovery_plan[name])} files from {name}...")
		copied = 0
		failed = []
		for basename in recovery_plan[name]:
			src = os.path.join(staging_dir, basename)
			dst = os.path.join(live_dir, basename)
			try:
				shutil.copy2(src, dst)
				copied += 1
			except Exception as e:
				failed.append((basename, str(e)))
		print(f"  Copied: {copied}, Failed: {len(failed)}")
		for bname, err in failed[:5]:
			print("   -", bname, "|", err)
		total_copied += copied
		total_failed += len(failed)

	print(f"\n=== TOTAL: {total_copied} files copied, {total_failed} failed ===")
	print("Fixing ownership/permissions on the whole directory...")
	os.system(f'find "{live_dir}" -type f -exec chown erpnext:erpnext {{}} +')
	os.system(f'find "{live_dir}" -type f -exec chmod 644 {{}} +')

	final_count = len([f for f in os.listdir(live_dir) if os.path.isfile(os.path.join(live_dir, f))])
	print(f"Live directory file count now: {final_count}")

	with open("/home/erpnext/recovery_plan_final.json", "w") as fh:
		json.dump(
			{"still_missing": [dict(m) for m in still_missing]},
			fh,
			default=str,
			indent=2,
		)
	print("Saved still-missing list to: /home/erpnext/recovery_plan_final.json")
