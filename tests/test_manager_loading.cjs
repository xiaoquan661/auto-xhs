const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require("node:path").join(__dirname, "../webui/manager-console.js"), "utf8");
const body = source.slice(source.indexOf("  async function performLoad()"), source.indexOf("  function scheduleRefresh()"));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}
(async () => {
  const pending = {snapshot: deferred(), tasks: deferred(), system: deferred()};
  let paints = 0;
  const state = {};
  const context = vm.createContext({state, api: "/api", managerApi: "/manager",
    fetchJson: (url) => url.endsWith("dashboard-snapshot") ? pending.snapshot.promise
      : url.endsWith("tasks") ? pending.tasks.promise : pending.system.promise,
    renderAll: () => {paints++;}, renderUnavailable: () => {},
    setText: () => {}, setMessage: () => {}, $: () => ({dataset: {}})});
  vm.runInContext(body, context);
  const loaded = context.performLoad();
  pending.snapshot.resolve({accounts: [{account_slot: "a"}]});
  await new Promise(setImmediate);
  assert.equal(paints, 1, "主界面应在辅助接口返回前显示");
  assert.equal(state.accounts.length, 1);
  pending.tasks.resolve({tasks: [{task_id: "local"}]});
  pending.system.reject(new Error("辅助接口失败"));
  assert.equal(await loaded, true);
  assert.equal(state.available, true);
  assert.equal(state.localTasks.length, 1);
  assert.ok(state.errors.system);
  assert.equal(paints, 2);
  console.log("manager loading behavior passed");
})().catch(error => {console.error(error); process.exitCode = 1;});
