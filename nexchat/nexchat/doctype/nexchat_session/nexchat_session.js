// Copyright (c) 2026, Nextech Systems and contributors
// For license information, please see license.txt

frappe.ui.form.on("NexChat Session", {
	refresh(frm) {
		frm.add_custom_button(__("Add agents"), () => {
			new frappe.ui.form.MultiSelectDialog({
				doctype: "User",
				target: frm,
				setters: { full_name: null },
				add_filters_group: 0,
				get_query() {
					return { filters: { enabled: 1, user_type: "System User" } };
				},
				action(selections) {
					(selections || []).forEach((u) => {
						if (!frm.doc.agents.some((r) => r.user === u)) {
							const row = frm.add_child("agents");
							row.user = u;
						}
					});
					frm.refresh_field("agents");
					frappe.show_alert(__("Added — save to apply queue access"));
				},
			});
		});
	},
});
