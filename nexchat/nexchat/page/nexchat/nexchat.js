// Copyright (c) 2026, Nextech Systems and contributors
// For license information, please see license.txt

frappe.pages["nexchat"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("NexChat"),
		single_column: true,
	});
	new NexChatCenter(page);
};

class NexChatCenter {
	constructor(page) {
		this.page = page;
		this.conversations = [];
		this.active = null;
		this.sending = false;
		this.make();
		this.bind_realtime();
		this.load_conversations();
		this.refresh_timer = setInterval(() => this.load_conversations(true), 60000);
	}

	make() {
		this.page.main.html(`
			<style>
				.nx-wrap { display: flex; height: calc(100vh - 140px); border: 1px solid var(--border-color); border-radius: 8px; overflow: hidden; background: var(--card-bg); }
				.nx-list { width: 300px; min-width: 240px; border-right: 1px solid var(--border-color); display: flex; flex-direction: column; }
				.nx-search { padding: 8px; border-bottom: 1px solid var(--border-color); }
				.nx-search input { width: 100%; }
				.nx-convs { overflow-y: auto; flex: 1; }
				.nx-conv { padding: 10px 12px; border-bottom: 1px solid var(--border-color); cursor: pointer; }
				.nx-conv:hover { background: var(--bg-color); }
				.nx-conv.active { background: var(--bg-color); border-left: 3px solid var(--primary); padding-left: 9px; }
				.nx-conv-top { display: flex; justify-content: space-between; align-items: center; gap: 6px; }
				.nx-conv-name { font-weight: 600; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
				.nx-badge { background: var(--primary); color: #fff; font-size: 11px; border-radius: 10px; padding: 0 7px; min-width: 18px; text-align: center; }
				.nx-chip { background: var(--bg-color); border: 1px solid var(--border-color); font-size: 10px; border-radius: 8px; padding: 0 6px; }
				.nx-conv-sub { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
				.nx-conv-preview { font-size: 12px; color: var(--text-muted); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
				.nx-thread { flex: 1; display: flex; flex-direction: column; min-width: 0; }
				.nx-head { padding: 10px 14px; border-bottom: 1px solid var(--border-color); display: flex; align-items: center; gap: 10px; }
				.nx-head-name { font-weight: 600; }
				.nx-head-sub { font-size: 12px; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }\n\t\t\t\t.nx-head-actions { display: flex; gap: 6px; flex-shrink: 0; }
				.nx-msgs { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 8px; background: var(--bg-color); }
				.nx-bubble { max-width: 72%; padding: 8px 12px; border-radius: 10px; font-size: 13px; white-space: pre-wrap; word-break: break-word; }
				.nx-in { align-self: flex-start; background: var(--card-bg); border: 1px solid var(--border-color); }
				.nx-out { align-self: flex-end; background: var(--blue-100, #d8e6fd); }
				.nx-meta { font-size: 11px; color: var(--text-muted); margin-top: 3px; }
				.nx-compose { display: flex; gap: 8px; padding: 10px; border-top: 1px solid var(--border-color); }
				.nx-compose textarea { flex: 1; resize: none; height: 60px; }
				.nx-empty { display: flex; align-items: center; justify-content: center; flex: 1; color: var(--text-muted); }
			</style>
			<div class="nx-wrap">
				<div class="nx-list">
					<div class="nx-search">
						<input type="text" class="form-control" placeholder="${__("Search name or number")}">
					</div>
					<div class="nx-convs"></div>
				</div>
				<div class="nx-thread">
					<div class="nx-empty">${__("Select a conversation")}</div>
				</div>
			</div>
		`);
		this.$convs = this.page.main.find(".nx-convs");
		this.$thread = this.page.main.find(".nx-thread");
		let t = null;
		this.page.main.find(".nx-search input").on("input", (e) => {
			clearTimeout(t);
			t = setTimeout(() => this.load_conversations(false, e.target.value), 300);
		});
	}

	bind_realtime() {
		frappe.realtime.on("nexchat_message", (data) => {
			this.load_conversations(true);
			if (this.active && data.conversation === this.active.name) {
				this.load_messages(this.active.name);
				if (data.direction === "Incoming") this.mark_read(this.active.name);
			}
		});
		frappe.realtime.on("nexchat_ack", () => {
			if (this.active) this.load_messages(this.active.name);
		});
	}

	load_conversations(silent, search) {
		frappe.call({
			method: "nexchat.api.get_conversations",
			args: { search: search || null },
			callback: (r) => {
				this.conversations = r.message || [];
				this.render_list();
			},
			freeze: false,
		});
	}

