const { webuiAssetPath } = require("./project_paths.cjs");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(webuiAssetPath("employee-panel.js"), "utf8");
const root = {innerHTML: "", querySelectorAll: () => [], contains: () => false,
  querySelector: () => null};
const saved = new Map();
const context = vm.createContext({window: {}, document: {querySelector: () => root, activeElement: {}},
  localStorage: {getItem: (key) => saved.get(key), setItem: (key, value) => saved.set(key, value)},
  structuredClone, Date, Intl});
vm.runInContext(source, context);
const today = new Date().toISOString().slice(0, 10);
const yesterdayDate = new Date(); yesterdayDate.setDate(yesterdayDate.getDate() - 1);
const yesterday = yesterdayDate.toISOString().slice(0, 10);
const tomorrow = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
saved.set("auto-xhs:employee-plan-templates", JSON.stringify({[tomorrow]: {
  rows: [{machine_id: "QA-A", account_slot: "same-slot", participating: true,
    metrics: {publish: 3, like: 4, comment: 2, group_apply: 50, group_intro: 70}}],
  updatedAt: today,
}}));
const state = {machines: [{machine_id: "QA-A", status: "READY", ssh_state: "ONLINE"},
  {machine_id: "QA-B", status: "OFFLINE"}],
  accounts: [{machine_id: "QA-A", account_slot: "same-slot", nickname: "<script>name</script>",
    current_uid: "uid-a", health_state: "READY", friend_events: [
      {event_type: "new_follower", actor_name: "新关注读者", handling_state: "NEW"},
      {event_type: "follow_user", actor_name: "主动关注对象"}]},
  {machine_id: "QA-B", account_slot: "same-slot", nickname: "Second account", health_state: "READY"}],
  dailyPlans: [{plan_date: yesterday, accounts: [{machine_id: "QA-A", account_slot: "same-slot", participating: true}]}],
  tasks: [], goals: [], results: [{machine_id: "QA-A", account_slot: "same-slot", date: today,
    result_id: "receipt-a", item_results: [{state: "SUCCESS", comment_text: "<b>text</b>", url: "https://example.com/note"}]}]};
const apiCalls = [];
const services = {state, managerApi: "/api/v1/manager",
  mutateJson: async (url, method, body) => {
    apiCalls.push({url, method, body});
    return {plan: {...body, version: 1, updated_at: new Date().toISOString()}};
  },
  workerDayMetrics: () => [{kind: "publish", target: 1, actual: 0, status: "未开始", tone: "info"},
    {kind: "comment", target: 2, actual: null, status: "等待回执", tone: "info"}],
  nextWorkerPlan: (machineId) => state.dailyPlans.find((p) => p.plan_date === tomorrow) || (machineId === "QA-A" ? {plan_date: tomorrow, accounts: [
    {machine_id: "QA-A", account_slot: "same-slot", participating: true, metrics: {publish: 3}},
  ]} : null), localDateValue: (date) => date.toISOString().slice(0, 10),
  resultDate: (item) => item.date, resultAccount: (item) => item.account_slot,
  resultLinks: (item) => item.url ? [{url: item.url}] : [], resultActionKind: () => "comment",
  workerTaskDate: (task) => task.date, accountPersona: () => null, labelFor: (value) => value || "—",
  operationalExceptions: () => []};
