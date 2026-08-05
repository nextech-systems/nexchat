// Copyright (c) 2026, Nextech Systems and contributors
// For license information, please see license.txt

frappe.pages["nexchat-center"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("NexChat Center"),
		single_column: true,
	});
	new NexChatCenter(page);
};

class NexChatCenter {
	constructor(page) {
		this.page = page;
		this.conversations = [];
		this.active = null;
		this.tab = "mine";
		this.availability = "Available";
		this.sending = false;
		this.search = "";
		this.make();
		this.bind_realtime();
		this.refresh_all();
		this.poll = setInterval(() => this.refresh_all(true), 45000);
		this.hb = setInterval(() => this.heartbeat(), 60000);
		this.heartbeat();
	}

	make() {
		this.page.main.html(`
			<style>
				.nxc-bar { display:flex; align-items:center; gap:12px; padding:8px 12px; background:var(--card-bg); border:1px solid var(--border-color); border-radius:8px; margin-bottom:8px; }
				.nxc-avail select { height:28px; padding:2px 8px; font-size:12px; }
				.nxc-counts { display:flex; gap:16px; font-size:12px; color:var(--text-muted); margin-left:auto; }
				.nxc-counts b { font-size:14px; }
				.nxc-grid { display:grid; grid-template-columns: 270px minmax(0,1fr) 230px; gap:8px; height:calc(100vh - 180px); }
				.nxc-col { background:var(--card-bg); border:1px solid var(--border-color); border-radius:8px; display:flex; flex-direction:column; overflow:hidden; }
				.nxc-tabs { display:flex; font-size:12px; border-bottom:1px solid var(--border-color); }
				.nxc-tab { flex:1; text-align:center; padding:8px 0; cursor:pointer; color:var(--text-muted); }
				.nxc-tab.active { font-weight:600; color:var(--text-color); border-bottom:2px solid var(--primary); }
				.nxc-search { padding:8px; border-bottom:1px solid var(--border-color); }
				.nxc-search input { width:100%; }
				.nxc-list { overflow-y:auto; flex:1; }
				.nxc-conv { padding:9px 11px; border-bottom:1px solid var(--border-color); cursor:pointer; }
				.nxc-conv:hover { background:var(--bg-color); }
				.nxc-conv.active { background:var(--bg-color); border-left:3px solid var(--primary); padding-left:8px; }
				.nxc-conv.sticky { border-left:3px solid var(--green, #28a745); padding-left:8px; }
				.nxc-conv-top { display:flex; justify-content:space-between; align-items:center; gap:6px; }
				.nxc-conv-name { font-weight:600; font-size:13px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
				.nxc-badge { background:var(--primary); color:#fff; font-size:10px; border-radius:9px; padding:0 6px; }
				.nxc-clock { font-size:10px; color:var(--red,#d33); white-space:nowrap; }
				.nxc-sub { font-size:11px; color:var(--text-muted); margin-top:2px; }
				.nxc-chip { background:var(--bg-color); border:1px solid var(--border-color); font-size:10px; border-radius:8px; padding:0 5px; }
				.nxc-prev { font-size:11px; color:var(--text-muted); margin-top:1px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
				.nxc-head { padding:9px 12px; border-bottom:1px solid var(--border-color); display:flex; align-items:center; gap:8px; }
				.nxc-head-name { font-weight:600; font-size:13px; }
				.nxc-head-sub { font-size:11px; color:var(--text-muted); }
				.nxc-banner { font-size:11px; padding:5px 12px; background:var(--bg-color); border-bottom:1px solid var(--border-color); color:var(--text-muted); }
				.nxc-msgs { flex:1; overflow-y:auto; padding:12px; display:flex; flex-direction:column; gap:7px; background:var(--bg-color); }
				.nxc-bubble { max-width:74%; padding:7px 11px; border-radius:10px; font-size:13px; white-space:pre-wrap; word-break:break-word; }
				.nxc-in { align-self:flex-start; background:var(--card-bg); border:1px solid var(--border-color); }
				.nxc-out { align-self:flex-end; background:var(--blue-100,#d8e6fd); }
				.nxc-meta { font-size:10px; color:var(--text-muted); margin-top:3px; }
				.nxc-compose { display:flex; gap:6px; padding:8px; border-top:1px solid var(--border-color); }
				.nxc-compose textarea { flex:1; resize:none; height:54px; }
				.nxc-empty { display:flex; align-items:center; justify-content:center; flex:1; color:var(--text-muted); font-size:13px; }
				.nxc-cust { padding:11px; overflow-y:auto; }
				.nxc-cust h6 { font-size:11px; color:var(--text-muted); margin:10px 0 3px; text-transform:uppercase; letter-spacing:.04em; }
				.nxc-stat { font-size:15px; font-weight:600; }
				.nxc-cust .btn { width:100%; margin-top:6px; font-size:12px; }
			</style>
			<div class="nxc-bar">
				<span class="nxc-avail"><select class="form-control">
					<option>Available</option><option>Away</option><option>Off</option>
				</select></span>
				<div class="nxc-counts">
					<span><b class="nxc-c-mine">0</b> mine</span>
					<span><b class="nxc-c-un" style="color:var(--orange,#e8910c)">0</b> unassigned</span>
					<span><b class="nxc-c-over" style="color:var(--red,#d33)">0</b> overdue</span>
				</div>
			</div>
			<div class="nxc-grid">
				<div class="nxc-col">
					<div class="nxc-tabs">
						<div class="nxc-tab" data-tab="mine">Mine</div>
						<div class="nxc-tab" data-tab="unassigned">Unassigned</div>
						<div class="nxc-tab" data-tab="all">All</div>
					</div>
					<div class="nxc-search"><input class="form-control" placeholder="${__("Search")}"></div>
					<div class="nxc-list"></div>
				</div>
				<div class="nxc-col nxc-thread"><div class="nxc-empty">${__("Select a conversation")}</div></div>
				<div class="nxc-col nxc-cust"><div class="nxc-empty">${__("No customer")}</div></div>
			</div>
		`);
		this.$list = this.page.main.find(".nxc-list");
		this.$thread = this.page.main.find(".nxc-thread");
		this.$cust = this.page.main.find(".nxc-cust");

		this.page.main.find(`.nxc-tab[data-tab="${this.tab}"]`).addClass("active");
		this.page.main.find(".nxc-tab").on("click", (e) => {
			this.tab = e.currentTarget.dataset.tab;
			this.page.main.find(".nxc-tab").removeClass("active");
			$(e.currentTarget).addClass("active");
			this.load_conversations();
		});
		this.page.main.find(".nxc-avail select").on("change", (e) => {
			frappe.call({
				method: "nexchat.locks.set_availability",
				args: { status: e.target.value },
				callback: () => this.refresh_all(true),
			});
		});
		let t = null;
		this.page.main.find(".nxc-search input").on("input", (e) => {
			clearTimeout(t);
			this.search = e.target.value;
			t = setTimeout(() => this.load_conversations(), 300);
		});
	}