	render_list() {
		const esc = frappe.utils.escape_html;
		this.$convs.empty();
		if (!this.conversations.length) {
			this.$convs.html(`<div class="nx-conv">${__("No conversations yet")}</div>`);
			return;
		}
		this.conversations.forEach((c) => {
			const name = esc(c.display_name || c.name);
			const sub = c.customer
				? esc(c.customer)
				: c.lead
				? __("Lead: {0}", [esc(c.lead)])
				: __("Unlinked");
			const badge = c.unread_count
				? `<span class="nx-badge">${c.unread_count}</span>`
				: "";
			const labels = new Set(this.conversations.map((x) => x.session_label).filter(Boolean));
			const chip =
				labels.size > 1 && c.session_label
					? ` <span class="nx-chip">${esc(c.session_label)}</span>`
					: "";
			const $row = $(`
				<div class="nx-conv ${this.active && this.active.name === c.name ? "active" : ""}" data-name="${esc(c.name)}">
					<div class="nx-conv-top">
						<div class="nx-conv-name">${name}</div>
						${badge}
					</div>
					<div class="nx-conv-sub">${sub}${chip}</div>
					<div class="nx-conv-preview">${esc(c.last_message_preview || "")}</div>
				</div>
			`);
			$row.on("click", () => this.open_conversation(c));
			this.$convs.append($row);
		});
	}

	open_conversation(c) {
		this.active = c;
		this.render_list();
		const esc = frappe.utils.escape_html;
		const sub_bits = [c.name];
		if (c.resolved_number && c.resolved_number !== c.name) sub_bits.push(c.resolved_number);
		if (c.session_label) sub_bits.push(c.session_label);
		const linked = c.customer || c.contact || c.lead;
		let actions = "";
		if (c.customer) actions += `<button class="btn btn-xs btn-default nx-open" data-dt="Customer" data-dn="${esc(c.customer)}">${esc(c.customer)}</button>`;
		else if (c.lead) actions += `<button class="btn btn-xs btn-default nx-open" data-dt="Lead" data-dn="${esc(c.lead)}">${__("Lead")}: ${esc(c.lead)}</button>`;
		else if (c.contact) actions += `<button class="btn btn-xs btn-default nx-open" data-dt="Contact" data-dn="${esc(c.contact)}">${esc(c.contact)}</button>`;
		if (linked) {
			actions += ` <button class="btn btn-xs btn-default nx-unlink" title="${__("Unlink")}">&times;</button>`;
		} else {
			actions += `<button class="btn btn-xs btn-default nx-link">${__("Link")}</button> <button class="btn btn-xs btn-default nx-new-lead">${__("Create Lead")}</button>`;
		}
		this.$thread.html(`
			<div class="nx-head">
				<div style="flex:1; min-width:0">
					<div class="nx-head-name">${esc(c.display_name || c.name)}</div>
					<div class="nx-head-sub">${esc(sub_bits.join(" · "))}</div>
				</div>
				<div class="nx-head-actions">${actions}</div>
			</div>
			<div class="nx-msgs"></div>
			<div class="nx-compose">
				<textarea class="form-control" placeholder="${__("Type a message")}" maxlength="4096"></textarea>
				<button class="btn btn-primary btn-sm nx-send">${__("Send")}</button>
			</div>
		`);
		this.$thread.find(".nx-open").on("click", (e) => {
			const el = e.currentTarget;
			frappe.set_route("Form", el.dataset.dt, el.dataset.dn);
		});
		this.$thread.find(".nx-link").on("click", () => this.link_dialog(c));
		this.$thread.find(".nx-new-lead").on("click", () => this.create_lead(c));
		this.$thread.find(".nx-unlink").on("click", () => this.unlink(c));
		this.$msgs = this.$thread.find(".nx-msgs");
		this.$input = this.$thread.find("textarea");
		this.$thread.find(".nx-send").on("click", () => this.send());
		this.$input.on("keydown", (e) => {
			if (e.key === "Enter" && !e.shiftKey) {
				e.preventDefault();
				this.send();
			}
		});
		this.load_messages(c.name);
		if (c.unread_count) this.mark_read(c.name);
	}

	load_messages(conversation) {
		frappe.call({
			method: "nexchat.api.get_messages",
			args: { conversation: conversation, limit: 50 },
			callback: (r) => {
				if (!this.active || this.active.name !== conversation) return;
				this.render_messages(r.message || []);
			},
		});
	}

	render_messages(msgs) {
		const esc = frappe.utils.escape_html;
		this.$msgs.empty();
		msgs.forEach((m) => {
			const dir = m.direction === "Outgoing" ? "nx-out" : "nx-in";
			let body = esc(m.message || "");
			if (!body && m.content_type !== "text") body = `[${esc(m.content_type)}]`;
			if (m.media_file) {
				body += ` <a href="${encodeURI(m.media_file)}" target="_blank">${__("attachment")}</a>`;
			}
			const when = frappe.datetime.str_to_user(m.timestamp);
			const ack =
				m.direction === "Outgoing" && m.ack_status
					? ` · ${esc(m.ack_status)}`
					: "";
			const by =
				m.direction === "Outgoing" && m.sent_by
					? ` · ${esc(m.sent_by)}`
					: "";
			this.$msgs.append(`
				<div class="nx-bubble ${dir}">
					<div>${body}</div>
					<div class="nx-meta">${esc(when)}${ack}${by}</div>
				</div>
			`);
		});
		this.$msgs.scrollTop(this.$msgs[0].scrollHeight);
	}

