const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

process.env.TZ = "Asia/Shanghai";
const source = fs.readFileSync(path.join(__dirname, "../webui/manager-console.js"), "utf8");
function extract(name) {
  const start = source.indexOf(`  function ${name}(`);
  assert.ok(start >= 0, name);
  const end = source.indexOf("\n  function ", start + 1);
  return source.slice(start, end);
}
const controls = {};
const collectionContext = vm.createContext({
  state: { sync: { results: { interval_seconds: 60 } } },
});
vm.runInContext(extract("collectionStatus"), collectionContext);
assert.equal(collectionContext.collectionStatus({result_sync_status: "IDLE",
  outbox_collection: {observed_at: new Date().toISOString()}}).status, "IDLE");
assert.equal(collectionContext.collectionStatus({result_sync_status: "SUCCESS",
  outbox_collection: {observed_at: "2020-01-01T00:00:00Z"}}).status, "UNKNOWN");
assert.equal(collectionContext.collectionStatus({result_sync_status: "OFFLINE",
  outbox_collection: {pending_records: 0, observed_at: new Date().toISOString()}}).cached, true);
assert.equal(collectionContext.collectionStatus({}).status, "UNKNOWN");
const collectionNodes = {};
Object.assign(collectionContext, {
  $: (selector) => collectionNodes[selector] ||= {replaceChildren(...items) {this.items = items;}},
  setText: () => {}, formatTime: (value) => value || "—",
  element: (tag, cls, text) => ({tag, text, children: [], appendChild(child) {this.children.push(child);}}),
  append: (parent, ...children) => children.forEach((child) => parent.appendChild(child)),
  stateBadge: (status) => ({status}), factList: (values) => ({values}),
});
collectionContext.state.machines = [{machine_id: "PC057", result_sync_status: "IDLE",
  outbox_collection: {observed_at: new Date().toISOString(), pending_records: 0,
    imported_records: 57, total_records: 57, batch_records: 0, batch_results: 0,
    batch_events: 0, batch_acknowledgements: 0}}];
vm.runInContext(extract("renderOutboxCollection"), collectionContext);
collectionContext.renderOutboxCollection();
const collectionCard = collectionNodes["#manager-collection-list"].items[0];
assert.equal(collectionCard.children[0].children[1].textContent, "已收齐");
assert.equal(collectionCard.children[1].values.find(([label]) => label === "待收回执")[1], "0");
const context = vm.createContext({ $: (selector) => controls[selector] });
for (const name of ["taskPrimaryState", "taskIsExecuting", "operationTaskMatchesFilters"]) {
  vm.runInContext(extract(name), context);
}
for (const [task, expected] of [
  [{ state: "PAUSED" }, false],
  [{ state: "PENDING_DISPATCH" }, false],
  [{ state: "DISPATCH_UNKNOWN" }, false],
  [{ state: "RUNNING" }, true],
  [{ dispatch_state: "ACCEPTED" }, true],
  [{ dispatch_state: "ACKNOWLEDGED" }, true],
  [{ dispatch_state: "PAUSE_REQUESTED" }, true],
  [{ business_state: "SUCCESS", state: "RUNNING" }, false],
]) assert.equal(context.taskIsExecuting(task), expected);

const task = {
  state: "COMPLETED", title: "浏览验收", created_at: "2026-09-06T17:00:00Z",
  targets: [{ machine_id: "QA-A", account_slot: "MixedCase" }],
};
for (const [key, value] of Object.entries({
  state: "COMPLETED", machine: "QA-A", account: "MixedCase", date: "2026-09-07",
  keyword: "浏览",
})) controls[`#manager-operation-filter-${key}`] = { value };
assert.equal(context.operationTaskMatchesFilters(task), true);
controls["#manager-operation-filter-date"].value = "2026-09-06";
assert.equal(context.operationTaskMatchesFilters(task), false);
controls["#manager-operation-filter-date"].value = "2026-09-07";
controls["#manager-operation-filter-account"].value = "Other";
assert.equal(context.operationTaskMatchesFilters(task), false);
console.log("PASS: execution states, receipt precedence, combined filters, local calendar date");

for (const name of ["targetNames", "goalMatchesFilters"]) {
  vm.runInContext(extract(name), context);
}
const goal = {state: "COMPLETED", title: "浏览验收", targets: task.targets,
  period_start: "2026-09-01", period_end: "2026-09-07"};
controls["#manager-operation-filter-account"].value = "MixedCase";
assert.equal(context.goalMatchesFilters(goal), true);
controls["#manager-operation-filter-date"].value = "2026-09-08";
assert.equal(context.goalMatchesFilters(goal), false);
controls["#manager-operation-filter-date"].value = "2026-09-01";
assert.equal(context.goalMatchesFilters(goal), true);
controls["#manager-operation-filter-date"].value = "2026-09-07";
const splitTargets = [{machine_id: "QA-A", account_slot: "Other"},
  {machine_id: "QA-B", account_slot: "MixedCase"}];
assert.equal(context.goalMatchesFilters({...goal, targets: splitTargets}), false);
assert.equal(context.operationTaskMatchesFilters({...task, targets: splitTargets}), false);
console.log("PASS: goal period boundaries and machine/account pair filtering");

