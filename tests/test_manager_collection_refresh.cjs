const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require("node:path").join(__dirname, "../webui/manager-console.js"), "utf8");

function extract(name) {
  const start = source.indexOf(`  async function ${name}(`);
  assert.ok(start >= 0);
  const rest = source.slice(start + 1);
  const next = rest.search(/\n  (?:async )?function /);
  return source.slice(start, start + 1 + next);
}

function setup(performLoad) {
  const messages = [];
  const context = vm.createContext({
    performLoad, messages, pendingActions: new Set(),
    mutateJson: async () => ({success: true, message: "已回收 1 个账号采集回执"}),
    setMessage: (...args) => messages.push(args),
    renderAll() {}, businessPayload() {}, labelFor: x => x,
    scheduleRefresh() {}, window: {clearTimeout() {}},
  });
  vm.runInContext(`let refreshPromise = null; let refreshTimer = null;\n${extract("load")}\n${extract("runMutation")}`, context);
  context.button = {dataset: {}, setAttribute() {}, removeAttribute() {}};
  return context;
}

test("采集完成后读取新快照，旧轮询返回后再发起刷新", async () => {
  let finishOld;
  let calls = 0;
  const context = setup(() => {
    calls++;
    return calls === 1 ? new Promise(resolve => {finishOld = resolve;}) : Promise.resolve(true);
  });
  const old = context.load();
  const mutation = context.runMutation(context.button, "/collect", {}, "message", "采集中");
  await Promise.resolve();
  assert.equal(calls, 1);
  finishOld(true);
  await Promise.all([old, mutation]);
  assert.equal(calls, 2);
  assert.equal(context.messages.at(-1)[2], "success");
});

test("回执成功但快照失败时明确显示刷新失败", async () => {
  const context = setup(async () => false);
  await context.runMutation(context.button, "/collect", {}, "message", "采集中");
  assert.match(context.messages.at(-1)[1], /已回收.*页面数据刷新失败/);
  assert.equal(context.messages.at(-1)[2], "warning");
  assert.equal(context.button.disabled, false);
});
