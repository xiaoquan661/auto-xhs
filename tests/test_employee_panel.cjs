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
const state = {machines: [{machine_id: "QA-A", status: "READY", ssh_state: "ONLINE"},
  {machine_id: "QA-B", status: "OFFLINE"}],
  accounts: [{machine_id: "QA-A", account_slot: "same-slot", nickname: "<script>name</script>",
    current_uid: "uid-a", health_state: "READY"},
  {machine_id: "QA-B", account_slot: "same-slot", nickname: "Second account", health_state: "READY"}],
  dailyPlans: [{plan_date: yesterday, accounts: [{machine_id: "QA-A", account_slot: "same-slot", participating: true}]}],
  tasks: [], goals: [], results: [{machine_id: "QA-A", account_slot: "same-slot", date: today,
    result_id: "receipt-a", item_results: [{state: "SUCCESS", comment_text: "<b>text</b>", url: "https://example.com/note"}]}]};
const services = {state,
  workerDayMetrics: () => [{kind: "publish", target: 1, actual: 0, status: "未开始", tone: "info"},
    {kind: "comment", target: 2, actual: null, status: "等待回执", tone: "info"}],
  nextWorkerPlan: () => null, localDateValue: (date) => date.toISOString().slice(0, 10),
  resultDate: (item) => item.date, resultAccount: (item) => item.account_slot,
  resultLinks: (item) => item.url ? [{url: item.url}] : [], resultActionKind: () => "comment",
  workerTaskDate: (task) => task.date, accountPersona: () => null, labelFor: (value) => value || "—",
  operationalExceptions: () => []};
const panel = context.window.AutoXhsEmployeePanel(services);
panel.render("");
for (const section of ["员工机集群与执行单元", "物理机器总数", "机器与账号",
  "运营日报", "明日任务规划", "智能生成明日计划", "保存计划", "实际动作汇总", "告警与提醒"]) {
  assert.ok(root.innerHTML.includes(section), section);
}
assert.equal((root.innerHTML.match(/class="overview-node-card /g) || []).length, 2);
assert.ok(!root.innerHTML.includes('data-panel="employee-profile"'));
panel.render("QA-A");
for (const section of ["下一个运营日", "EMPLOYEE / PROFILE", "今日目标 / 实际", "往日目标 / 实际",
  "动作与对应链接", "评论回复提醒", "好友模块明细", "私聊明细"]) {
  assert.ok(root.innerHTML.includes(section), section);
}
assert.ok(root.innerHTML.includes("&lt;script&gt;name&lt;/script&gt;"));
assert.ok(root.innerHTML.includes("&lt;b&gt;text&lt;/b&gt;"));
assert.ok(!root.innerHTML.includes("Second account"));
const metrics = panel.dailyMetrics(state.accounts[0], today);
assert.equal(metrics.find((item) => item.kind === "publish").actual, 0);
assert.equal(metrics.find((item) => item.kind === "comment").actual, null);
assert.equal(metrics.find((item) => item.kind === "new_followers").status, "未接入");
assert.equal(panel.actions(state.accounts[1], today).length, 0);
panel.render("QA-B");
assert.ok(root.innerHTML.includes("当前快照没有私聊明细。"));
assert.ok(!root.innerHTML.includes("https://example.com/note"));
console.log("PASS: employee templates, all/detail views, account isolation and missing metrics");

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
  const click = (action) => handlers.click({target: {closest: () => ({dataset: {unitAction: action}})}});
  await click("generate");
  assert.match(controls["[data-plan-message]"].textContent, /尚未接入/);
  await click("save-plan");
  const templates = JSON.parse(saved.get("auto-xhs:employee-plan-templates"));
  const stored = Object.values(templates)[0];
  assert.equal(stored.rows[0].metrics.comment, 7);
  assert.equal(stored.rows[1].metrics.comment, null);
  assert.equal(state.dailyPlans.length, 1);
  assert.match(controls["[data-plan-message]"].textContent, /模板已保存到本机/);
  const reloaded = context.window.AutoXhsEmployeePanel(services);
  reloaded.render("");
  assert.ok(root.innerHTML.includes('data-plan-preview="comment">7</b>'));
  assert.ok(root.innerHTML.includes("复制模板"));
  console.log("PASS: numeric draft preview, local template persistence and generation availability");
}
checkPlanTemplate().catch((error) => {console.error(error); process.exitCode = 1;});
