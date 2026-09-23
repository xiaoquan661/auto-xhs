const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require("node:path").join(__dirname, "../webui/manager-console.js"), "utf8");
const body = source.slice(source.indexOf("  function operationalExceptions()"), source.indexOf("  function renderExceptions()"));
const account = {machine_id: "PC052", account_slot: "xiix", scheduling_enabled: false, account_note: "已封禁"};
const result = (id, status, capability = "collect-operations-metrics", slot = "xiix") => ({
  machine_id: "PC052", account_slot: slot, task_id: id, state: status, capability,
});
const results = [result("closed", "BLOCKED"), result("stopped", "FAILED"),
  result("unknown", "RESULT_UNKNOWN", "post-comment"), result("success", "SUCCESS"),
  result("one", "FAILED", "collect-operations-metrics", "2-2"),
  result("two", "FAILED", "collect-operations-metrics", "2-2")];
const state = {accounts: [account], machines: [], alerts: [], sync: {}, results,
  tasks: results.map((r) => ({...r, business_state: r.state === "SUCCESS" ? "RESULT_UNKNOWN" : r.state,
    request: {account_slot: r.account_slot, capability: r.capability}}))};
const context = vm.createContext({state, taskNeedsReconciliation: (t) => t.business_state === "RESULT_UNKNOWN",
  resultAccount: (r) => r.account_slot});
vm.runInContext(body, context);
const alerts = context.operationalExceptions();
assert.equal(alerts.length, 3);
assert.equal(alerts.filter((a) => a.state === "PAUSED").length, 1);
assert.equal(alerts.filter((a) => a.state === "RESULT_UNKNOWN").length, 1);
assert.equal(alerts.find((a) => a.state === "FAILED").count, 2);
assert.ok(alerts.find((a) => a.state === "FAILED").message.includes("同类记录 2 次"));
console.log("PASS: paused collection failures, task/receipt deduplication, grouped failures and unknown outcomes");
