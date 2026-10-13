const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../webui/manager-bulk-collection.js'), 'utf8');

function setup(mutateJson, refresh = true) {
  const nodes = {};
  const node = key => nodes[key] ||= {dataset: {}, disabled: false, checked: true,
    textContent: '', events: {}, replaceChildren(...children) {this.children = children;},
    addEventListener(name, fn) {this.events[name] = fn;}};
  const buttons = ['creator', 'notifications', 'outbox'].map(action => {
    const button = node(action); button.dataset.recoveryBulk = action; return button;
  });
  const context = vm.createContext({window: {}, document: {querySelector: node,
    querySelectorAll: () => buttons, createElement: () => ({dataset: {}})}});
  vm.runInContext(source, context);
  const state = {machines: ['A', 'B', 'C', 'D'].map(machine_id => ({machine_id})),
    accounts: ['A', 'B', 'C', 'D'].flatMap(machine_id => ['one', 'two'].map(account_slot => ({machine_id, account_slot})))};
  const loads = [];
  const view = context.window.AutoXhsBulkCollection({state, mutateJson, managerApi: '/manager',
    load: async options => {loads.push(options); return refresh;}});
  view.init();
  return {view, state, nodes, buttons, loads, node};
}

test('全部作品采集覆盖账号、机器间并发且同机串行，账号失败不阻断后续', async () => {
  const calls = [];
  const active = new Set();
  let peak = 0;
  const context = setup(async (url, method, body) => {
    assert.equal(method, 'POST'); assert.match(url, /creator-metrics\/collect-now$/);
    assert.ok(!active.has(body.machine_id), 'same machine must be serial');
    active.add(body.machine_id); peak = Math.max(peak, active.size);
    calls.push({...body});
    await new Promise(resolve => setImmediate(resolve));
    active.delete(body.machine_id);
    if (body.machine_id === 'A' && body.account_slot === 'one') throw new Error('连接失败');
    if (body.machine_id === 'B' && body.account_slot === 'one') {
      throw Object.assign(new Error('账号暂停'), {payload: {success: false, status: 'PARTIAL_SUCCESS',
        accounts: [{account_slot: 'one', status: 'PAUSED', message: '账号已暂停'}]}});
    }
    return {success: true, status: 'SUCCESS', database_status: 'SYNCED', collected: 1,
      accounts: [{account_slot: body.account_slot, status: 'SUCCESS', count: 2}]};
  });
  context.state.accounts.push({...context.state.accounts[0]});
  const pending = context.view.start('creator');
  await context.view.start('creator'); // Busy click is ignored.
  assert.ok(context.buttons.every(button => button.disabled));
  // A later account refresh does not change this round's target set.
  context.state.accounts.push({machine_id: 'A', account_slot: 'three'});
  context.view.renderScope();
  await pending;
  assert.equal(calls.length, 8);
  assert.equal(peak, 3);
  const progress = context.nodes['#recovery-bulk-progress'].children;
  assert.equal(progress.filter(row => row.dataset.status === 'SUCCESS').length, 6);
  assert.equal(progress.filter(row => row.dataset.status === 'PAUSED').length, 1);
  assert.match(context.nodes['#recovery-bulk-message'].textContent, /已处理 8\/8，成功 6，需关注 2/);
  assert.equal(context.loads.length, 1); assert.equal(context.loads[0].fresh, true);
  assert.ok(context.buttons.every(button => !button.disabled));
});

test('全部通知保留部分完成及数据库待同步状态，并报告刷新失败', async () => {
  const context = setup(async (url, method, body) => {
    assert.match(url, /notifications\/collect-now$/);
    return {success: true, collected: 1, database_status: 'PENDING',
      accounts: [{account_slot: body.account_slot, status: 'SUCCESS', count: 20}]};
  }, false);
  await context.view.start('notifications');
  const rows = context.nodes['#recovery-bulk-progress'].children;
  assert.ok(rows.every(row => row.dataset.status === 'PARTIAL_SUCCESS'));
  assert.match(rows[0].textContent, /20 条.*业务数据库待同步/);
  assert.match(context.nodes['#recovery-bulk-message'].textContent, /页面刷新失败/);
});

test('全部回执按机器回收一次并传递证据选项，单账号执行期间不启动', async () => {
  const calls = [];
  const context = setup(async (url, method, body) => {
    calls.push({url, body}); return {success: true, status: 'SUCCESS', collected: 3};
  });
  context.node('#manager-collect-results').disabled = true;
  await context.view.start('outbox'); assert.equal(calls.length, 0);
  context.node('#manager-collect-results').disabled = false;
  context.node('#recovery-bulk-evidence').checked = false;
  await context.view.start('outbox');
  assert.equal(calls.length, 4);
  assert.equal(new Set(calls.map(row => row.url)).size, 4);
  assert.ok(calls.every(row => row.url.endsWith('/collect') && !row.body.download_evidence));
});

test('重复回执入库待同步显示采集结果和具体原因', async () => {
  const context = setup(async (url, method, body) => {
    throw Object.assign(new Error('业务数据库待同步'), {payload: {
      success: false, status: 'PARTIAL_SUCCESS', collected: 0, received: 1,
      database_status: 'PENDING', database_error: 'database is locked',
      accounts: [{account_slot: body.account_slot, status: 'SUCCESS', count: 6,
        message: '本机 CLI 已采集 6 篇作品'}],
    }});
  });
  await context.view.start('creator');
  const rows = context.nodes['#recovery-bulk-progress'].children;
  assert.ok(rows.every(row => row.dataset.status === 'PARTIAL_SUCCESS'));
  assert.match(rows[0].textContent, /6 篇.*业务数据库待同步：database is locked/);
});