	heartbeat() {
		frappe.call({ method: "nexchat.locks.heartbeat", callback: (r) => {
			if (r.message) {
				this.availability = r.message;
				this.page.main.find(".nxc-avail select").val(r.message);
			}
		}});
	}

	bind_realtime() {
		frappe.realtime.on("nexchat_message", (d) => {
			this.refresh_all(true);
			if (this.active && d.conversation === this.active.name) {
				this.load_messages(this.active.name);
				if (d.direction === "Incoming") this.mark_read(this.active.name);
			}
		});
		frappe.realtime.on("nexchat_lock", () => this.load_conversations());
		frappe.realtime.on("nexchat_escalation", () => this.refresh_all(true));
		frappe.realtime.on("nexchat_ack", () => { if (this.active) this.load_messages(this.active.name); });
	}

	refresh_all(silent) {
		this.load_conversations();
		frappe.call({ method: "nexchat.api.workload_counts", callback: (r) => {
			if (!r.message) return;
			this.page.main.find(".nxc-c-mine").text(r.message.mine);
			this.page.main.find(".nxc-c-un").text(r.message.unassigned);
			this.page.main.find(".nxc-c-over").text(r.message.overdue);
		}});
	}

	load_conversations() {
		frappe.call({
			method: "nexchat.api.get_conversations",
			args: { search: this.search || null, tab: this.tab },
			callback: (r) => { this.conversations = r.message || []; this.render_list(); },
		});
	}

