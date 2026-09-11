const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../webui/manager-console.js"), "utf8");
function extract(name) {
  const start = source.indexOf(`  async function ${name}(`);
  assert.ok(start >= 0, name);
  const end = source.indexOf("\n  }", start);
  assert.ok(end > start, name);
  return source.slice(start, end + 4);
}

for (const recorded of [true, false]) {
  test(recorded ? "经理启动已绑定账号" : "经理启动首次接入账号", async () => {
    const calls = [];
    const account = {machine_id: "PC 057", account_slot: "alpha / one", identity: {recorded}};
    const context = vm.createContext({
      state: {accounts: [account]}, managerApi: "/api/manager",
      window: {confirm: () => true},
      accountTools: {open: async (...args) => calls.push({identity: args})},
      runMutation: async (_button, url, body) => calls.push({url, body}),
    });
    vm.runInContext(extract("handleAccountAction"), context);
    await context.handleAccountAction({dataset: {
      managerMachine: account.machine_id, managerAccount: account.account_slot,
      managerAccountAction: "profile-start",
    }});
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url,
      "/api/manager/machines/PC%20057/accounts/alpha%20%2F%20one/profile/start");
    assert.equal(JSON.stringify(calls[0].body), "{}");
  });
}

test("身份核对按机器和槽位选择账号", async () => {
  const accounts = [
    {machine_id: "PC052", account_slot: "alpha"},
    {machine_id: "PC057", account_slot: "alpha"},
  ];
  const calls = [];
  const context = vm.createContext({state: {accounts},
    accountTools: {open: async (action, account) => calls.push({action, account})},
  });
  vm.runInContext(extract("handleAccountAction"), context);
  await context.handleAccountAction({dataset: {managerMachine: "PC057",
    managerAccount: "alpha", managerAccountAction: "identity-check"}});
  assert.deepEqual(calls, [{action: "identity-check", account: accounts[1]}]);
});

test("经理任务操作回读指定远程任务", async () => {
  const calls = [];
  const context = vm.createContext({managerApi: "/api/manager",
    runMutation: async (_button, url, body, message) => calls.push({url, body, message}),
  });
  vm.runInContext(extract("handleTaskAction"), context);
  await context.handleTaskAction({dataset: {managerIdentity: "task / 1"}});
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/manager/tasks/task%20%2F%201/reconcile");
  assert.equal(JSON.stringify(calls[0].body), "{}");
  assert.equal(calls[0].message, "#manager-task-message");
});
