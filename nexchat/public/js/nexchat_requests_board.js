// Sales Request board - shared by NexChat (Requests view) and /app/nexchat-requests
window.NexChatSalesRequestHub = window.NexChatSalesRequestHub || class {
    constructor($root) {
        this.$root = $root;
        this.requests = [];
        this.summary = {};
        this.my_tasks_only = false;

        this.statuses = [
            "New",
            "In Progress",
            "Waiting on Rep",
            "Quote Ready",
            "Completed",
        ];

        this.make();
        this.bind_actions();
        this.refresh();
    }


    make() {
        this.$root.html(`
            <style>
                .nxr-toolbar {
                    display: flex;
                    align-items: center;
                    gap: 10px;
                    margin-bottom: 12px;
                }

                .nxr-summary {
                    display: grid;
                    grid-template-columns: repeat(6, minmax(110px, 1fr));
                    gap: 8px;
                    margin-bottom: 14px;
                }

                .nxr-summary-card {
                    background: var(--card-bg);
                    border: 1px solid var(--border-color);
                    border-radius: 8px;
                    padding: 10px 12px;
                }

                .nxr-summary-label {
                    color: var(--text-muted);
                    font-size: 11px;
                    text-transform: uppercase;
                    letter-spacing: .04em;
                }

                .nxr-summary-value {
                    margin-top: 2px;
                    font-size: 22px;
                    font-weight: 600;
                }

                .nxr-board {
                    display: grid;
                    grid-template-columns: repeat(5, minmax(220px, 1fr));
                    gap: 10px;
                    overflow-x: auto;
                    align-items: start;
                    padding-bottom: 12px;
                }

                .nxr-column {
                    min-width: 220px;
                    background: var(--bg-color);
                    border: 1px solid var(--border-color);
                    border-radius: 8px;
                    overflow: hidden;
                }

                .nxr-column-head {
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    padding: 10px 12px;
                    background: var(--card-bg);
                    border-bottom: 1px solid var(--border-color);
                    font-weight: 600;
                    font-size: 12px;
                }

                .nxr-column-count {
                    color: var(--text-muted);
                    font-weight: 400;
                }

                .nxr-column-body {
                    padding: 8px;
                    min-height: 180px;
                }

                .nxr-card {
                    background: var(--card-bg);
                    border: 1px solid var(--border-color);
                    border-radius: 8px;
                    padding: 10px;
                    margin-bottom: 8px;
                    cursor: pointer;
                }

                .nxr-card:hover {
                    border-color: var(--primary);
                }

                .nxr-card-top {
                    display: flex;
                    justify-content: space-between;
                    align-items: flex-start;
                    gap: 8px;
                }

                .nxr-name {
                    font-weight: 600;
                    font-size: 12px;
                }

                .nxr-priority {
                    font-size: 10px;
                    border: 1px solid var(--border-color);
                    border-radius: 10px;
                    padding: 1px 6px;
                    white-space: nowrap;
                }

                .nxr-priority-high,
                .nxr-priority-urgent {
                    font-weight: 600;
                }

                .nxr-customer {
                    font-size: 13px;
                    font-weight: 600;
                    margin-top: 7px;
                }

                .nxr-meta {
                    margin-top: 4px;
                    font-size: 11px;
                    color: var(--text-muted);
                }

                .nxr-text {
                    margin-top: 7px;
                    font-size: 12px;
                    line-height: 1.35;
                    display: -webkit-box;
                    -webkit-line-clamp: 3;
                    -webkit-box-orient: vertical;
                    overflow: hidden;
                }

                .nxr-quotation {
                    margin-top: 7px;
                    font-size: 11px;
                }

                .nxr-empty {
                    padding: 18px 8px;
                    text-align: center;
                    color: var(--text-muted);
                    font-size: 12px;
                }

                @media (max-width: 1100px) {
                    .nxr-summary {
                        grid-template-columns: repeat(3, 1fr);
                    }
                }
            </style>

            <div class="nxr-toolbar">
                <button class="btn btn-sm btn-default nxr-refresh">
                    ${__("Refresh")}
                </button>

                <label style="margin:0; display:flex; align-items:center; gap:6px;">
                    <input type="checkbox" class="nxr-my-tasks">
                    <span>${__("My Tasks")}</span>
                </label>
            </div>

            <div class="nxr-summary">
                <div class="nxr-summary-card">
                    <div class="nxr-summary-label">${__("Total")}</div>
                    <div class="nxr-summary-value nxr-s-total">0</div>
                </div>

                <div class="nxr-summary-card">
                    <div class="nxr-summary-label">${__("New")}</div>
                    <div class="nxr-summary-value nxr-s-new">0</div>
                </div>

                <div class="nxr-summary-card">
                    <div class="nxr-summary-label">${__("In Progress")}</div>
                    <div class="nxr-summary-value nxr-s-progress">0</div>
                </div>

                <div class="nxr-summary-card">
                    <div class="nxr-summary-label">${__("Waiting")}</div>
                    <div class="nxr-summary-value nxr-s-waiting">0</div>
                </div>

                <div class="nxr-summary-card">
                    <div class="nxr-summary-label">${__("Quote Ready")}</div>
                    <div class="nxr-summary-value nxr-s-ready">0</div>
                </div>

                <div class="nxr-summary-card">
                    <div class="nxr-summary-label">${__("Completed")}</div>
                    <div class="nxr-summary-value nxr-s-completed">0</div>
                </div>
            </div>

            <div class="nxr-board"></div>
        `);

        this.$board = this.$root.find(".nxr-board");
    }


    bind_actions() {
        this.$root.find(".nxr-refresh").on("click", () => {
            this.refresh();
        });

        this.$root.find(".nxr-my-tasks").on("change", (e) => {
            this.my_tasks_only = e.target.checked;
            this.load_requests();
        });
    }


    refresh() {
        this.load_summary();
        this.load_requests();
    }


    load_summary() {
        frappe.call({
            method: "nexchat.nexchat.doctype.sales_request.sales_request.get_sales_request_summary",
            callback: (r) => {
                this.summary = r.message || {};
                this.render_summary();
            },
        });
    }


    load_requests() {
        const args = {
            limit: 500,
        };

        if (this.my_tasks_only) {
            args.assigned_to = frappe.session.user;
        }

        frappe.call({
            method: "nexchat.nexchat.doctype.sales_request.sales_request.get_sales_requests",
            args,
            callback: (r) => {
                const result = r.message || {};
                this.requests = result.requests || [];
                this.render_board();
            },
        });
    }


    render_summary() {
        this.$root.find(".nxr-s-total").text(this.summary.total || 0);
        this.$root.find(".nxr-s-new").text(this.summary.new || 0);
        this.$root.find(".nxr-s-progress").text(this.summary.in_progress || 0);
        this.$root.find(".nxr-s-waiting").text(this.summary.waiting_on_rep || 0);
        this.$root.find(".nxr-s-ready").text(this.summary.quote_ready || 0);
        this.$root.find(".nxr-s-completed").text(this.summary.completed || 0);
    }


    render_board() {
        const esc = frappe.utils.escape_html;

        this.$board.empty();

        this.statuses.forEach((status) => {
            const rows = this.requests.filter(
                (row) => row.status === status
            );

            const $column = $(`
                <div class="nxr-column">
                    <div class="nxr-column-head">
                        <span>${esc(status)}</span>
                        <span class="nxr-column-count">${rows.length}</span>
                    </div>

                    <div class="nxr-column-body"></div>
                </div>
            `);

            const $body = $column.find(".nxr-column-body");

            if (!rows.length) {
                $body.html(
                    `<div class="nxr-empty">${__("No requests")}</div>`
                );
            }

            rows.forEach((row) => {
                $body.append(
                    this.make_card(row)
                );
            });

            this.$board.append($column);
        });
    }


    make_card(row) {
        const esc = frappe.utils.escape_html;

        const priority_class = (
            row.priority || "Normal"
        ).toLowerCase().replaceAll(" ", "-");

        const customer = row.customer || (row.lead ? __("Lead: {0}", [row.lead_name || row.lead]) : __("Customer not resolved"));
        const assigned =
            row.assigned_to_name
            || row.assigned_to
            || __("Unassigned");
        const request_text = row.request_text || "";
        const quotation = row.quotation || "";

        const $card = $(`
            <div class="nxr-card">
                <div class="nxr-card-top">
                    <div class="nxr-name">${esc(row.name)}</div>
                    <div class="nxr-priority nxr-priority-${esc(priority_class)}">
                        ${esc(row.priority || "Normal")}
                    </div>
                </div>

                <div class="nxr-customer">
                    ${esc(customer)}
                </div>

                <div class="nxr-meta">
                    ${__("Rep")}: ${esc(row.rep_name || row.rep_code || "—")}
                </div>

                <div class="nxr-meta">
                    ${__("Assigned")}: ${esc(assigned)}
                </div>

                <div class="nxr-text">
                    ${esc(request_text)}
                </div>

                ${
                    (row.has_transcript || row.call_transcript)
                        ? `<div class="nxr-meta">&#9742; ${__("Call transcript")}</div>`
                        : ""
                }

                ${
                    quotation
                        ? `<div class="nxr-quotation">${__("Draft Quote")}: ${esc(quotation)}</div>`
                        : ""
                }
            </div>
        `);

        $card.on("click", () => {
            this.open_request(row.name);
        });

        return $card;
    }


    open_request(name) {
        frappe.call({
            method: "nexchat.nexchat.doctype.sales_request.sales_request.get_sales_request",
            args: {
                name,
            },
            freeze: true,
            freeze_message: __("Loading request..."),
            callback: (r) => {
                if (!r.message) {
                    frappe.msgprint(
                        __("Sales Request could not be loaded.")
                    );
                    return;
                }

                this.show_request_dialog(r.message);
            },
        });
    }


    show_request_dialog(row) {
        const dialog = new frappe.ui.Dialog({
            title: row.name,
            size: "large",
            fields: [
                {
                    fieldname: "status",
                    label: __("Status"),
                    fieldtype: "Select",
                    options: (
                        row.status === "Quote Ready"
                            ? [
                                "Quote Ready",
                                "Cancelled",
                            ]
                            : row.status === "Completed"
                            ? [
                                "Completed",
                            ]
                            : row.status === "Cancelled"
                            ? [
                                "Cancelled",
                            ]
                            : [
                                "New",
                                "In Progress",
                                "Waiting on Rep",
                                "Cancelled",
                            ]
                    ).join("\n"),
                    default: row.status,
                    reqd: 1,
                },
                {
                    fieldname: "priority",
                    label: __("Priority"),
                    fieldtype: "Select",
                    options: [
                        "Low",
                        "Normal",
                        "High",
                        "Urgent",
                    ].join("\n"),
                    default: row.priority || "Normal",
                    reqd: 1,
                },
                {
                    fieldname: "assigned_to",
                    label: __("Assigned To"),
                    fieldtype: "Link",
                    options: "User",
                    default: row.assigned_to || "",
                },
                {
                    fieldname: "customer_section",
                    label: __("Customer"),
                    fieldtype: "HTML",
                },
                {
                    fieldname: "quotation",
                    label: __("Draft Quotation"),
                    fieldtype: "Link",
                    options: "Quotation",
                    default: row.quotation || "",
                    read_only: 1,
                },
                {
                    fieldname: "request_text",
                    label: __("Request Text"),
                    fieldtype: "Small Text",
                    default: row.request_text || "",
                    read_only: 1,
                },
                {
                    fieldtype: "Section Break",
                    label: __("Call Transcript"),
                    collapsible: 1,
                },
                {
                    fieldname: "call_transcript_html",
                    fieldtype: "HTML",
                    options: row.call_transcript
                        ? `<pre style="white-space: pre-wrap; max-height: 320px; overflow-y: auto; font-size: 12px; margin: 0;">${frappe.utils.escape_html(row.call_transcript)}</pre>`
                        : `<div class="text-muted">${__("No call transcript for this request.")}</div>`,
                },
                {
                    fieldtype: "Section Break",
                },
                {
                    fieldname: "rep_name",
                    label: __("Sales Rep"),
                    fieldtype: "Data",
                    default: row.rep_name || row.rep_code || "",
                    read_only: 1,
                },
                {
                    fieldname: "sales_person",
                    label: __("ERPNext Sales Person"),
                    fieldtype: "Link",
                    options: "Sales Person",
                    default: row.sales_person || "",
                    read_only: 1,
                },
            ],

            primary_action_label: __("Save"),
            primary_action: (values) => {
                frappe.call({
                    method: "nexchat.nexchat.doctype.sales_request.sales_request.update_sales_request",
                    args: {
                        name: row.name,
                        status: values.status,
                        priority: values.priority,
                        assigned_to: values.assigned_to || "",
                    },
                    freeze: true,
                    freeze_message: __("Updating request..."),
                    callback: (r) => {
                        if (!r.message) {
                            return;
                        }

                        dialog.hide();
                        this.refresh();

                        frappe.show_alert({
                            message: __("Sales Request updated"),
                            indicator: "green",
                        });
                    },
                });
            },
        });

        const $customer_section =
            dialog.fields_dict.customer_section.$wrapper;

        if (row.customer) {
            $customer_section.html(`
                <div style="
                    display:flex;
                    align-items:center;
                    justify-content:space-between;
                    gap:12px;
                    padding:10px 12px;
                    border:1px solid var(--border-color);
                    border-radius:8px;
                    margin-bottom:12px;
                ">
                    <div>
                        <div class="text-muted" style="font-size:11px">
                            ${__("Customer")}
                        </div>
                        <div style="font-weight:600">
                            ${frappe.utils.escape_html(row.customer)}
                        </div>
                    </div>

                    ${
                        !row.quotation
                            ? `
                                <button
                                    type="button"
                                    class="btn btn-default btn-xs nxr-resolve-customer"
                                >
                                    ${__("Change Customer")}
                                </button>
                            `
                            : ""
                    }
                </div>
            `);
        } else if (row.lead) {
            $customer_section.html(`
                <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 12px;border:1px solid var(--border-color);border-radius:8px;margin-bottom:12px;">
                    <div>
                        <div class="text-muted" style="font-size:11px">${__("Lead")}</div>
                        <div style="font-weight:600" class="nxr-lead-name">${frappe.utils.escape_html(row.lead)}</div>
                    </div>
                    <button type="button" class="btn btn-default btn-xs nxr-open-lead">${__("Open Lead")}</button>
                </div>
            `);
            frappe.db.get_value("Lead", row.lead, "lead_name").then((r) => {
                const nm = r && r.message && r.message.lead_name;
                if (nm) $customer_section.find(".nxr-lead-name").text(nm + " (" + row.lead + ")");
            });
            $customer_section.find(".nxr-open-lead").on("click", () => frappe.set_route("Form", "Lead", row.lead));
        } else {
            $customer_section.html(`
                <div style="
                    display:flex;
                    align-items:center;
                    justify-content:space-between;
                    gap:12px;
                    padding:10px 12px;
                    border:1px solid var(--border-color);
                    border-radius:8px;
                    margin-bottom:12px;
                ">
                    <div>
                        <div class="text-muted" style="font-size:11px">
                            ${__("Customer")}
                        </div>
                        <div class="text-muted">
                            ${__("Customer not resolved")}
                        </div>
                    </div>

                    <button
                        type="button"
                        class="btn btn-primary btn-xs nxr-resolve-customer"
                    >
                        ${__("Resolve Customer")}
                    </button>
                </div>
            `);
        }

        $customer_section
            .find(".nxr-resolve-customer")
            .on("click", () => {
                this.open_customer_resolver(
                    row,
                    dialog
                );
            });

        dialog.set_secondary_action_label(
            __("Open Full Request")
        );

        dialog.set_secondary_action(() => {
            dialog.hide();

            frappe.set_route(
                "Form",
                "Sales Request",
                row.name
            );
        });

        if (row.customer) {
            dialog.add_custom_action(
                __("Open Customer"),
                () => {
                    frappe.set_route(
                        "Form",
                        "Customer",
                        row.customer
                    );
                }
            );
        }

        if (row.status === "Quote Ready" && row.quotation) {
            dialog.add_custom_action(
                __("Mark Complete"),
                () => {
                    frappe.confirm(
                        __("Mark this Sales Request as completed?"),
                        () => {
                            frappe.call({
                                method: "nexchat.nexchat.doctype.sales_request.sales_request.update_sales_request",
                                args: {
                                    name: row.name,
                                    status: "Completed",
                                },
                                freeze: true,
                                freeze_message: __("Completing request..."),
                                callback: (r) => {
                                    if (!r.message) {
                                        return;
                                    }

                                    dialog.hide();
                                    this.refresh();

                                    frappe.show_alert({
                                        message: __("Sales Request completed"),
                                        indicator: "green",
                                    });
                                },
                            });
                        }
                    );
                }
            );
        }

        if (row.quotation) {
            dialog.add_custom_action(
                __("Open Draft Quotation"),
                () => {
                    frappe.set_route(
                        "Form",
                        "Quotation",
                        row.quotation
                    );
                }
            );
        } else {
            dialog.add_custom_action(
                __("Create Quotation"),
                () => {
                    const sales_request = row.name;
                    const customer = row.customer || row.lead || "";
                    const party_type = row.customer ? "Customer" : "Lead";

                    dialog.hide();

                    frappe.new_doc("Quotation").then(() => {
                        const apply_values = () => {
                            const frm = cur_frm;

                            if (
                                !frm
                                || frm.doctype !== "Quotation"
                            ) {
                                return;
                            }

                            frm.set_value(
                                "custom_nexchat_sales_request",
                                sales_request
                            );

                            if (customer) {
                                frm.set_value(
                                    "quotation_to",
                                    party_type
                                ).then(() => {
                                    frm.set_value(
                                        "party_name",
                                        customer
                                    );
                                });
                            }
                        };

                        setTimeout(
                            apply_values,
                            600
                        );
                    });
                }
            );
        }

        dialog.show();
    }

    open_customer_resolver(row, parent_dialog) {
        const dialog = new frappe.ui.Dialog({
            title: row.customer
                ? __("Change Customer")
                : __("Resolve Customer"),
            size: "large",
            fields: [
                {
                    fieldname: "search",
                    label: __("Search Customer"),
                    fieldtype: "Data",
                    reqd: 1,
                },
                {
                    fieldname: "results",
                    fieldtype: "HTML",
                },
            ],
        });

        const render_results = (customers) => {
            const $wrapper = dialog.fields_dict.results.$wrapper;
            $wrapper.empty();

            if (!customers.length) {
                $wrapper.html(
                    `<div class="text-muted" style="padding:12px 0">
                        ${__("No matching customers found")}
                    </div>`
                );
                return;
            }

            customers.forEach((customer) => {
                const esc = frappe.utils.escape_html;

                const $row = $(`
                    <div
                        class="nxr-customer-result"
                        style="
                            border:1px solid var(--border-color);
                            border-radius:8px;
                            padding:10px 12px;
                            margin-bottom:8px;
                            cursor:pointer;
                        "
                    >
                        <div style="font-weight:600">
                            ${esc(customer.customer_name || customer.name)}
                        </div>
                        <div class="text-muted" style="font-size:11px">
                            ${esc(customer.name)}
                            ${
                                customer.customer_group
                                    ? " · " + esc(customer.customer_group)
                                    : ""
                            }
                            ${
                                customer.territory
                                    ? " · " + esc(customer.territory)
                                    : ""
                            }
                        </div>
                    </div>
                `);

                $row.on("click", () => {
                    frappe.confirm(
                        __(
                            "Use customer {0} for this Sales Request?",
                            [customer.customer_name || customer.name]
                        ),
                        () => {
                            frappe.call({
                                method: "nexchat.nexchat.doctype.sales_request.sales_request.set_sales_request_customer",
                                args: {
                                    sales_request: row.name,
                                    customer: customer.name,
                                },
                                freeze: true,
                                freeze_message: __("Updating customer..."),
                                callback: (r) => {
                                    if (!r.message) {
                                        return;
                                    }

                                    dialog.hide();

                                    if (parent_dialog) {
                                        parent_dialog.hide();
                                    }

                                    this.refresh();

                                    frappe.show_alert({
                                        message: __("Customer resolved"),
                                        indicator: "green",
                                    });
                                },
                            });
                        }
                    );
                });

                $wrapper.append($row);
            });
        };

        const run_search = () => {
            const search = (
                dialog.get_value("search")
                || ""
            ).trim();

            if (search.length < 2) {
                render_results([]);
                return;
            }

            frappe.call({
                method: "nexchat.nexchat.doctype.sales_request.sales_request.search_customers",
                args: {
                    search,
                    limit: 20,
                },
                callback: (r) => {
                    const result = r.message || {};
                    render_results(
                        result.customers || []
                    );
                },
            });
        };

        dialog.show();

        const $input = dialog.fields_dict.search.$input;

        let timer = null;

        $input.on("input", () => {
            clearTimeout(timer);
            timer = setTimeout(
                run_search,
                300
            );
        });

        $input.on("keydown", (e) => {
            if (e.key === "Enter") {
                e.preventDefault();
                run_search();
            }
        });
    }

};
