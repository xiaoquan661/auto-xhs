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