	render_list() {
		const esc = frappe.utils.escape_html;
		this.$list.empty();
		if (!this.conversations.length) {
			this.$list.html(`<div class="nxc-conv text-muted">${__("Nothing here")}</div>`);
			return;
		}
		const multi = new Set(this.conversations.map((x) => x.session_label).filter(Boolean)).size > 1;
		this.conversations.forEach((c) => {
			const cls = (this.active && this.active.name === c.name) ? "active" : (c.sticky_mine ? "sticky" : "");
			const name = esc(c.display_name || c.name);
			const sub = c.customer ? esc(c.customer) : (c.lead ? __("Lead") : (c.sticky_mine ? __("Returning - yours") : __("Unlinked")));
			const chip = multi && c.session_label ? ` <span class="nxc-chip">${esc(c.session_label)}</span>` : "";
			const badge = c.unread_count ? `<span class="nxc-badge">${c.unread_count}</span>` : "";
			const clock = (c.waiting && c.sla_state === "Escalated") ? `<span class="nxc-clock">overdue</span>`
				: (c.waiting && c.sla_state === "Reminded") ? `<span class="nxc-clock">waiting</span>` : "";
			const $r = $(`<div class="nxc-conv ${cls}">
				<div class="nxc-conv-top"><span class="nxc-conv-name">${name}</span>${badge||clock}</div>
				<div class="nxc-sub">${sub}${chip}</div>
				<div class="nxc-prev">${esc(c.last_message_preview || "")}</div>
			</div>`);
			$r.on("click", () => this.open(c));
			this.$list.append($r);
		});
	}

	open(c) {
		this.active = c;
		this.render_list();
		this.render_thread(c);
		this.load_messages(c.name);
		this.load_customer(c);
		if (c.unread_count) this.mark_read(c.name);
	}

