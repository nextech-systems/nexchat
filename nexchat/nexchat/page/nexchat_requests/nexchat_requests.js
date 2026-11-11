frappe.pages["nexchat-requests"].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: __("Sales Request Hub"),
        single_column: true,
    });
    frappe.require("/assets/nexchat/js/nexchat_requests_board.js", () => {
        new NexChatSalesRequestHub(page.main);
    });
};