const resultContext = vm.createContext({
  element: (tag, className, text = "") => ({
    tag, className, text, children: [],
    appendChild(child) { this.children.push(child); },
  }),
  append: (parent, ...children) => children.forEach((child) => parent.appendChild(child)),
  labelFor: (value) => value,
});
for (const name of ["numericCount", "resultDataStatus", "renderBusinessResult", "resultCompleteness"]) {
  vm.runInContext(extract(name), resultContext);
}
const collectedResult = {
  business_result_status: "observed",
  business_result: {
    count: 1,
    feeds: [{
      id: "note-1", displayTitle: "露营🏕️", user: { nickname: "小林" },
      interactInfo: { likedCount: "12" },
    }],
  },
  item_results_status: "observed",
  incremental_data_status: "observed",
};
const panel = resultContext.renderBusinessResult(collectedResult);
assert.equal(panel.tag, "details");
assert.equal(panel.children[0].text, "业务数据 · 1 条");
const feedCard = panel.children[1].children[0];
assert.equal(feedCard.children[0].text, "露营🏕️");
assert.equal(feedCard.children[1].text, "小林 · note-1");
assert.equal(feedCard.children[2].text, "赞 12");
assert.equal(
  resultContext.resultCompleteness(collectedResult),
  "业务 observed · 逐项 observed · 增量 observed",
);
assert.equal(
  resultContext.renderBusinessResult({
    business_result_status: "failed",
    business_result_missing_reason: "页面没有返回业务数据",
  }).text,
  "业务数据：页面没有返回业务数据",
);
console.log("PASS: collected business results, Chinese text, emoji and data completeness");

assert.equal(resultContext.renderBusinessResult({business_result: {count: null}})
  .children[0].text, "业务数据 · 数量未回传");
assert.equal(resultContext.renderBusinessResult({business_result: {feeds: []}})
  .children[0].text, "业务数据 · 0 条");
assert.equal(resultContext.renderBusinessResult({business_result: {
  feeds: [{interactInfo: {likedCount: 0}}],
}}).children[1].children[0].children[2].text, "赞 0");

const nodes = {};
const accountContext = vm.createContext({
  $: (selector) => nodes[selector] ||= {
    replaceChildren(...children) { this.children = children; },
  },
  state: {accounts: [{machine_id: "QA-A", account_slot: "only-account"}], alerts: []},
  selectedAccountNode: "QA-A",
  renderAccountNavigation() {}, setText() {}, accountHasActiveTask: () => false,
  emptyCard: (title) => ({title}),
  visibleAccounts: () => [{machine_id: "QA-A", account_slot: "only-account"}],
  compactAccountCard: (account, allowActions = true) => ({account, allowActions}),
});
vm.runInContext(extract("renderAccountWorkspace"), accountContext);
accountContext.renderAccountWorkspace();
assert.equal(nodes["#manager-remote-account-grid"].children[0].allowActions, true);

resultContext.compactTodayResultCard = () => ({
  children: [], appendChild(child) {this.children.push(child);},
});
resultContext.factList = (facts) => facts;
for (const name of ["resultItemValue", "resultItemDetails", "operationTaskResultCard"]) {
  vm.runInContext(extract(name), resultContext);
}
const detailCard = resultContext.operationTaskResultCard({
  item_results: [{title: "逐篇结果", browse: {opened: true, duration_seconds: 5}}],
});
assert.equal(detailCard.children[0].children[0].text, "查看逐项结果（1）");
assert.equal(detailCard.children[0].children[1].children[0].children[0].text, "逐篇结果");
const workerItem = resultContext.resultItemDetails({item_results: [{
  title: "实机浏览回执", browse: {status: "success"}, read_seconds: 9.83,
}]}).children[1].children[0].children[1];
assert.equal(workerItem.find(([label]) => label === "打开详情")[1], "SUCCESS");
assert.equal(workerItem.find(([label]) => label === "阅读时长")[1], 9.83);
console.log("PASS: first account actions, task item details, missing counts and observed zero");

context.state = {tasks: [{machine_id: "QA-A", request: {account_slot: "only-account"}, state: "PAUSED"}]};
vm.runInContext(extract("accountHasActiveTask"), context);
assert.equal(context.accountHasActiveTask({machine_id: "QA-A", account_slot: "only-account"}), false);
context.state.tasks[0].state = "RUNNING";
assert.equal(context.accountHasActiveTask({machine_id: "QA-A", account_slot: "only-account"}), true);
console.log("PASS: account execution filter follows current execution state");

resultContext.taskForResult = () => ({});
for (const name of ["resultRequest", "resultCapability", "resultActionKind", "requestedResultCount",
  "actualResultCount", "targetActualText", "actionTargetActualText"]) {
  vm.runInContext(extract(name), resultContext);
}
assert.equal(resultContext.actualResultCount({item_results: [{id: "discovery"}]}), null);
assert.equal(resultContext.actualResultCount({item_results: []}), 0);
const engagement = [{capability: "home-engagement",
  task_request: {parameters: {browse_count: 5, like_count: 2}},
  item_results: [{browse: {status: "success"}, like: {success: true}},
    {browse: {status: "success"}, like: null}],
}];
assert.equal(resultContext.actionTargetActualText(engagement, "browse"), "5 / 2");
assert.equal(resultContext.actionTargetActualText(engagement, "like"), "2 / 1");
assert.equal(resultContext.actionTargetActualText(engagement, "comment"), "— / —");
console.log("PASS: discovery counts and separate engagement browse/like metrics");

vm.runInContext(extract("controlRoomDayMetrics"), resultContext);
const dayMetrics = resultContext.controlRoomDayMetrics(engagement);
assert.equal(dayMetrics.find((item) => item.kind === "browse").actual, "2");
assert.equal(dayMetrics.find((item) => item.kind === "like").actual, "1");
assert.equal(dayMetrics.find((item) => item.kind === "publish").actual, "—");
assert.equal(resultContext.controlRoomDayMetrics([{capability: "like-feed", actual_count: 0}])
  .find((item) => item.kind === "like").actual, "0");
console.log("PASS: dashboard daily metrics preserve observed zero and missing quantities");