	send() {
		if (this.sending || !this.active) return;
		const text = (this.$input.val() || "").trim();
		if (!text) return;
		this.sending = true;
		const $btn = this.$thread.find(".nx-send").prop("disabled", true);
		frappe.call({
			method: "nexchat.api.send_message",
			args: { conversation: this.active.name, message: text },
			callback: () => {
				this.$input.val("");
				this.load_messages(this.active.name);
				this.load_conversations(true);
			},
			always: () => {
				this.sending = false;
				$btn.prop("disabled", false);
				this.$input.focus();
			},
		});
	}

	mark_read(conversation) {
		frappe.call({
			method: "nexchat.api.mark_read",
			args: { conversation: conversation },
			callback: () => this.load_conversations(true),
		});
	}

	reopen_active() {
		// refetch list, then reopen the active conversation with fresh links
		const name = this.active && this.active.name;
		if (!name) return;
		frappe.call({
			method: "nexchat.api.get_conversations",
			callback: (r) => {
				this.conversations = r.message || [];
				const fresh = this.conversations.find((x) => x.name === name);
				this.render_list();
				if (fresh) this.open_conversation(fresh);
			},
		});
	}

	link_dialog(c) {
		const esc = frappe.utils.escape_html;
		const d = new frappe.ui.Dialog({
			title: __("Link conversation"),
			fields: [
				{ fieldname: "suggestions", fieldtype: "HTML" },
				{
					fieldname: "doctype",
					fieldtype: "Select",
					label: __("Link to"),
					options: "Customer\nContact\nLead",
					default: "Customer",
				},
				{
					fieldname: "docname",
					fieldtype: "Dynamic Link",
					label: __("Record"),
					options: "doctype",
					reqd: 1,
				},
			],
			primary_action_label: __("Link"),
			primary_action: (values) => {
				frappe.call({
					method: "nexchat.crm.link_conversation",
					args: {
						conversation: c.name,
						doctype: values.doctype,
						docname: values.docname,
					},
					callback: () => {
						d.hide();
						this.reopen_active();
					},
				});
			},
		});
		d.show();
		frappe.call({
			method: "nexchat.crm.get_link_options",
			args: { conversation: c.name },
			callback: (r) => {
				const m = r.message || {};
				let html = "";
				const row = (dt, dn, label) =>
					`<div class="nx-suggest" data-dt="${esc(dt)}" data-dn="${esc(dn)}" style="padding:6px 8px;border:1px solid var(--border-color);border-radius:6px;margin-bottom:6px;cursor:pointer">
						<b>${esc(dt)}</b>: ${esc(label)}</div>`;
				(m.contacts || []).forEach((x) => {
					const nm = [x.first_name, x.last_name].filter(Boolean).join(" ") || x.name;
					html += row("Contact", x.name, `${nm} (${x.mobile_no || x.phone || ""})`);
				});
				(m.customers || []).forEach((x) => {
					html += row("Customer", x.name, x.customer_name || x.name);
				});
				(m.leads || []).forEach((x) => {
					html += row("Lead", x.name, x.lead_name || x.company_name || x.name);
				});
				if (!html) html = `<div class="text-muted">${__("No matches on this number — pick manually below or create a Lead.")}</div>`;
				else html = `<div class="text-muted" style="margin-bottom:6px">${__("Matches on this number:")}</div>` + html;
				const $w = d.get_field("suggestions").$wrapper;
				$w.html(html);
				$w.find(".nx-suggest").on("click", (e) => {
					const el = e.currentTarget;
					d.set_value("doctype", el.dataset.dt);
					d.set_value("docname", el.dataset.dn);
				});
			},
		});
	}

	create_lead(c) {
		frappe.prompt(
			{
				fieldname: "lead_name",
				fieldtype: "Data",
				label: __("Lead Name"),
				default: c.display_name || "",
				reqd: 1,
			},
			(values) => {
				frappe.call({
					method: "nexchat.crm.create_lead",
					args: { conversation: c.name, lead_name: values.lead_name },
					callback: (r) => {
						frappe.show_alert({
							message: __("Lead {0} created", [r.message]),
							indicator: "green",
						});
						this.reopen_active();
					},
				});
			},
			__("Create Lead from conversation")
		);
	}

	unlink(c) {
		frappe.confirm(__("Unlink this conversation from its CRM records?"), () => {
			frappe.call({
				method: "nexchat.crm.unlink_conversation",
				args: { conversation: c.name },
				callback: () => this.reopen_active(),
			});
		});
	}
}
