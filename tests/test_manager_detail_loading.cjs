const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require("node:path").join(__dirname, "../webui/manager-console.js"), "utf8");
const body = source.slice(source.indexOf("  async function loadDashboardDetails("), source.indexOf("  function scheduleRefresh()"));
const calls = [];
const state = {snapshot: {details_deferred: true}, results: [{task_id: "current"}]};
const context = vm.createContext({state, managerApi: "/manager", selectedView: "employee-units",
  selectedOperationTaskId: "", renderAll: () => {}, setMessage: () => {},
  fetchJson: async (url) => {
    calls.push(url);
    if (url.endsWith("audit")) return {result_history: [{task_id: "historical"}]};
    if (url.endsWith("debug")) return {debug: {incidents: []}};
    return {agent_messages: [{summary: "progress"}]};
  }});
vm.runInContext(body, context);
(async () => {
  await context.loadDashboardDetails();
  context.selectedView = "exceptions";
  await context.loadDashboardDetails();
  assert.equal(calls.length, 0);
  context.selectedView = "settings";
  await context.loadDashboardDetails();
  assert.deepEqual(calls, ["/manager/dashboard-details/debug"]);
  context.selectedView = "results";
  await context.loadDashboardDetails();
  await context.loadDashboardDetails();
  assert.equal(calls.length, 2);
  assert.equal(state.resultHistory[0].task_id, "historical");
  await context.loadDashboardDetails("messages");
  assert.equal(state.agentMessages[0].summary, "progress");
  console.log("PASS: dashboard details load by page and remain cached within each snapshot");
})().catch((error) => {console.error(error); process.exitCode = 1;});
