const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

process.env.TZ = "Asia/Shanghai";
const source = fs.readFileSync(path.join(__dirname, "../webui/manager-console.js"), "utf8");
const metricLabels = vm.runInNewContext(`(${source.match(/const metricLabels = (\{[\s\S]*?\});/)[1]})`);
assert.equal(metricLabels.friend_request, "关注博主");
assert.ok(source.includes('accountActionButton("启动并连接账号", "profile-start"'));
assert.ok(source.includes('"profile-start": "启动并连接账号"'));
const workerSource = fs.readFileSync(path.join(__dirname, "../webui/worker/worker.js"), "utf8");
assert.ok(workerSource.includes('"profile-start": "启动并连接账号"'));
function extract(name) {
  const start = source.indexOf(`  function ${name}(`);
  assert.ok(start >= 0, name);
  const end = source.indexOf("\n  function ", start + 1);
  return source.slice(start, end);
}
const controls = {};
const collectionContext = vm.createContext({
  state: { snapshot: {}, sync: { results: { interval_seconds: 60 } } },
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
    imported_records: 57, total_records: 57, imported_tasks: 4, total_tasks: 4,
    pending_tasks: 0, batch_records: 0, batch_results: 0,
    batch_events: 0, batch_acknowledgements: 0}}];
vm.runInContext(extract("renderOutboxCollection"), collectionContext);
collectionContext.renderOutboxCollection();
const collectionCard = collectionNodes["#manager-collection-list"].items[0];
assert.equal(collectionCard.children[0].children[1].textContent, "已收齐");
assert.equal(collectionCard.children[1].values.find(([label]) => label === "待收任务")[1], "0");
assert.equal(collectionCard.children[1].values.find(([label]) => label === "已导入 / 任务总数")[1], "4 / 4");
assert.equal(collectionCard.children[1].values.find(([label]) => label === "原始回执")[1], "57 / 57");
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
resultContext.state = {tasks: []};
for (const name of ["taskForResult", "resultRequest", "resultCapability", "resultActionKind",
  "numericCount", "resultDataStatus", "renderBusinessResult", "resultCompleteness"]) {
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

resultContext.state = {tasks: [], results: [], goals: [], accounts: []};
resultContext.metricLabels = {browse: "浏览", like: "点赞", comment: "评论",
  reply: "回复", publish: "发布"};
for (const name of ["taskForResult", "taskPrimaryState", "localDateValue", "resultDate",
  "resultAccount", "workerMetric", "workerTaskDate", "workerDayMetrics", "workerDayAccounts"]) {
  vm.runInContext(extract(name), resultContext);
}
const dailyRequest = {account_slot: "account-a", goal_id: "daily-goal",
  capability: "home-engagement", parameters: {browse_count: 5, like_count: 2}};
resultContext.state.tasks = [{task_id: "daily-task", machine_id: "QA-A",
  created_at: "2026-09-06T17:00:00Z", request: dailyRequest}];
resultContext.state.goals = [{goal_id: "daily-goal", state: "ACTIVE",
  period_start: "2026-09-07", period_end: "2026-09-07",
  targets: [{machine_id: "QA-A", account_slot: "account-a"}], target_metrics: {browse: 5}}];
resultContext.state.results = [{...engagement[0], task_id: "daily-task", machine_id: "QA-A",
  recorded_at: "2026-09-07T02:00:00Z", task_request: dailyRequest}];
const workerMetric = (kind) => resultContext.workerDayMetrics("QA-A", "account-a", "2026-09-07")
  .find((item) => item.kind === kind);
assert.equal(workerMetric("browse").target, 5);
assert.equal(workerMetric("browse").actual, 2);
assert.equal(workerMetric("browse").status, "进行中 · 差 3");
assert.equal(workerMetric("like").target, 2);
assert.equal(workerMetric("like").actual, 1);
assert.equal(resultContext.workerDayMetrics("QA-A", "account-b", "2026-09-07")[0].actual, null);
assert.equal(resultContext.workerDayMetrics("QA-B", "account-a", "2026-09-07")[0].target, null);
assert.equal(resultContext.workerDayMetrics("QA-A", "account-a", "2026-09-06")[0].target, null);
assert.deepEqual(Array.from(resultContext.workerDayAccounts("QA-A", "2026-09-07")), ["account-a"]);

// A receipt without item counts keeps the total visibly incomplete.
resultContext.state.results.push({task_id: "pending-receipt", machine_id: "QA-A",
  recorded_at: "2026-09-07T03:00:00Z", task_request: dailyRequest});
assert.equal(workerMetric("browse").target, 5);
assert.equal(workerMetric("browse").actual, 2);
assert.equal(workerMetric("browse").incomplete, true);
assert.equal(workerMetric("browse").status, "等待回执");
resultContext.state.results = [{task_id: "daily-task", machine_id: "QA-A",
  recorded_at: "2026-09-07T03:00:00Z", task_request: dailyRequest, item_results: []}];
assert.equal(workerMetric("browse").actual, 0);
assert.equal(workerMetric("browse").status, "未开始 · 差 5");
resultContext.state.results[0].item_results = undefined;
assert.equal(workerMetric("browse").actual, null);
assert.equal(workerMetric("browse").status, "等待回执");

resultContext.state.goals[0].period_start = "2026-09-01";
resultContext.state.goals[0].target_metrics.browse = 100;
assert.equal(workerMetric("browse").target, 5);
for (const capability of ["collect-private-messages", "collect-note-comments", "prepare-private-messages",
  "generate-reply-draft", "fill-publish", "list-feeds"]) {
  for (const kind of Object.keys(resultContext.metricLabels)) {
    assert.equal(resultContext.workerMetric({capability, target_count: 20, actual_count: 20}, kind)
      .actual, null);
  }
}
console.log("PASS: worker daily targets, account/date isolation, receipt completeness and history");

resultContext.state.employeeSummary = {daily_metrics: [{
  machine_id: "QA-A", account_slot: "account-a", date: "2026-09-07", metrics: {
    browse: {kind: "browse", target: null, actual: null, status: "执行结果待核对",
      tone: "warning", incomplete: false},
  },
}]};
assert.equal(workerMetric("browse").target, 5);
assert.equal(workerMetric("browse").status, "执行结果待核对");
assert.equal(workerMetric("browse").incomplete, false);
delete resultContext.state.employeeSummary;

vm.runInContext(extract("nextWorkerPlan"), resultContext);
const dailyRow = {machine_id: "QA-A", account_slot: "account-a", participating: true,
  metrics: {browse: 30, like: 6, comment: 3, reply: 0, publish: 1}};
resultContext.state.dailyPlans = [
  {plan_date: "2026-09-10", accounts: [dailyRow]},
  {plan_date: "2026-09-08", accounts: [{...dailyRow, participating: false}]},
  {plan_date: "2026-09-09", accounts: [dailyRow]},
  {plan_date: "2026-09-07", accounts: [dailyRow]},
];
assert.equal(resultContext.nextWorkerPlan("QA-A", "2026-09-07").plan_date, "2026-09-09");
assert.equal(resultContext.nextWorkerPlan("QA-B", "2026-09-07"), undefined);
assert.equal(workerMetric("browse").target, 30);
assert.deepEqual(Array.from(resultContext.workerDayAccounts("QA-A", "2026-09-09")), ["account-a"]);
console.log("PASS: next operating day selects the nearest participating plan and uses saved quantities");

async function checkPersonaEditor() {
  const fields = Object.fromEntries(["display_name", "role", "audience", "language", "tone",
    "identity_background", "professional_direction", "reply_length", "behavior_boundaries",
    "interaction_style", "fact_boundaries", "knowledge", "status", "content_topics"]
    .map((field) => [field, {value: ""}]));
  const submit = {disabled: false};
  const form = {reset() {}, elements: {namedItem: (field) => fields[field]},
    querySelector: () => submit};
  const dialog = {open: false, showModal() {this.open = true;}, close() {this.open = false;}};
  const messages = {};
  let saved;
  const editor = vm.createContext({
    editingPersona: null, expandedPersonas: new Set(), managerApi: "/api/v1/manager",
    state: {personas: [{persona_id: "persona-a", machine_id: "QA-A", account_slot: "account-a",
      display_name: "自然浏览者", version: 3, content_topics: ["生活", "阅读"]}]},
    $: (selector) => selector.endsWith("-form") ? form : dialog,
    setText() {}, setMessage: (selector, message) => {messages[selector] = message;},
    renderAccountWorkspace() {},
    mutateJson: async (url, method, body) => {
      saved = {url, method, body};
      return {persona: {...body, version: 4}};
    },
  });
  // Include the async save handler following the editor's opening function.
  vm.runInContext(extract("openPersonaEditor"), editor);
  editor.openPersonaEditor({machine_id: "QA-A", account_slot: "account-a"});
  assert.equal(dialog.open, true);
  assert.equal(fields.display_name.value, "自然浏览者");
  assert.equal(fields.content_topics.value, "生活、阅读");
  fields.display_name.value = "  阅读分享者  ";
  fields.content_topics.value = "生活、阅读，旅行\n摄影";
  await editor.savePersonaEditor({preventDefault() {}, currentTarget: form});
  assert.equal(saved.url, "/api/v1/manager/personas");
  assert.equal(saved.method, "POST");
  assert.equal(saved.body.machine_id, "QA-A");
  assert.equal(saved.body.account_slot, "account-a");
  assert.equal(saved.body.persona_id, "persona-a");
  assert.equal(saved.body.display_name, "阅读分享者");
  assert.ok(Object.hasOwn(saved.body, "reply_length"));
  assert.ok(Object.hasOwn(saved.body, "behavior_boundaries"));
  assert.deepEqual(Array.from(saved.body.content_topics), ["生活", "阅读", "旅行", "摄影"]);
  assert.equal(editor.state.personas.length, 1);
  assert.equal(editor.state.personas[0].version, 4);
  assert.equal(dialog.open, false);
  assert.equal(submit.disabled, false);
  editor.openPersonaEditor({machine_id: "QA-A", account_slot: "account-a"});
  fields.role.value = "编辑中的内容";
  editor.mutateJson = async () => {throw new Error("保存失败，请重试");};
  await editor.savePersonaEditor({preventDefault() {}, currentTarget: form});
  assert.equal(dialog.open, true);
  assert.equal(fields.role.value, "编辑中的内容");
  assert.equal(submit.disabled, false);
  assert.equal(messages["#manager-persona-editor-message"], "保存失败，请重试");
  console.log("PASS: account-bound persona editing, version refresh and retained drafts on failure");
}
checkPersonaEditor().catch((error) => {console.error(error); process.exitCode = 1;});

const machineNodes = {};
const navigationFrames = [];
const machineContext = vm.createContext({
  state: {available: true, machines: [{machine_id: "QA-A", status: "READY"}, {machine_id: "QA-B"}],
    accounts: [{machine_id: "QA-A", account_slot: "first"},
      {machine_id: "QA-A", account_slot: "second"}, {machine_id: "QA-B", account_slot: "first"}]},
  selectedEmployeeAccount: "",
  selectedMachineId: "QA-A", selectedView: "workers", VIEW_STORAGE_KEY: "view",
  $: (selector) => machineNodes[selector] ||= {
    dataset: {}, classList: {toggle() {}}, setAttribute() {},
    replaceChildren(...items) {this.items = items;},
  },
  setText() {}, populateMachineSelects() {},
  localStorage: {setItem() {}},
  document: {querySelectorAll: () => []},
  window: {scrollY: 400, location: {hash: "#workers"}, history: {replaceState() {}},
    requestAnimationFrame(callback) {navigationFrames.push(callback);},
    scrollTo({top}) {this.scrollY = top;}},
  accountUnitNavButton: (account) => `${account.machine_id}:${account.account_slot}`,
  accountThumbnailCard: (account) => `${account.machine_id}:${account.account_slot}`,
  machineThumbnailCard: (machine) => machine.machine_id,
  renderMachineDetail: (machine) => {machineNodes.detailId = machine.machine_id;},
  emptyCard: (title) => title,
});
for (const name of ["employeeAccountKey", "renderMachineNavigation", "renderMachineStage", "selectMachine",
  "renderMachines", "selectManagerView"]) vm.runInContext(extract(name), machineContext);
machineContext.selectMachine("");
assert.equal(machineContext.selectedView, "workers");
assert.equal(machineContext.selectedMachineId, "");
assert.equal(machineNodes["#manager-machine-detail"].hidden, true);
assert.equal(machineNodes["#manager-all-machine-list"].items.join(","), "QA-A,QA-B");
machineContext.renderMachines();
assert.equal(machineNodes["#manager-machine-list"].items.join(","), "QA-A:first,QA-A:second,QA-B:first");
machineContext.selectManagerView("workers");
assert.equal(machineContext.selectedMachineId, "");
assert.equal(machineNodes[".control-room"].dataset.machineScope, "all");
machineContext.selectMachine("QA-B");
assert.equal(machineNodes["#manager-cluster-overview-panel"].hidden, true);
assert.equal(machineNodes["#manager-machine-detail"].hidden, false);
assert.equal(machineNodes.detailId, "QA-B");
machineContext.selectMachine("");
machineContext.state.machines = [];
machineContext.renderMachines();
assert.equal(machineNodes["#manager-machine-detail"].hidden, true);
assert.equal(machineNodes["#manager-all-machine-list"].items[0], "等待员工机同步");
console.log("PASS: all-machine cards, single-machine detail and selection retained after refresh");
machineContext.selectedEmployeeAccount = "";
machineContext.employeePanel = {render(id) {machineNodes.employeeId = id;}};
machineContext.state.machines = [{machine_id: "QA-A"}, {machine_id: "QA-B"}];
machineContext.selectedMachineId = "QA-A";
vm.runInContext(extract("selectEmployeeAccount"), machineContext);
machineContext.selectEmployeeAccount("QA-A:second");
assert.equal(machineContext.selectedView, "employee-units");
assert.equal(machineNodes.employeeId, "QA-A:second");
assert.equal(machineContext.selectedMachineId, "QA-A");
assert.equal(machineNodes["#manager-machine-nav"].items.join(","), "QA-A:first,QA-A:second,QA-B:first");
machineContext.selectEmployeeAccount("QA-B:first");
assert.equal(machineNodes.employeeId, "QA-B:first");
machineContext.state.accounts.pop();
machineContext.state.available = false;
machineContext.renderMachineNavigation();
assert.equal(machineContext.selectedEmployeeAccount, "QA-B:first");
machineContext.state.available = true;
machineContext.renderMachineNavigation();
assert.equal(machineContext.selectedEmployeeAccount, "");
machineContext.selectEmployeeAccount("");
assert.equal(machineNodes.employeeId, "");
machineContext.selectManagerView("workers");
assert.equal(machineContext.selectedMachineId, "QA-A");
console.log("PASS: account unit navigation and removal keep the workspace machine selection independent");

navigationFrames.length = 0;
for (const navigate of [
  () => machineContext.selectEmployeeAccount("QA-A:first"),
  () => machineContext.selectEmployeeAccount("QA-A:second"),
  () => machineContext.selectManagerView("settings"),
  () => machineContext.selectManagerView("employee-units"),
  () => machineContext.selectMachine("QA-A"),
]) {
  machineContext.window.scrollY = 780;
  navigate();
  machineContext.window.scrollY = 0;
  navigationFrames.splice(0).forEach((callback) => callback());
  assert.equal(machineContext.window.scrollY, 780);
}
console.log("PASS: account, machine and workspace navigation retain the reading position");

const cardContext = vm.createContext({
  state: {machines: [{machine_id: "QA-A", status: "READY", ssh_state: "ONLINE"}]},
  element: (tag, className = "", textContent = "") => ({tag, className, textContent,
    dataset: {}, children: [], appendChild(child) {this.children.push(child);},
    setAttribute(name, value) {this[name] = value;}}),
  append: (parent, ...children) => {children.forEach((child) => parent.appendChild(child)); return parent;},
  accountPersona: (account) => ({display_name: account.persona_name}),
  stateBadge: (status) => ({textContent: status, children: []}),
  labelFor: (value) => value, toneFor: (value) => value, formatTime: (value) => value || "—",
});
for (const name of ["employeeAccountKey", "accountThumbnailCard"]) {
  vm.runInContext(extract(name), cardContext);
}
const cardText = (node) => [node.textContent, ...node.children.map(cardText)].join(" ");
const firstCard = cardContext.accountThumbnailCard({machine_id: "QA-A", account_slot: "first",
  nickname: "First account", current_uid: "uid-first", persona_name: "First persona", health_state: "READY"});
const secondCard = cardContext.accountThumbnailCard({machine_id: "QA-A", account_slot: "second",
  nickname: "Second account", current_uid: "uid-second", persona_name: "Second persona",
  health_state: "BLOCKED", platform: "rednote"});
assert.equal(firstCard.dataset.employeeAccountSelect, "QA-A:first");
assert.equal(secondCard.dataset.employeeAccountSelect, "QA-A:second");
assert.equal(secondCard.dataset.tone, "BLOCKED");
assert.ok(cardText(firstCard).includes("First account"));
assert.ok(!cardText(firstCard).includes("Second"));
assert.ok(cardText(secondCard).includes("RedNote"));
assert.ok(cardText(secondCard).includes("uid-second"));
assert.ok(cardText(secondCard).includes("Second persona"));
assert.ok(!cardText(secondCard).includes("First"));
cardContext.state.machines = [];
assert.ok(cardText(cardContext.accountThumbnailCard({machine_id: "QA-A", account_slot: "cached"}))
  .includes("cached"));
console.log("PASS: overview account cards keep same-machine identity, persona and health separate");
