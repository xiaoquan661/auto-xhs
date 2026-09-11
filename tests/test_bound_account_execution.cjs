const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../webui/app.js"), "utf8");
const poll = source.slice(
  source.indexOf("async function pollGuidedAccountSetup()"),
  source.indexOf("async function inspectGuidedIdentity()"),
);

for (const ready of [true, false]) {
  test(ready ? "恢复已绑定账号直接就绪" : "首次接入读取页面身份", async () => {
    const calls = [];
    const context = vm.createContext({
      activeAccountSetup: { account: { name: "alpha" }, phase: "WAITING_EXTENSION" },
      api: "/api/v1",
      fetchJson: async () => ({
        ready,
        extension_connected: true,
        profile_verified: true,
        identity: { recorded: ready, user_id: ready ? "owner-alpha" : "" },
      }),
      finishGuidedAccountSetup: async (identity) => calls.push(["ready", identity.user_id]),
      inspectGuidedIdentity: async () => calls.push(["inspect"]),
    });
    await vm.runInContext(`${poll}\npollGuidedAccountSetup()`, context);
    assert.deepEqual(calls, ready ? [["ready", "owner-alpha"]] : [["inspect"]]);
  });
}
