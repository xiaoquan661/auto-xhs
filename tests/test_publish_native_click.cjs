const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync('extension/background.js', 'utf8');
const code = source.slice(source.indexOf('function publishDomNodes('), source.indexOf('async function cmdClickViaDebugger('));
function harness(disabled = false, text = '发布') {
  const calls = [];
  const root = {nodeName: 'HTML', children: [{nodeName: 'XHS-PUBLISH-BTN',
    attributes: ['is-publish', 'true'], shadowRoots: [{children: [
      {nodeId: 8, nodeName: 'BUTTON', children: [{nodeType: 3, nodeValue: '暂存离开'}]},
      {nodeId: 9, nodeName: 'BUTTON', attributes: disabled ? ['disabled', ''] : [],
        children: [{nodeType: 3, nodeValue: text}]}
    ]}]}]};
  const context = vm.createContext({
    getOrOpenPlatformTab: async () => ({id: 57}),
    sleep: async () => {},
    _dispatchRealClickAt: async (target, x, y) => calls.push(['native', target.tabId, x, y]),
    chrome: { debugger: {
      attach: async () => calls.push(['attach']), detach: async () => calls.push(['detach']),
      sendCommand: async (_, method, params) => {
        calls.push([method, params]);
        if (method === 'DOM.getDocument') return {root};
        if (method === 'DOM.resolveNode') return {object: {objectId:'publish-button'}};
        if (method === 'Runtime.callFunctionOn' && params.returnByValue) {
          const n=calls.filter(c=>c[0]==='Runtime.callFunctionOn' && c[1].returnByValue).length;
          return {result:{value:{x:790,y:n===1?630:540,width:180,height:60,viewportHeight:585}}};
        }
        if (method === 'Runtime.evaluate') return {result:{value:{received:true,trusted:true}}};
        return {};
      }
    }}
  });
  vm.runInContext(code, context);
  return {context, calls};
}
test('clicks the actual closed-shadow publish button after measuring its current layout', async () => {
  const h = harness();
  const r = await h.context.cmdClickPublishControl();
  assert.equal(r.dispatched, true);
  assert.equal(r.button_click.trusted, true);
  assert.equal(h.calls.find(c => c[0] === 'DOM.resolveNode')[1].nodeId, 9);
  assert.deepEqual(h.calls.find(c => c[0] === 'native'), ['native',57,790,540]);
  assert.equal(h.calls.at(-1)[0], 'detach');
});
test('unavailable publish control never sends a click', async () => {
  for (const h of [harness(true), harness(false, '其他')]) {
    await assert.rejects(h.context.cmdClickPublishControl());
    assert.equal(h.calls.some(c => c[0] === 'native'), false);
    assert.equal(h.calls.at(-1)[0], 'detach');
  }
});
