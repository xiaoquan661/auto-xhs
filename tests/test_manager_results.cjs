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
const tabs = ["results", "incremental", "reports"].map((key) => {
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
let selection = {machine:"",account:""};
let selectedCalls = [];
const view = context.window.AutoXhsResultView({
  recoverySelection: () => selection,
  selectRecoveryAccount: (machine, account) => {selection={machine,account}; selectedCalls.push(selection);},
  publishedMetricHistory: (record, id) => id === "note-17" ? [{metrics:{views:0, likes:2},captured_at:"2026-09-16"}] : [],
  publishedNoteMetrics: () => new Node("article", "", "每日数据"),
  resultLinks: () => [{url:"https://www.xiaohongshu.com/explore/example"}],
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
  factList: (pairs) => new Node("dl", "", JSON.stringify(pairs)),
  resultDate: r => (r.created_at || "").slice(0, 10),
  metricLabels: {publish: "发布", comment: "评论"}, collectionStatus: (machine) => ({cached: machine.offline}),
  resultMatchesFilters: (record) => record.machine_id === "PC-A",
});
view.init();
state.machines = [{machine_id:"PC-A", last_result_success_at:"2026-09-16"}, {machine_id:"PC-B"}];
const results = Array.from({length:18},(_,i) => ({task_id:`task-${i}`,machine_id:"PC-A",
  account_slot:"same-slot",capability:"publish",state:i%3 ? "SUCCESS":"FAILED",
  created_at:new Date(Date.UTC(2026,8,8,i)).toISOString(),
  item_results:[{note_id:`note-${i}`,title:`笔记 ${i}`}]}));
state.results=results;
const values={results,incremental:[],reports:[],resultHistory:results};
view.render(values);
assert.equal(node("#recovery-account-content").hidden,true);
const machines=node("#recovery-navigation").children[0].children[1].children;
assert.equal(machines.length,2);
machines[0].emit("click"); view.render(values);
assert.equal(selection.machine,"PC-A");
assert.equal(selection.account,"");
assert.equal(node("#recovery-account-content").hidden,true);
let accounts=node("#recovery-navigation").children[1].children[1].children;
assert.equal(accounts.length,1);
assert.ok(JSON.stringify(accounts).includes("山野日记"));
assert.ok(!JSON.stringify(accounts).includes("城市生活"));
accounts[0].emit("click"); view.render(values);
assert.equal(node("#recovery-account-content").hidden,false);
let cards=()=>node("#manager-result-list").children[0].children;
assert.equal(cards().length,8);
assert.equal(cards()[0].children[1].textContent,"笔记 17");
assert.ok(JSON.stringify(cards()[0]).includes('"textContent":"0"'));
assert.ok(JSON.stringify(cards()[0]).includes("点赞"));
cards()[0].children.at(-1).children.at(-1).emit("click");
assert.equal(node("#recovery-detail-dialog").open,true);
assert.ok(JSON.stringify(node("#recovery-detail-content").children).includes("每日数据"));
node("#recovery-next").emit("click");
assert.equal(node("#recovery-page-number").textContent,"2 / 3");
node("#recovery-search").value="笔记 17";node("#recovery-search").emit("input");
assert.equal(cards().length,1);
node("#recovery-sync-details").emit("click");
assert.equal(node("#recovery-sync-dialog").open,true);
node("#recovery-audit-next").emit("click");
assert.equal(node("#recovery-audit-page").textContent,"2 / 3 · 18 条回执");
node("#recovery-navigation").children[0].children[1].children[1].emit("click");
view.render(values);
assert.equal(selection.machine,"PC-B");assert.equal(selection.account,"");
assert.equal(node("#recovery-account-content").hidden,true);
accounts=node("#recovery-navigation").children[1].children[1].children;
assert.ok(JSON.stringify(accounts).includes("城市生活"));
assert.ok(!JSON.stringify(accounts).includes("山野日记"));
console.log("PASS: machine/account hierarchy, account isolation, note cards, zero metrics, details, search and pagination");
