const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class Node {
  constructor(tag = "div", className = "", text = "") {
    Object.assign(this, {tag, className, textContent: text, children: [],
      dataset: {}, attributes: {}, events: {}, value: ""});
  }
  appendChild(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) { (this.events[name] ||= []).push(callback); }
  emit(name, values = {}) {
    for (const callback of this.events[name] || []) callback({target: this, ...values});
  }
  focus() { this.focused = true; }
  showModal() { this.open = true; }
  close() { this.open = false; }
}
const nodes = {};
const node = (selector) => nodes[selector] ||= new Node();
const tabs = ["results", "incremental", "reports", "audit"].map((key) => {
  const button = new Node("button"); button.dataset.recoveryTab = key; return button;
});
const panes = tabs.map((button) => {
  const pane = new Node(); pane.dataset.recoveryPane = button.dataset.recoveryTab; return pane;
});
const scopes = ["all", "attention", "success"].map((key) => {
  const button = new Node("button"); button.dataset.recoveryScope = key; return button;
});
const root = {querySelector: node, querySelectorAll: (selector) => ({
  "[data-recovery-tab]": tabs, "[data-recovery-pane]": panes,
  "[data-recovery-scope]": scopes,
})[selector]};
const state = {
  accounts: [{machine_id: "PC-A", account_slot: "same-slot", nickname: "山野日记"},
    {machine_id: "PC-B", account_slot: "same-slot", nickname: "城市生活"}],
  machines: [{last_result_success_at: "2026-09-08T10:00:00Z",
    outbox_collection: {pending_records: 0}}],
  operationReports: [{report_id: "report-a", definition: {machine_id: "PC-A"},
    content: {actual_total: 0, target_total: 5}}],
};
const context = vm.createContext({window: {}, document: {querySelector: () => root}});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../webui/manager-results.js"), "utf8"), context);
const view = context.window.AutoXhsResultView({
  state, element: (...args) => new Node(...args),
  append: (parent, ...children) => children.forEach((child) => parent.appendChild(child)),
  stateBadge: (value) => new Node("span", "", value), formatTime: (value) => value || "—",
  resultAccount: (record) => record.account_slot,
  resultCapability: (record) => record.capability,
  actualResultCount: (record) => record.actual,
  requestedResultCount: (record) => record.target,
  resultDetailCard: (record) => new Node("article", "", record.task_id),
  resultAuditCard: (record) => new Node("article", "", record.task_id),
  operationReportCard: (record) => new Node("article", "", record.report_id),
  factList: () => new Node(), collectionStatus: (machine) => ({cached: machine.offline}),
  resultMatchesFilters: (record) => record.machine_id === "PC-A",
});
view.init();
const results = Array.from({length: 18}, (_, index) => ({
  task_id: `task-${index}`, machine_id: index % 2 ? "PC-B" : "PC-A",
  account_slot: "same-slot", capability: "post-comment", summary: `评论记录 ${index}`,
  created_at: new Date(Date.UTC(2026, 8, 8, index)).toISOString(),
  state: index % 3 ? "SUCCESS" : "FAILED", actual: index === 17 ? 0 : undefined, target: 5,
}));
const values = {results, incremental: [{record_type: "record", machine_id: "PC-A"}],
  resultHistory: results.map((record) => ({...record, result_revision: 2}))};
const rows = (selector = "#manager-result-list") => node(selector).children[0].children[0]
  .children.find((child) => child.tag === "tbody").children;
const cellText = (row, column) => row.children[column].children[0].textContent;

view.render(values);
assert.equal(rows().length, 8);
assert.equal(rows()[0].children[0].children[0].children[1].textContent, "评论记录 17");
assert.equal(cellText(rows()[0], 2), "0 / 5");
assert.equal(cellText(rows()[1], 2), "— / 5");
assert.equal(node("#recovery-attention-total").textContent, 6);
assert.equal(node("#recovery-success-total").textContent, 12);
assert.equal(node("#recovery-prev").disabled, true);
node("#recovery-next").emit("click");
assert.equal(node("#recovery-page-number").textContent, "2 / 3");
view.render(values);
assert.equal(node("#recovery-page-number").textContent, "2 / 3");
node("#recovery-next").emit("click");
assert.equal(rows().length, 2);
assert.equal(node("#recovery-next").disabled, true);
scopes[1].emit("click");
assert.equal(rows().length, 6);
assert.equal(node("#recovery-page-number").textContent, "1 / 1");
assert.ok(view.visibleResults().every((record) => record.state === "FAILED"));
scopes[0].emit("click");
node("#recovery-search").value = "山野日记";
node("#recovery-search").emit("input");
assert.equal(view.visibleResults().length, 9);
assert.ok(view.visibleResults().every((record) => record.machine_id === "PC-A"));
node("#recovery-search").value = "发表评论";
node("#recovery-search").emit("input");
assert.equal(view.visibleResults().length, 18);
rows()[0].children.at(-1).children[0].emit("click");
assert.equal(node("#recovery-detail-dialog").open, true);
assert.equal(node("#recovery-detail-content").children[0].textContent, "task-17");
node("#recovery-detail-close").emit("click");
assert.equal(node("#recovery-detail-dialog").open, false);
node("#recovery-search").value = "不存在的内容";
node("#recovery-search").emit("input");
assert.equal(node("#recovery-page-info").textContent, "共 0 条");
assert.equal(node("#recovery-next").disabled, true);
node("#manager-result-reset").emit("click");
assert.equal(rows().length, 8);
assert.equal(node("#recovery-search").value, "");
console.log("PASS: newest results, pagination, refresh, zero counts, scoped search and detail dialog");

tabs[1].emit("click");
assert.equal(panes[0].hidden, true);
assert.equal(panes[1].hidden, false);
assert.equal(node("#recovery-list-tools").hidden, true);
assert.equal(rows("#manager-incremental-list").length, 1);
tabs[2].emit("click");
assert.equal(rows("#manager-operation-report-list").length, 1);
assert.equal(cellText(rows("#manager-operation-report-list")[0], 2), "0 / 5");
tabs[2].emit("keydown", {key: "ArrowRight", preventDefault() {}});
assert.equal(tabs[3].focused, true);
assert.equal(rows("#manager-result-audit-list").length, 8);
node("#recovery-next").emit("click");
assert.equal(node("#recovery-page-number").textContent, "2 / 3");
node(".recovery-filters").emit("change");
assert.equal(node("#recovery-page-number").textContent, "1 / 3");
node("#recovery-sync-details").emit("click");
assert.equal(node(".recovery-technical").open, true);
assert.equal(panes[3].hidden, false);
state.machines[0].offline = true;
view.render(values);
assert.equal(node("#recovery-sync-label").textContent, "1 台员工机待同步");
assert.equal(node("#recovery-sync-bar").dataset.tone, "warning");
console.log("PASS: report and audit tabs, keyboard navigation, filter reset and sync status");