const panel = context.window.AutoXhsEmployeePanel(services);
panel.render("");
assert.ok(!root.innerHTML.includes("明日任务规划"));
assert.ok(!root.innerHTML.includes("data-plan-host"));
for (const section of ["全部账号", "所属机器", "账号单元",
  "运营日报", "实际动作汇总", "告警与提醒"]) {
  assert.ok(root.innerHTML.includes(section), section);
}
assert.equal((root.innerHTML.match(/class="overview-node-card /g) || []).length, 2);
assert.ok(!root.innerHTML.includes('data-panel="employee-profile"'));
panel.render("QA-A:same-slot");
state.machines[0].status = "PAUSED";
panel.render("QA-A:same-slot");
assert.ok(root.innerHTML.includes('<div class="machine-live"><i class="signal tone-good"></i>'));
state.machines[0].status = "READY";
for (const section of ["EMPLOYEE / PROFILE", "今日目标 / 实际", "往日目标 / 实际",
  "执行记录", "收到的评论与回复", "新增关注明细", "收到的私信与回复"]) {
  assert.ok(root.innerHTML.includes(section), section);
}
const followerDetails = root.innerHTML.split('data-panel="friend-details"')[1].split("</details>")[0];
assert.ok(followerDetails.includes("新关注读者"));
assert.ok(followerDetails.includes("已采集"));
assert.ok(!followerDetails.includes("主动关注对象"));
assert.ok(!followerDetails.includes("待处理"));
assert.ok(root.innerHTML.includes("<h2>same-slot</h2>"));
assert.ok(!root.innerHTML.includes("&lt;script&gt;name&lt;/script&gt;"));
assert.ok(root.innerHTML.includes("&lt;b&gt;text&lt;/b&gt;"));
assert.ok(!root.innerHTML.includes("Second account"));
const metrics = panel.dailyMetrics(state.accounts[0], today);
assert.equal(metrics.find((item) => item.kind === "friend_request").label, "关注博主");
for (const section of ["today-targets", "history"]) {
  const html = root.innerHTML.split(`data-panel="${section}"`)[1].split("</table>")[0];
  assert.ok(html.includes("关注博主"), section);
}
const followPanel = context.window.AutoXhsEmployeePanel({...services,
  state: {...state, results: state.results.map((result) => ({...result, capability: "follow-user"}))},
  resultActionKind: () => "friend_request"});
assert.equal(followPanel.actions(state.accounts[0], today)[0].label, "关注博主");
assert.equal(metrics.find((item) => item.kind === "publish").actual, 0);
assert.equal(metrics.find((item) => item.kind === "comment").actual, null);
assert.equal(metrics.find((item) => item.kind === "new_followers").status, "等待采集");
assert.equal(panel.actions(state.accounts[1], today).length, 0);
panel.render("QA-B:same-slot");
assert.ok(root.innerHTML.includes("当前快照没有收到的私信与回复。"));
assert.ok(!root.innerHTML.includes("https://example.com/note"));
console.log("PASS: employee templates, all/detail views, account isolation and missing metrics");

state.employeeSummary = {daily_metrics: [{machine_id: "QA-A", account_slot: "same-slot", date: today,
  metrics: {new_followers: {target: 3, actual: null, observed_new: 1,
    actual_display: "至少 1", status: "当日总量待确认", incomplete: true,
    metric_semantics: "daily_event_count", current_total: 8,
    sampled_net_change: {value: -2, from: yesterday, to: today}}}}],
  daily_report: {date: yesterday, rows: [{machine_id: "QA-A", account_slot: "same-slot",
    pressure: "高压", external_actions: 1, completion_rate: 0.5, status: "已回收"}],
    groups: [{label: "高压组", account_count: 1, analyzed_count: 1, actual: 2,
      completion_rate: 0.5, exception_rate: 0}], conclusion: "高压组平均完成率 50%",
    planned_machines: 1, executed_machines: 1, target: 4, actual: 2, exception_count: 0}};
panel.render("");
assert.ok(root.innerHTML.includes("高压组平均完成率 50%"));
assert.match(root.innerHTML, /<th>关注博主<\/th>/);
assert.ok(root.innerHTML.includes("纳入对比 1/1"));
panel.render("QA-A:same-slot");
assert.ok(root.innerHTML.includes("至少 1"));
assert.ok(root.innerHTML.includes("采样净变化：-2"));
assert.ok(root.innerHTML.includes("截至采集时总量：8"));
assert.equal(panel.dailyMetrics(state.accounts[0], today).find((m) => m.kind === "new_followers").actual, null);
assert.equal(panel.dailyMetrics(state.accounts[1], today).find((m) => m.kind === "new_followers").actual, null);
state.employeeSummary = {};
console.log("PASS: server daily report and account-scoped growth metrics");

async function checkAttribution() {
  const handlers = {};
  root.addEventListener = (event, handler) => {handlers[event] = handler;};
  panel.init();
  const attributionMessage = {textContent: ""};
  const attributionSelect = {value: "human", dataset: {actionRecord: "result:QA-A:1", actionItem: "0", actionSource: "machine"},
    matches: (selector) => selector === "[data-action-record]",
    closest: () => ({querySelector: () => attributionMessage})};
  await handlers.change({target: attributionSelect});
  assert.equal(apiCalls.at(-1).url, "/api/v1/manager/results/result%3AQA-A%3A1/attribution");
  assert.equal(apiCalls.at(-1).body.execution_source, "human");
  assert.equal(attributionSelect.dataset.actionSource, "human");
}
function checkAccountUnits() {
  const later = new Date(Date.now() + 3 * 86400000).toISOString().slice(0, 10);
  const isolated = {...state, employeeSummary: {}, tasks: [], goals: [], accounts: [
    {machine_id: "QA-A", account_slot: "first", nickname: "First account", health_state: "READY",
      comment_reply_alerts: [{actor_name: "First commenter", reply_text: "First comment"}],
      private_message_events: [{peer_name: "First sender", message_preview: "First inbox"}]},
    {machine_id: "QA-A", account_slot: "second", nickname: "Second same-machine account",
      health_state: "BLOCKED", platform: "rednote", current_uid: "uid-second",
      comment_reply_alerts: [{actor_name: "Second commenter", reply_text: "Second comment"}],
      private_message_events: [{peer_name: "Second sender", message_preview: "Second inbox"}]},
    {machine_id: "QA-B", account_slot: "first", nickname: "Other machine account", health_state: "READY"},
  ], dailyPlans: [
    {plan_date: later, accounts: [{machine_id: "QA-A", account_slot: "second", participating: true,
      metrics: {publish: 8}, notes: "Second account plan"}]},
    {plan_date: tomorrow, accounts: [{machine_id: "QA-A", account_slot: "first", participating: true,
      metrics: {publish: 3}, notes: "First account plan"}]},
  ], results: [
    {machine_id: "QA-A", account_slot: "first", date: today, summary: "First action", url: "https://example.com/first"},
    {machine_id: "QA-A", account_slot: "second", date: today, summary: "Second action", url: "https://example.com/second"},
    {machine_id: "QA-B", account_slot: "first", date: yesterday, summary: "Other machine history"},
  ]};
  const units = context.window.AutoXhsEmployeePanel({...services, state: isolated,
    workerDayMetrics: (machine, slot) => [{kind: "comment", target: 10,
      actual: machine === "QA-A" ? (slot === "first" ? 1 : 9) : 0, status: "进行中"}]});
  units.render("");
  assert.equal((root.innerHTML.match(/class="overview-node-card /g) || []).length, 3);
  for (const account of isolated.accounts) {
    assert.ok(root.innerHTML.includes(`data-employee-account-select="${account.machine_id}:${account.account_slot}"`));
  }
  assert.match(root.innerHTML, /账号单元<\/span><strong>3<\/strong>/);
  assert.match(root.innerHTML, /所属机器<\/span><strong>2<\/strong>/);
  assert.ok(root.innerHTML.includes("今日运营规划"));
  assert.ok(!root.innerHTML.includes("First account plan"));
  assert.ok(!root.innerHTML.includes("Second account plan"));
  isolated.dailyPlans.push({plan_date: today, accounts: [
    {machine_id: "QA-A", account_slot: "first", participating: true, metrics: {publish: 0}, notes: "Today first"},
    {machine_id: "QA-A", account_slot: "second", participating: true, metrics: {publish: 6}, notes: "Today second"},
  ]});
  units.render("");
  const cards = [...root.innerHTML.matchAll(/data-today-plan-account="([^"]+)"[\s\S]*?<\/article>/g)];
  assert.equal(cards.length, 3);
  assert.ok(cards[0][0].includes('<strong>0 / '));
  assert.ok(cards[1][0].includes('<strong>6 / '));
  assert.ok(!cards[2][0].includes("Today first"));
  assert.ok(!cards[0][0].includes("Today second"));
  assert.ok(!root.innerHTML.includes("Second account plan"));
  for (const [slot, expected, excluded, date, publish] of [
    ["first", "First", "Second", tomorrow, 3], ["second", "Second", "First", later, 8],
  ]) {
    units.render(`QA-A:${slot}`);
    assert.equal((root.innerHTML.match(/data-panel="employee-profile"/g) || []).length, 1);
    for (const item of ["comment", "inbox", "action"]) {
      assert.ok(root.innerHTML.includes(`${expected} ${item}`), `${slot}: ${item}`);
      assert.ok(!root.innerHTML.includes(`${excluded} ${item}`), `${slot}: isolated ${item}`);
    }
    assert.ok(!root.innerHTML.includes("Other machine history"));
    assert.ok(!root.innerHTML.includes('data-panel="next-day"'));
    assert.ok(!root.innerHTML.includes('data-panel="today-planning"'));
  }
  assert.ok(root.innerHTML.includes("https://www.rednote.com/user/profile/uid-second"));
  units.render("QA-B:first");
  assert.ok(!root.innerHTML.includes("下一个运营日"));
  assert.ok(!root.innerHTML.includes("First account plan"));
  isolated.machines = [];
  units.render("QA-A:second");
  assert.ok(root.innerHTML.includes("<h2>second</h2>"));
  assert.ok(root.innerHTML.includes("Second inbox"));
  units.render("QA-A:deleted");
  assert.ok(root.innerHTML.includes("全部账号"));
  console.log("PASS: same-machine account cards, future plans, messages and cached details stay isolated");
}
checkAttribution().then(checkAccountUnits).catch((error) => {console.error(error); process.exitCode = 1;});

state.accounts[0].private_message_events = [{event_type: "private_message", handling_state: "REPLIED",
  occurred_at: "2026-09-15T01:00:00Z", message_preview: "入站原文<测试>",
  processing_rule_basis: "applied", processing_rule: {rule_version: 2, enabled: 1, private_message_enabled: 1, reply_style: "natural"},
  reply_history: [{reply_text: "回复原文<测试>", sent_at: "2026-09-15T01:01:00Z", result_state: "SUCCESS"}]}];
panel.render("QA-A:same-slot");
for (const text of ["收到的私信原文", "入站原文&lt;测试&gt;", "回复原文&lt;测试&gt;", "收到时间", "回复时间", "处理时规则", "版本 2"]) assert.ok(root.innerHTML.includes(text), text);

function checkPlanHandling() {
  const account = {machine_id: "QA-A", account_slot: "review", nickname: "Review account"};
  const fixture = {accounts: [account], machines: [], tasks: [], results: [], goals: [], dailyPlans: []};
  let metric = {kind: "comment", target: 2, actual: 2, incomplete: false};
  const view = context.window.AutoXhsEmployeePanel({...services, state: fixture,
    workerDayMetrics: () => [metric]});
  view.render("");
  assert.ok(root.innerHTML.includes("顺利完成"));
  assert.ok(root.innerHTML.includes('2 / <span title="">2</span>'));
  metric = {...metric, actual: 1};
  fixture.tasks = [{machine_id: "QA-A", request: {account_slot: "review"}, date: today,
    state: "BLOCKED", message: "需要身份验证", recommended_action: "在员工机完成身份验证"}];
  view.render("");
  assert.ok(root.innerHTML.includes("未完成 · 差 1"));
  assert.ok(root.innerHTML.includes("需要身份验证"));
  assert.ok(root.innerHTML.includes("在员工机完成身份验证"));
  fixture.tasks[0].request.account_slot = "another";
  metric = {...metric, actual: null};
  view.render("");
  assert.ok(!root.innerHTML.includes("需要身份验证"));
  assert.ok(root.innerHTML.includes("具体阻塞原因未回传"));
  assert.ok(root.innerHTML.includes("等待回执"));
  metric = {...metric, actual: 2, incomplete: true};
  view.render("");
  assert.ok(!root.innerHTML.includes("已确认的实际数量达到全部"));
  console.log("PASS: planned/actual quantities, completion, account-scoped blockers and missing receipts");
}
checkPlanHandling();

function checkOverviewMetricScope() {
  const passive = ["评论回复", "私信回复", "新增粉丝", "收获点赞", "收到新评论", "收到私信"];
  const scoped = context.window.AutoXhsEmployeePanel(services);
  scoped.render("");
  const planning = root.innerHTML.match(/data-panel="today-planning"[\s\S]*?<\/section>/)[0];
  for (const label of passive) assert.ok(!planning.includes(`<span>${label}</span>`), label);
  for (const label of ["浏览", "发帖", "点赞", "评论", "私信发送", "关注博主"]) {
    assert.ok(planning.includes(`<span>${label}</span>`), label);
  }
  scoped.render("QA-A:same-slot");
  assert.ok(root.innerHTML.includes("收到的互动"));
  assert.ok(root.innerHTML.includes("收获点赞"));
  console.log("PASS: account interactions show notifications and messages separately");
}
checkOverviewMetricScope();

function checkPublishHistoryInRenderedProfile() {
  const fixture = {...state, results: [today, yesterday].map((date, i) => ({
    machine_id: "QA-A", account_slot: "same-slot", date,
    task_id: `publish-${i}`, capability: "publish", completed_at: `${date}T08:00:00Z`,
    item_results: [{title: `历史发布-${i}`, status: "SUCCESS", verified: true,
      note_url: `https://www.xiaohongshu.com/explore/history${i}`}],
  }))};
  const view = context.window.AutoXhsEmployeePanel({...services, state: fixture});
  view.render("QA-A:same-slot");
  assert.ok(root.innerHTML.includes("历史发布-0"));
  assert.ok(root.innerHTML.includes("历史发布-1"));
  assert.ok(root.innerHTML.includes("https://www.xiaohongshu.com/explore/history1"));
  assert.ok(root.innerHTML.includes("2 条记录 · 2 轮"));
  view.render("QA-B:same-slot");
  assert.ok(!root.innerHTML.includes("历史发布-1"));
  console.log("PASS: rendered account history includes yesterday and today with note links");
}
checkPublishHistoryInRenderedProfile();

function checkPassivePresentation() {
  const handlers={}; root.addEventListener=(name,fn)=>handlers[name]=fn;
  const fixture={...state, employeeSummary:{daily_metrics:[
    {machine_id:"QA-A",account_slot:"same-slot",date:today,metrics:{received_comments:{
      actual:null,observed_new:1,passive_view:{count_display:"至少 1",status:"部分数据待补齐",
        status_code:"partial",pending_count:1,replied_count:0,reason:"尚有时段未覆盖",
        missing_intervals:[{start:`${today}T12:00:00+08:00`,end:`${today}T13:00:00+08:00`}]}}}},
    {machine_id:"QA-A",account_slot:"same-slot",date:yesterday,metrics:{received_comments:{
      actual:0,coverage:"complete",passive_view:{count_display:"0",status:"当日统计完整",
        status_code:"complete",pending_count:0,replied_count:0}}}}],
    collection_records:[{machine_id:"QA-A",account_slot:"same-slot",statistics_date:today,
      event_type:"note_comment",actor_name:"读者甲",message_preview:"评论<正文>",handling_state:"NEW"},
      {machine_id:"QA-B",account_slot:"same-slot",statistics_date:today,event_type:"note_comment",
        message_preview:"另一个账号的私密正文"}]}};
  const view=context.window.AutoXhsEmployeePanel({...services,state:fixture});view.init();
  view.render("QA-A:same-slot");
  assert(root.innerHTML.indexOf('class="action-details"') < root.innerHTML.indexOf('data-panel="passive-interactions"'), 'received interactions follow execution records');
  let html=root.innerHTML.split('data-panel="passive-interactions"')[1].split('</section>')[0];
  assert.ok(html.includes("至少 1")); assert.ok(html.includes("待处理 1"));
  assert.ok(html.includes("待补齐时段"));assert.ok(html.includes("评论&lt;正文&gt;"));
  assert.ok(!html.includes("另一个账号的私密正文"));
  const target=root.innerHTML.split('data-panel="today-targets"')[1].split('data-panel="history"')[0];
  assert.ok(!target.includes("新增粉丝"));assert.ok(!target.includes("收到新评论"));
  handlers.change({target:{matches:selector=>selector==="[data-passive-date]",value:yesterday}});
  html=root.innerHTML.split('data-panel="passive-interactions"')[1].split('</section>')[0];
  assert.ok(html.includes("当日统计完整"));assert.ok(!html.includes("评论&lt;正文&gt;"));
  assert.ok(!html.includes('class="passive-count">至少 1'));
  console.log("PASS: passive count/status separation, missing intervals, date switching, content escaping and account isolation");
}
checkPassivePresentation();

function checkFleetOverview() {
  const handlers = {};
  root.addEventListener = (name, fn) => {handlers[name] = fn;};
  const account = {machine_id: "QA-A", account_slot: "review"};
  const fixture = {accounts: [account], machines: [], results: [], goals: [], dailyPlans: [],
    tasks: [{task_id: "blocked-task", machine_id: "QA-A", request: {account_slot: "review"},
      date: today, state: "BLOCKED", blocked_reason: "需要身份验证"},
    {task_id: "waiting-task", machine_id: "QA-B", request: {account_slot: "other"},
      date: today, state: "TASK_ACK"}]};
  const view = context.window.AutoXhsEmployeePanel({...services, state: fixture,
    workerDayMetrics: (machine, slot, date) => [
      {kind: "comment", actual: date === today ? 2 : 3}, {kind: "browse", actual: 50},
      {kind: "like", actual: 0}]});
  view.init(); view.render("");
  for (const name of ["fleet-actions", "operational-tasks", "daily-report", "actual-summary", "alerts"]) {
    assert.ok(root.innerHTML.includes(`data-panel="${name}"`), name);
  }
  assert.ok(root.innerHTML.includes('href="#workers"'));
  assert.ok(root.innerHTML.includes("当日总动作：2"));
  assert.ok(root.innerHTML.includes("今日计划复盘"));
  assert.ok(!root.innerHTML.includes("明日任务规划"));
  const click = (dataset) => handlers.click({target: {closest: () => ({dataset})}});
  click({overviewRange: "yesterday"});
  assert.ok(root.innerHTML.includes("昨日总动作：3"));
  click({overviewTask: "waiting"});
  let table = root.innerHTML.split('data-panel="operational-tasks"')[1].split("</section>")[0];
  assert.ok(table.includes("waiting-task"));
  assert.ok(!table.includes("blocked-task"));
  assert.ok(table.includes("等待回执"));
  click({overviewTask: "blocked"});
  table = root.innerHTML.split('data-panel="operational-tasks"')[1].split("</section>")[0];
  assert.ok(table.includes("需要身份验证"));
  assert.ok(!table.includes("waiting-task"));
  view.render("QA-A:review"); view.render("");
  assert.ok(root.innerHTML.includes("全部账号 · 运营总览"));
  assert.ok(!root.innerHTML.includes('data-panel="employee-profile"'));
  console.log("PASS: overview sections, external totals, date ranges, task filters and return navigation");
}
checkFleetOverview();
