const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../webui/employee-panel.js"), "utf8");
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
    current_uid: "uid-a", health_state: "READY"},
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
for (const section of ["账号运营总览", "所属机器", "账号单元",
  "运营日报", "明日任务规划", "保存计划", "实际动作汇总", "告警与提醒"]) {
  assert.ok(root.innerHTML.includes(section), section);
}
assert.equal((root.innerHTML.match(/class="overview-node-card /g) || []).length, 2);
assert.ok(!root.innerHTML.includes('data-panel="employee-profile"'));
panel.render("QA-A:same-slot");
for (const section of ["下一个运营日", "EMPLOYEE / PROFILE", "今日目标 / 实际", "往日目标 / 实际",
  "执行记录", "收到的评论与回复", "关注博主明细", "私聊明细"]) {
  assert.ok(root.innerHTML.includes(section), section);
}
assert.ok(root.innerHTML.includes("&lt;script&gt;name&lt;/script&gt;"));
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
assert.ok(root.innerHTML.includes("当前快照没有私聊明细。"));
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

async function checkPlanTemplate() {
  const handlers = {};
  const controls = {};
  root.addEventListener = (event, handler) => {handlers[event] = handler;};
  root.querySelector = (selector) => controls[selector] ||= {textContent: "", innerHTML: ""};
  panel.init();
  panel.render("");
  const rowNode = {dataset: {planRow: "QA-A:same-slot"}, classList: {toggle() {}},
    querySelector: (selector) => controls[selector] ||= {}};
  handlers.input({target: {value: "7", dataset: {planMetric: "comment"}, closest: () => rowNode}});
  assert.equal(controls['[data-plan-preview="comment"]'].textContent, "7");
  handlers.input({target: {value: "12", dataset: {planMetric: "new_followers"}, closest: () => rowNode}});
  assert.match(controls["[data-plan-summary]"].innerHTML, /计划动作上限<\/span><strong>14<\/strong>/);
  const click = (action) => handlers.click({target: {closest: () => ({dataset: {unitAction: action}})}});
  await click("save-plan");
  const stored = {rows: state.dailyPlans.find((p) => p.plan_date === tomorrow).accounts};
  assert.equal(apiCalls[0].url, "/api/v1/manager/daily-plans");
  assert.equal(apiCalls[0].method, "POST");
  assert.equal(stored.rows[0].metrics.comment, 7);
  assert.equal(stored.rows[0].metrics.new_followers, 12);
  assert.equal(stored.rows[0].metrics.group_apply, undefined);
  assert.equal(stored.rows[0].metrics.group_intro, undefined);
  assert.equal(stored.rows[1].metrics.comment, null);
  assert.equal(state.dailyPlans.length, 2);
  assert.match(controls["[data-plan-message]"].textContent, /计划已保存/);
  const reloaded = context.window.AutoXhsEmployeePanel(services);
  reloaded.render("");
  assert.ok(root.innerHTML.includes('data-plan-preview="comment">7</b>'));
  assert.ok(root.innerHTML.includes("复制模板"));
  for (const label of ["关注博主", "新增粉丝", "收获点赞", "收到新评论", "收到私信"]) {
    assert.ok(root.innerHTML.includes(label), label);
  }
  assert.ok(root.innerHTML.includes("新增粉丝：12"));
  assert.ok(root.innerHTML.includes("关注博主：未设置"));
  assert.ok(root.innerHTML.includes("关注博主按机会预算执行"));
  reloaded.render("QA-A:same-slot");
  const nextDay = root.innerHTML.match(/data-panel="next-day"[\s\S]*?<\/section>/)[0];
  assert.ok(nextDay.includes('<span>新增粉丝</span><strong>12</strong>'));
  for (const label of ["关注博主", "收获点赞", "收到新评论", "收到私信"]) assert.ok(nextDay.includes(label));
  assert.ok(!/入群|群介绍|申请进群|进群成功/.test(root.innerHTML));
  const attributionMessage = {textContent: ""};
  const attributionSelect = {value: "human", dataset: {actionRecord: "result:QA-A:1", actionItem: "0", actionSource: "machine"},
    matches: (selector) => selector === "[data-action-record]",
    closest: () => ({querySelector: () => attributionMessage})};
  await handlers.change({target: attributionSelect});
  assert.equal(apiCalls.at(-1).url, "/api/v1/manager/results/result%3AQA-A%3A1/attribution");
  assert.equal(apiCalls.at(-1).body.execution_source, "human");
  assert.equal(attributionSelect.dataset.actionSource, "human");
  handlers.input({target: {value: "9", dataset: {planMetric: "comment"}, closest: () => rowNode}});
  services.mutateJson = async () => {throw new Error("保存失败，请重试");};
  attributionSelect.value = "machine";
  await handlers.change({target: attributionSelect});
  assert.equal(attributionSelect.value, "human");
  await click("save-plan");
  assert.equal(state.dailyPlans.find((p) => p.plan_date === tomorrow).accounts[0].metrics.comment, 7);
  assert.equal(controls['[data-plan-preview="comment"]'].textContent, "9");
  assert.equal(controls["[data-plan-message]"].textContent, "保存失败，请重试");
  console.log("PASS: numeric draft preview and shared plan persistence");
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
  for (const [slot, expected, excluded, date, publish] of [
    ["first", "First", "Second", tomorrow, 3], ["second", "Second", "First", later, 8],
  ]) {
    units.render(`QA-A:${slot}`);
    assert.equal((root.innerHTML.match(/data-panel="employee-profile"/g) || []).length, 1);
    for (const item of ["comment", "inbox", "action", "account plan"]) {
      assert.ok(root.innerHTML.includes(`${expected} ${item}`), `${slot}: ${item}`);
      assert.ok(!root.innerHTML.includes(`${excluded} ${item}`), `${slot}: isolated ${item}`);
    }
    assert.ok(!root.innerHTML.includes("Other machine history"));
    const nextDay = root.innerHTML.match(/data-panel="next-day"[\s\S]*?<\/section>/)[0];
    assert.ok(nextDay.includes(date));
    assert.ok(nextDay.includes(`<span>发帖</span><strong>${publish}</strong>`));
    units.render(`QA-A:${slot}`);
    assert.ok(root.innerHTML.includes(`${expected} account plan`));
  }
  assert.ok(root.innerHTML.includes("https://www.rednote.com/user/profile/uid-second"));
  units.render("QA-B:first");
  assert.ok(root.innerHTML.includes("等待运营规划"));
  assert.ok(!root.innerHTML.includes("First account plan"));
  isolated.machines = [];
  units.render("QA-A:second");
  assert.ok(root.innerHTML.includes("Second same-machine account"));
  assert.ok(root.innerHTML.includes("Second inbox"));
  units.render("QA-A:deleted");
  assert.ok(root.innerHTML.includes("账号运营总览"));
  console.log("PASS: same-machine account cards, future plans, messages and cached details stay isolated");
}
async function checkDailySubmission() {
  const handlers = {}, controls = {}, calls = [];
  root.addEventListener = (name, handler) => {handlers[name] = handler;};
  root.querySelector = (selector) => controls[selector] ||= {textContent: "", innerHTML: "", value: ""};
  const row = {machine_id: "QA-A", account_slot: "same-slot", participating: true,
    metrics: {browse: 10, like: 1, comment: 1, reply: 0, publish: 0}};
  const isolated = {...state, accounts: [state.accounts[0]],
    dailyPlans: [{plan_date: tomorrow, version: 1, updated_at: today, accounts: [row]}]};
  const ui = context.window.AutoXhsEmployeePanel({...services, state: isolated,
    mutateJson: async (url, method, body) => {
      calls.push({url, method, body});
      if (url.endsWith("/generate")) return {plan: {accounts: [{...row,
        metrics: {...row.metrics, browse: 15, comment: 3, reply: 1}}]}};
      if (url.endsWith("/preview")) return {materials: [{plan_version: body.version}]};
      if (url.endsWith("/submit")) {
        isolated.dailyPlans[0].submissions = [{version: body.version}];
        return {status: "SUBMITTED"};
      }
      return {plan: {...body, version: 2, updated_at: today}};
    }});
  ui.init(); ui.render("");
  const click = (action) => handlers.click({target: {closest: () => ({dataset: {unitAction: action}})}});
  await click("generate-plan");
  assert.equal(isolated.dailyPlans[0].accounts[0].metrics.comment, 1);
  const beforeSubmit = calls.length;
  await click("submit-plan");
  assert.equal(calls.length, beforeSubmit);
  await click("save-plan");
  assert.equal(isolated.dailyPlans[0].accounts[0].metrics.browse, 15);
  assert.equal(isolated.dailyPlans[0].accounts[0].metrics.reply, 1);
  await click("preview-plan");
  assert.match(controls["[data-dispatch-template] textarea"].value, /"plan_version": 2/);
  await click("submit-plan");
  assert.equal(calls.at(-1).body.version, 2);
  ui.render("");
  assert.ok(root.innerHTML.includes("已提交 v2"));
  console.log("PASS: generated drafts, saved versions, material preview and explicit submission");
}
checkPlanTemplate().then(checkDailySubmission).then(checkAccountUnits)
  .catch((error) => {console.error(error); process.exitCode = 1;});
