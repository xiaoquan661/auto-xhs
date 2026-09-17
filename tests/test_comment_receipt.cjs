const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../scripts/xhs/comment_receipt.py'), 'utf8');
const arm = source.match(/_ARM_NOTICE = r"""([\s\S]*?)"""/)[1].replace('__KEY__', '"receipt"');
const read = source.match(/_READ_NOTICE = r"""([\s\S]*?)"""/)[1].replace('__KEY__', '"receipt"');
function element(text) {
  return {nodeType: 1, isConnected: true, innerText: text,
    getClientRects: () => [1], matches: () => true, closest: () => null, querySelectorAll: () => []};
}
function harness(initial = []) {
  let callback, timer, disconnected = false;
  const context = vm.createContext({window: {}, location: {href: 'https://www.rednote.com/explore/n'},
    document: {documentElement: element(''), querySelectorAll: () => initial},
    getComputedStyle: () => ({display: 'block', visibility: 'visible'}),
    MutationObserver: class {constructor(cb) {callback = cb;} observe() {} disconnect() {disconnected = true;}},
    setTimeout: cb => {timer = cb; return 1;}, clearTimeout() {}, Date, Map, Promise});
  vm.runInContext(arm, context);
  return {context, emit: el => callback([{target: el, addedNodes: []}]),
    expire: () => timer(), disconnected: () => disconnected};
}
test('notice remains readable after the visible toast disappears', () => {
  const h = harness(), toast = element('评论成功');
  h.emit(toast);
  toast.isConnected = false;
  assert.equal(vm.runInContext(read, h.context).status, 'success');
  assert.equal(h.disconnected(), true);
});
test('existing notice is not accepted as the new submission', () => {
  const toast = element('评论成功'), h = harness([toast]);
  h.emit(toast); h.expire();
  assert.equal(vm.runInContext(read, h.context).status, 'unknown');
});
test('observer catches notice while caller awaits the result', async () => {
  const h = harness();
  const pending = vm.runInContext(read, h.context);
  h.emit(element('评论发布成功'));
  assert.equal((await pending).status, 'success');
});
test('unrelated page text is not a success receipt', () => {
  const h = harness(); h.emit(element('有人说评论成功')); h.expire();
  assert.equal(vm.runInContext(read, h.context).status, 'unknown');
});
