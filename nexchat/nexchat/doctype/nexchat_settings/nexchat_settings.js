// Copyright (c) 2026, Nextech Systems and contributors
// For license information, please see license.txt

frappe.ui.form.on("NexChat Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test Connection"), () => {
			frappe.call({
				method: "nexchat.api.test_connection",
				freeze: true,
				freeze_message: __("Contacting gateway..."),
				callback: (r) => {
					if (r.message) {
						frappe.msgprint({
							title: __("Gateway Connected"),
							indicator: r.message.status === "ready" ? "green" : "orange",
							message: `Session: ${frappe.utils.escape_html(r.message.name || "")}<br>Status: ${frappe.utils.escape_html(r.message.status || "")}<br>Phone: ${frappe.utils.escape_html(r.message.phone || "")}`,
						});
					}
				},
			});
		});
	},
});
