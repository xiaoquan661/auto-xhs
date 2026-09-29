const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

test('all accounts renders cached overview when the snapshot is unavailable', () => {
  const source = fs.readFileSync('webui/manager-console.js', 'utf8');
  const selection = source.slice(source.indexOf('  function selectEmployeeAccount('),
    source.indexOf('  function accountsForSelectedNode('));
  const navigation = source.slice(source.indexOf('  function selectManagerView('),
    source.indexOf('  function renderPartialErrors('));
  const rendered = [];
  const context = vm.createContext({
    state: {available: false, accounts: [{machine_id: 'PC057', account_slot: 'ceshi3'}]},
    selectedEmployeeAccount: 'PC057:ceshi3', selectedView: 'employee-units',
    employeeAccountKey: (account) => `${account.machine_id}:${account.account_slot}`,
    employeePanel: {render: (key) => rendered.push(key)},
    renderMachineNavigation() {}, renderAll() {throw new Error('live snapshot required');},
    localStorage: {setItem() {}}, VIEW_STORAGE_KEY: 'view',
    $: () => ({dataset: {}}), document: {querySelectorAll: () => []},
    window: {scrollY: 0, location: {hash: '#employee-units'}, requestAnimationFrame() {}},
  });
  vm.runInContext(`${selection}\n${navigation}\nselectEmployeeAccount('');`, context);
  assert.deepEqual(rendered, ['']);
  assert.equal(context.selectedEmployeeAccount, '');
});
