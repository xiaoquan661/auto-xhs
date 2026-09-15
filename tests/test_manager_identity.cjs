const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../webui/manager-account-tools.js"), "utf8");
class Node {
  constructor(tag) {Object.assign(this, {tag, children: [], events: {}});}
  append(...children) {children.forEach((child) => this.children.push(child));}
  replaceChildren(...children) {this.children = children;}
  setAttribute() {}
  addEventListener(type, handler) {this.events[type] = handler;}
  async click() {await this.events.click();}
  showModal() {}
}
function find(node, predicate) {
  if (predicate(node)) return node;
  for (const child of node.children) {const found = find(child, predicate); if (found) return found;}
}
const readyIdentity = {recorded: true, user_id: "red-user", live_user_id: "red-user",
  live_nickname: "测试昵称", matches_record: true};
async function setup(identity, options = {}) {
  const body = new Node("body"); const calls = [];
  const context = vm.createContext({window: {}, document: {body, createElement: (tag) => new Node(tag)}});
  vm.runInContext(source, context);
  const response = {success: true, account: {platform: "rednote", raw_status: "READY", identity},
    operation: {identity: {platform: "rednote", user_id: "red-user", nickname: "测试昵称"}}};
  const tools = context.window.AutoXhsAccountTools({state: {}, managerApi: "/api/manager",
    load: async () => {},
    fetchJson: async (url) => {
      calls.push({url});
      return {success: true, account: {platform: "rednote", raw_status: "READY", identity: readyIdentity}};
    },
    mutateJson: async (url, method, request) => {
      calls.push({url, method, request});
      if (url.endsWith("/identity/check")) {
        if (options.checkError) throw new Error(options.checkError);
        return response;
      }
      if (options.confirmError) throw new Error(options.confirmError);
      return {operation: {success: true}};
    },
  });
  await tools.open("identity-check", {machine_id: "PC057", account_slot: "red account", platform: "rednote"});
  return {body, calls,
    retry: find(body, (node) => node.textContent === "重新检查身份"),
    confirm: find(body, (node) => node.textContent === "确认此账号并绑定"),
    result: find(body, (node) => node.className === "manager-account-tools-result")};
}
(async () => {
  const fresh = await setup({recorded: false, live_user_id: "red-user"});
  assert.equal(fresh.calls.length, 1);
  assert.ok(fresh.calls[0].url.endsWith("/red%20account/identity/check"));
  assert.equal(fresh.confirm.hidden, false);
  assert.match(fresh.result.textContent, /等待首次绑定确认/);
  assert.ok(find(fresh.body, (node) => node.textContent === "RedNote"));
  assert.ok(find(fresh.body, (node) => node.textContent === "red-user"));
  await fresh.confirm.click();
  assert.equal(fresh.calls[1].request.action, "identity-record");
  assert.equal(fresh.calls[1].request.values.expected_user_id, "red-user");
  assert.equal(fresh.calls[1].request.values.confirmed, true);
  assert.match(fresh.result.textContent, /核对一致.*READY/);
  assert.equal(fresh.confirm.hidden, true);

  const matched = await setup(readyIdentity);
  assert.match(matched.result.textContent, /核对一致/);
  assert.equal(matched.confirm.hidden, true);
  const mismatched = await setup({...readyIdentity, user_id: "old-user", matches_record: false});
  assert.match(mismatched.result.textContent, /不一致/);
  assert.equal(mismatched.confirm.hidden, true);
  const switching = await setup({recorded: false, switch_pending: true});
  assert.match(switching.result.textContent, /正在换号/);
  assert.equal(switching.confirm.hidden, true);
  const failed = await setup({}, {checkError: "页面读取超时"});
  assert.equal(failed.confirm.hidden, true);
  assert.equal(failed.result.textContent, "页面读取超时");
  const loggedOut = await setup({recorded: true, logged_out: true});
  assert.equal(loggedOut.confirm.hidden, true);
  assert.match(loggedOut.result.textContent, /确认登录后重新检查/);
  assert.equal(loggedOut.calls.length, 1);
  assert.equal(loggedOut.retry.disabled, false);
  await loggedOut.retry.click();
  assert.equal(loggedOut.calls.length, 2);
  const loginRequired = await setup({}, {checkError: "LOGIN_REQUIRED：请先登录"});
  assert.equal(loginRequired.confirm.hidden, true);
  assert.match(loginRequired.result.textContent, /请先登录/);
  assert.equal(loginRequired.calls.length, 1);
  assert.equal(loginRequired.retry.disabled, false);
  await loginRequired.retry.click();
  assert.equal(loginRequired.calls.length, 2);
  const changed = await setup({recorded: false}, {confirmError: "当前登录 UID 与确认页面不一致"});
  await changed.confirm.click();
  assert.match(changed.result.textContent, /重新检查身份/);
  assert.equal(changed.confirm.hidden, true);
  console.log("PASS: identity comparison, explicit first binding, UID change and failed checks");
})().catch((error) => {console.error(error); process.exitCode = 1;});
