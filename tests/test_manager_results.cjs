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
assert.ok(!JSON.stringify(accounts).includes("山野日记"));
assert.ok(JSON.stringify(accounts).includes(state.accounts[0].account_slot));
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
assert.equal(node("#recovery-page-number").textContent,"2 / 2");
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
assert.ok(!JSON.stringify(accounts).includes("城市生活"));
assert.ok(JSON.stringify(accounts).includes(state.accounts[1].account_slot));
assert.ok(!JSON.stringify(accounts).includes("山野日记"));
console.log("PASS: machine/account hierarchy, account isolation, note cards, zero metrics, details, search and pagination");


selection.machine = "PC-A"; selection.account = "same-slot";
node("#recovery-search").value = ""; node("#recovery-search").emit("input");
const recovered = {task_id:"recovered",machine_id:"PC-A",account_slot:"same-slot",
  capability:"publish",state:"SUCCESS",created_at:"2026-09-23T10:32:00",
  business_result:{title:"草稿恢复作品",note_id:"recovered-note",published:true}};
const attempts = [recovered, {...recovered,task_id:"duplicate"},
  {...recovered,task_id:"blocked",state:"BLOCKED",business_result:{title:"草稿恢复作品"}},
  {...recovered,task_id:"unknown",state:"RESULT_UNKNOWN",business_result:{title:"草稿恢复作品"}}];
view.render({results:attempts,incremental:[],reports:[],resultHistory:attempts});
assert.equal(cards().length,1);
assert.equal(cards()[0].children[1].textContent,"草稿恢复作品");
node("#recovery-sync-details").emit("click");
assert.ok(node("#recovery-audit-page").textContent.includes("4 条回执"));
console.log("PASS: confirmed works deduplicate by note, recovery attempts retained in audit");
const collected = {...recovered, task_id: 'collect', capability: 'collect-operations-metrics',
  business_result: {snapshot: {source:'creator_manager',captured_at:'2026-09-24T10:00:00Z',notes:[
    {entity_id:'recovered-note',title:'草稿恢复作品'},
    {entity_id:'manual-note',title:'平台已发布作品',extra:{published_at:'2026-09-23 12:00'}}]}}};
view.render({results:attempts,collectionResults:[collected],incremental:[],reports:[],resultHistory:[collected]});
assert.equal(cards().length,2);
assert.ok(JSON.stringify(cards()).includes('平台已发布作品'));
assert.ok(JSON.stringify(cards()).includes('2026-09-23 12:00'));
assert.equal(collected.capability,'collect-operations-metrics');
console.log('PASS: collected creator works join published list and deduplicate without changing receipts');
const source = fs.readFileSync(path.join(__dirname, '../webui/manager-console.js'), 'utf8');
const metricFunction = source.slice(source.indexOf('  function publishedMetricHistory('),
  source.indexOf('  function publishedNoteMetrics('));
const metricState = {results:[],resultHistory:[]};
const metricContext = vm.createContext({state:metricState,resultAccount:r=>r.account_slot,Intl,Date});
vm.runInContext(metricFunction,metricContext);
const base = {machine_id:'PC071',account_slot:'3-3'};
metricState.results = [{...base,business_result:{source:'creator_manager',captured_at:'2026-09-23T16:31:05Z',
  notes:[{note_id:'n',views:343,likes:22,favorites:8,comments:0,shares:2}]}},
  {machine_id:'PC052',account_slot:'3-3',business_result:{source:'creator_manager',captured_at:'2026-09-24T02:00:00Z',
    notes:[{note_id:'n',views:999}]}}];
let history = metricContext.publishedMetricHistory(base,'n');
assert.equal(history.length,1);
assert.deepEqual(JSON.parse(JSON.stringify(history[0].metrics)),{views:343,likes:22,favorites:8,comments:0,shares:2});
metricState.results[0].business_result.notes[0]={note_id:'n',metrics:{views:0,likes:0}};
history=metricContext.publishedMetricHistory(base,'n');
assert.equal(history[0].metrics.views,0);
assert.equal(history[0].metrics.comments,null);
console.log('PASS: flat and nested creator metrics, zero/null distinction and machine isolation');
const inventory = {...collected,business_result:{source:'creator_manager',captured_at:'2026-09-24T12:00:00Z',
  inventory_complete:true,notes:[{note_id:'manual-note',title:'当前作品'}]}};
view.render({results:attempts,collectionResults:[collected],inventoryResults:[inventory],incremental:[],reports:[],resultHistory:attempts});
assert.equal(cards().length,1);
assert.ok(!JSON.stringify(cards()).includes('草稿恢复作品'));
inventory.business_result.inventory_complete=false;
view.render({results:attempts,collectionResults:[collected],inventoryResults:[inventory],incremental:[],reports:[],resultHistory:attempts});
assert.equal(cards().length,2);
inventory.business_result.inventory_complete=true;inventory.business_result.notes=[];
view.render({results:attempts,collectionResults:[collected],inventoryResults:[inventory],incremental:[],reports:[],resultHistory:attempts});
assert.equal(node('[data-recovery-count="results"]').textContent,0);
console.log('PASS: complete inventory removes absent works, incomplete scan preserves works, confirmed empty clears list');
const fresh = {...recovered,task_id:'fresh',created_at:'2026-09-24T13:00:00Z',
  business_result:{published:true,note_id:'fresh-note',title:'清单采集后发布'}};
view.render({results:[...attempts,fresh],collectionResults:[collected],inventoryResults:[inventory],incremental:[],reports:[],resultHistory:attempts});
assert.equal(cards().length,1);
assert.equal(cards()[0].children[1].textContent,'清单采集后发布');
const older = {...inventory,business_result:{...inventory.business_result,captured_at:'2026-09-24T11:00:00Z',notes:[{note_id:'recovered-note'}]}};
view.render({results:attempts,collectionResults:[collected],inventoryResults:[older,inventory],incremental:[],reports:[],resultHistory:attempts});
assert.equal(node('[data-recovery-count="results"]').textContent,0);
console.log('PASS: newest complete inventory wins and later publications stay visible');
selection.machine='PC071';selection.account='3-3';
const dated = ['2026-09-21T23:59:00+08:00','2026-09-22T00:00:00+08:00','2026-09-23T12:00:00+08:00'].map((day,i)=>({
 ...recovered,machine_id:'PC071',account_slot:'3-3',task_id:'date-'+i,
 business_result:{published:true,note_id:'date-'+i,title:'date-'+i,published_at:day}}));
view.render({results:dated,incremental:[],reports:[],resultHistory:[]});
assert.equal(cards().length,2);
assert.ok(!JSON.stringify(cards()).includes('date-0'));
console.log('PASS: PC071 3-3 includes September 22 boundary and excludes earlier works');