	render_thread(c) {
		const esc = frappe.utils.escape_html;
		const me = frappe.session.user;
		let banner = "", composer = true;
		if (c.assigned_agent && c.assigned_agent !== me) {
			banner = `<div class="nxc-banner">${__("Claimed by")} ${esc(c.assigned_agent_name || c.assigned_agent)} — ${__("read only")} <button class="btn btn-xs nxc-takeover" style="margin-left:6px">${__("Take over")}</button></div>`;
			composer = false;
		} else if (c.assigned_agent === me) {
			banner = `<div class="nxc-banner">${__("Claimed by you")} <button class="btn btn-xs nxc-release" style="margin-left:6px">${__("Release")}</button></div>`;
		}
		this.$thread.html(`
			<div class="nxc-head">
				<div style="flex:1;min-width:0">
					<div class="nxc-head-name">${esc(c.display_name || c.name)}</div>
					<div class="nxc-head-sub">${esc(c.resolved_number || c.name)}${c.session_label ? " · " + esc(c.session_label) : ""}</div>
				</div>
			</div>
			${banner}
			<div class="nxc-msgs"></div>
			${composer ? `<div class="nxc-compose"><textarea class="form-control" placeholder="${__("Type a reply")}" maxlength="4096"></textarea><button class="btn btn-primary btn-sm nxc-send">${__("Send")}</button></div>` : ""}
		`);
		this.$msgs = this.$thread.find(".nxc-msgs");
		if (composer) {
			this.$input = this.$thread.find("textarea");
			this.$thread.find(".nxc-send").on("click", () => this.send());
			this.$input.on("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this.send(); }});
		}
		this.$thread.find(".nxc-release").on("click", () => this.lock_action("release", c.name));
		this.$thread.find(".nxc-takeover").on("click", () => this.lock_action("take_over", c.name));
	}

	lock_action(action, name) {
		frappe.call({ method: `nexchat.locks.${action}`, args: { conversation: name }, callback: () => this.load_conversations() });
	}

	load_messages(name) {
		frappe.call({ method: "nexchat.api.get_messages", args: { conversation: name, limit: 50 }, callback: (r) => {
			if (!this.active || this.active.name !== name || !this.$msgs) return;
			const esc = frappe.utils.escape_html;
			this.$msgs.empty();
			(r.message || []).forEach((m) => {
				const dir = m.direction === "Outgoing" ? "nxc-out" : "nxc-in";
				let body = esc(m.message || "");
				if (!body && m.content_type !== "text") body = `[${esc(m.content_type)}]`;
				const ack = (m.direction === "Outgoing" && m.ack_status) ? ` · ${esc(m.ack_status)}` : "";
				this.$msgs.append(`<div class="nxc-bubble ${dir}"><div>${body}</div><div class="nxc-meta">${esc(frappe.datetime.str_to_user(m.timestamp))}${ack}</div></div>`);
			});
			this.$msgs.scrollTop(this.$msgs[0].scrollHeight);
		}});
	}

	send() {
		if (this.sending || !this.active) return;
		const text = (this.$input.val() || "").trim();
		if (!text) return;
		this.sending = true;
		const $b = this.$thread.find(".nxc-send").prop("disabled", true);
		frappe.call({
			method: "nexchat.api.send_message",
			args: { conversation: this.active.name, message: text },
			callback: () => { this.$input.val(""); this.load_messages(this.active.name); this.refresh_all(true); },
			always: () => { this.sending = false; $b.prop("disabled", false); this.$input.focus(); },
		});
	}

	mark_read(name) {
		frappe.call({ method: "nexchat.api.mark_read", args: { conversation: name }, callback: () => this.refresh_all(true) });
	}

	load_customer(c) {
		frappe.call({ method: "nexchat.api.customer_summary", args: { conversation: c.name }, callback: (r) => {
			if (!this.active || this.active.name !== c.name) return;
			this.render_customer(r.message || {});
		}});
	}

	render_customer(s) {
		const esc = frappe.utils.escape_html;
		const fmt = (v) => v == null ? "—" : format_currency(v, frappe.defaults.get_default("currency"));
		if (!s.customer) {
			this.$cust.html(`
				<div style="text-align:center;padding-top:10px">
					<div style="font-size:13px;font-weight:600">${esc(s.display_name || s.resolved_number || "")}</div>
					<div class="nxc-sub">${s.lead ? __("Lead: {0}", [esc(s.lead)]) : __("Not linked to a customer")}</div>
				</div>
				<button class="btn btn-default btn-xs nxc-link">${__("Link")}</button>
				<button class="btn btn-default btn-xs nxc-lead">${__("Create Lead")}</button>
			`);
			this.$cust.find(".nxc-link").on("click", () => this.open_link_dialog(s));
			this.$cust.find(".nxc-lead").on("click", () => this.create_lead(s));
			return;
		}
		this.$cust.html(`
			<div style="text-align:center;padding-bottom:9px;border-bottom:1px solid var(--border-color)">
				<div style="font-size:13px;font-weight:600">${esc(s.customer_name || s.customer)}</div>
				<a class="nxc-open" style="font-size:11px;cursor:pointer">${__("Open customer")} ↗</a>
			</div>
			<h6>${__("Outstanding balance")}</h6>
			<div class="nxc-stat" style="color:${s.balance > 0 ? "var(--red,#d33)" : "inherit"}">${fmt(s.balance)}</div>
			<h6>${__("Open orders")}</h6>
			<div class="nxc-stat">${s.open_orders != null ? s.open_orders : "—"}</div>
			<h6>${__("Last order")}</h6>
			<div style="font-size:13px">${s.last_order_date ? esc(frappe.datetime.str_to_user(s.last_order_date)) : "—"}</div>
			<button class="btn btn-default nxc-quote">${__("Create Quotation")}</button>
		`);
		this.$cust.find(".nxc-open").on("click", () => frappe.set_route("Form", "Customer", s.customer));
		this.$cust.find(".nxc-quote").on("click", () => {
			const customer = s.customer;
			frappe.new_doc("Quotation").then(() => {
				const apply = () => {
					const f = cur_frm;
					if (!f || f.doctype !== "Quotation") return;
					f.set_value("quotation_to", "Customer").then(() => {
						f.set_value("party_name", customer);
					});
				};
				// wait for the new form to be the current one, then apply once
				setTimeout(apply, 600);
			});
		});
	}

	open_link_dialog(s) {
		if (!this.active) return;
		const conv = this.active.name;
		const esc = frappe.utils.escape_html;
		frappe.call({
			method: "nexchat.crm.get_link_options",
			args: { conversation: conv },
			callback: (r) => {
				const m = r.message || {};
				const contacts = m.contacts || [];
				const leads = m.leads || [];
				const customers = m.customers || [];
				let rows = "";
				if (customers.length) {
					rows += '<h6 style="margin:10px 0 4px;font-size:11px;color:var(--text-muted);text-transform:uppercase;">Customers</h6>';
					customers.forEach(c => {
						rows += '<div class="nxc-lr" data-doctype="Customer" data-name="' + esc(c.name) + '" style="padding:6px 10px;cursor:pointer;border-radius:6px;border:1px solid var(--border-color);margin-bottom:4px;font-size:13px;"><b>' + esc(c.customer_name || c.name) + '</b><br><span style="font-size:11px;color:var(--text-muted)">' + esc(c.mobile_no || "") + '</span></div>';
					});
				}
				if (contacts.length) {
					rows += '<h6 style="margin:10px 0 4px;font-size:11px;color:var(--text-muted);text-transform:uppercase;">Contacts</h6>';
					contacts.forEach(c => {
						const nm = [c.first_name, c.last_name].filter(Boolean).join(" ") || c.name;
						rows += '<div class="nxc-lr" data-doctype="Contact" data-name="' + esc(c.name) + '" style="padding:6px 10px;cursor:pointer;border-radius:6px;border:1px solid var(--border-color);margin-bottom:4px;font-size:13px;"><b>' + esc(nm) + '</b><br><span style="font-size:11px;color:var(--text-muted)">' + esc(c.mobile_no || c.phone || "") + '</span></div>';
					});
				}
				if (leads.length) {
					rows += '<h6 style="margin:10px 0 4px;font-size:11px;color:var(--text-muted);text-transform:uppercase;">Leads</h6>';
					leads.forEach(c => {
						rows += '<div class="nxc-lr" data-doctype="Lead" data-name="' + esc(c.name) + '" style="padding:6px 10px;cursor:pointer;border-radius:6px;border:1px solid var(--border-color);margin-bottom:4px;font-size:13px;"><b>' + esc(c.lead_name || c.name) + '</b><br><span style="font-size:11px;color:var(--text-muted)">' + esc(c.mobile_no || c.phone || "") + '</span></div>';
					});
				}
				if (!rows) rows = '<div style="color:var(--text-muted);font-size:13px;padding:10px 0">No matches found. Search below or create a lead.</div>';
				const search_html = '<div style="margin-top:12px;border-top:1px solid var(--border-color);padding-top:10px"><h6 style="font-size:11px;color:var(--text-muted);text-transform:uppercase;margin-bottom:6px;">Search manually</h6><div style="display:flex;gap:6px;margin-bottom:6px;"><select class="form-control nxc-link-dt" style="width:120px;height:30px;font-size:12px;"><option value="Customer">Customer</option><option value="Lead">Lead</option><option value="Contact">Contact</option></select><input class="form-control nxc-link-search" placeholder="Type to search..." style="height:30px;font-size:12px;"></div><div class="nxc-link-results"></div></div>';
				const d = new frappe.ui.Dialog({
					title: __("Link Conversation"),
					fields: [{ fieldtype: "HTML", fieldname: "body" }],
					primary_action_label: __("Close"),
					primary_action: () => d.hide(),
				});
				d.fields_dict.body.$wrapper.html('<div style="max-height:420px;overflow-y:auto;">' + rows + search_html + '</div>');
				d.show();
				const do_link = (doctype, name) => {
					frappe.call({
						method: "nexchat.crm.link_conversation",
						args: { conversation: conv, doctype: doctype, docname: name },
						callback: () => {
							frappe.show_alert({ message: __("Linked to {0}: {1}", [doctype, name]), indicator: "green" });
							d.hide();
							this.load_customer(this.active);
							this.load_conversations();
						},
					});
				};
				d.$wrapper.find(".nxc-lr").on("click", function() {
					do_link($(this).data("doctype"), $(this).data("name"));
				}).on("mouseenter", function() {
					$(this).css("background", "var(--bg-color)");
				}).on("mouseleave", function() {
					$(this).css("background", "");
				});
				let st = null;
				d.$wrapper.find(".nxc-link-search").on("input", function() {
					clearTimeout(st);
					const q = $(this).val().trim();
					const dt = d.$wrapper.find(".nxc-link-dt").val();
					if (!q) { d.$wrapper.find(".nxc-link-results").empty(); return; }
					st = setTimeout(() => {
						frappe.call({
							method: "frappe.client.get_list",
							args: {
								doctype: dt,
								filters: [[ dt, dt === "Contact" ? "full_name" : (dt === "Lead" ? "lead_name" : "customer_name"), "like", "%" + q + "%" ]],
								fields: ["name", dt === "Contact" ? "full_name" : (dt === "Lead" ? "lead_name" : "customer_name")],
								limit: 6
							},
							callback: (r2) => {
								const res = r2.message || [];
								const $r = d.$wrapper.find(".nxc-link-results");
								$r.empty();
								if (!res.length) { $r.html('<div style="font-size:12px;color:var(--text-muted);padding:4px 0">No results</div>'); return; }
								res.forEach(item => {
									const label = item.full_name || item.lead_name || item.customer_name || item.name;
									$('<div style="padding:5px 8px;cursor:pointer;border-radius:4px;font-size:13px;border:1px solid var(--border-color);margin-bottom:3px;">' + esc(label) + '</div>')
										.on("click", () => do_link(dt, item.name))
										.on("mouseenter", function() { $(this).css("background","var(--bg-color)"); })
										.on("mouseleave", function() { $(this).css("background",""); })
										.appendTo($r);
								});
							},
						});
					}, 300);
				});
			},
		});
	}

	create_lead(s) {
		if (!this.active) return;
		const conv = this.active.name;
		const display = s.display_name || s.resolved_number || "";
		const phone = s.resolved_number || "";
		frappe.new_doc("Lead").then(() => {
			setTimeout(() => {
				const f = cur_frm;
				if (!f || f.doctype !== "Lead") return;
				if (display) f.set_value("lead_name", display);
				if (phone) f.set_value("mobile_no", phone);
				// Link back to conversation after save
				f.doc.__nexchat_conv = conv;
				const orig_save = f.save_or_update ? f.save_or_update.bind(f) : null;
				frappe.after_ajax(() => {});
			}, 600);
		});
	}
}
