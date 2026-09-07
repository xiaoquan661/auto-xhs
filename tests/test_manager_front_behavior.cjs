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

const resultContext = vm.createContext({
  element: (tag, className, text = "") => ({
    tag, className, text, children: [],
    appendChild(child) { this.children.push(child); },
  }),
  append: (parent, ...children) => children.forEach((child) => parent.appendChild(child)),
  labelFor: (value) => value,
});
for (const name of ["resultDataStatus", "renderBusinessResult", "resultCompleteness"]) {
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
