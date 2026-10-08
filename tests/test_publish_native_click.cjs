const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync('extension/background.js', 'utf8');
const code = source.slice(source.indexOf('function publishDomNodes('), source.indexOf('async function cmdClickViaDebugger('));
function harness(disabled = false, text = '\u53d1\u5e03', scale = 1, moved = false,
  detached = false, mouseDelivered = true) {
  let pointer = {x:0,y:0};
  const calls = [];
  const root = {nodeName: 'HTML', children: [{nodeName: 'XHS-PUBLISH-BTN',
    attributes: ['is-publish', 'true'], shadowRoots: [{children: [
      {nodeId: 8, nodeName: 'BUTTON', children: [{nodeType: 3, nodeValue: 'save'}]},
      {nodeId: 9, nodeName: 'BUTTON', attributes: disabled ? ['disabled', ''] : [],
        children: [{nodeType: 3, nodeValue: text}]}
    ]}]}]};
  const context = vm.createContext({
    getOrOpenPlatformTab: async id => ({id: id ?? 57}),
    sleep: async () => {},
    _dispatchRealClickAt: async (target, x, y) => {
      calls.push(['native', target.tabId, x, y]);
      calls.push(['Input.dispatchMouseEvent', {type:'mousePressed', x, y}]);
      calls.push(['Input.dispatchMouseEvent', {type:'mouseReleased', x, y}]);
    },
    chrome: { debugger: {
      attach: async () => calls.push(['attach']), detach: async () => calls.push(['detach']),
      sendCommand: async (_, method, params) => {
        calls.push([method, params]);
        if (method === 'Input.dispatchMouseEvent') pointer = {x:params.x / scale,y:params.y / scale};
        if (method === 'Runtime.evaluate' && params.expression === 'window.__xhsPointer') return {result:{value:pointer}};
        if (method === 'Runtime.callFunctionOn' &&
          (params.functionDeclaration.includes(":hover") ||
           params.functionDeclaration.includes('this.isConnected'))) {
          return {result:{value: !(detached && params.functionDeclaration.includes('isConnected'))}};
        }
        if (method === 'DOM.getDocument') return {root};
        if (method === 'DOM.resolveNode') return {object: {objectId:'publish-button'}};
        if (method === 'Runtime.callFunctionOn' && params.returnByValue) {
          const n=calls.filter(c=>c[0]==='Runtime.callFunctionOn' && c[1].returnByValue).length;
          return {result:{value:{x:790,y:n===1?630:(moved && n>5?550:540),width:180,height:60,viewportHeight:585}}};
        }
        if (method === 'Runtime.evaluate') {
          if (params.expression === 'window.__xhsPublishNativeClick || null') {
            return {result:{value:mouseDelivered ? {received:true,trusted:true} : null}};
          }
          if (params.expression === 'window.__xhsPublishNativeEvents || []' ||
              params.expression === 'window.__xhsPublishPageEvents || []') {
            return {result:{value:mouseDelivered ? [{type:'click',trusted:true}] : []}};
          }
          return {result:{value:null}};
        }
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
  const press = h.calls.find(c => c[0] === 'Input.dispatchMouseEvent' && c[1].type === 'mousePressed');
  assert.equal(press[1].x,790);
  assert.equal(press[1].y,540);
  assert.equal(h.calls.some(c => c[0] === 'native'),true);
  assert.equal(h.calls.some(c => c[0] === 'Page.bringToFront'),true);
  assert.equal(r.page_events[0].type,'click');
  assert.equal(h.calls.some(c => c[0] === 'Input.dispatchKeyEvent'), false);
  const lastMove = h.calls.findLastIndex(c => c[0] === 'Input.dispatchMouseEvent' && c[1].type === 'mouseMoved');
  assert.ok(h.calls.slice(lastMove + 1).some(c => c[0] === 'Runtime.callFunctionOn' && c[1].functionDeclaration.includes('isConnected')));
  assert.equal(h.calls.at(-1)[0], 'detach');
});
test('unreceived mouse input focuses the same button and submits once with Enter', async () => {
  const h = harness(false, '发布', 1, false, false, false);
  const r = await h.context.cmdClickPublishControl();
  assert.equal(r.method, 'native_mouse_then_keyboard');
  assert.equal(h.calls.filter(c => c[0] === 'DOM.focus').length, 1);
  assert.deepEqual(h.calls.filter(c => c[0] === 'Input.dispatchKeyEvent')
    .map(c => c[1].type), ['keyDown', 'keyUp']);
});
test('unavailable publish control never sends a click', async () => {
  for (const h of [harness(true), harness(false, 'other')]) {
    await assert.rejects(h.context.cmdClickPublishControl());
    assert.equal(h.calls.some(c => c[0] === 'Input.dispatchMouseEvent' && c[1].type === 'mousePressed'), false);
    assert.equal(h.calls.at(-1)[0], 'detach');
  }
});

test('publish input follows the measured zoom and explicit tab without submitting during inspection', async () => {
  const h = harness(false, '\u53d1\u5e03', 1.4);
  const r = await h.context.cmdClickPublishControl({_tab_id: 99, dry_run: true});
  assert.equal(r.ready, true);
  assert.equal(r.tab_id, 99);
  assert.ok(Math.abs(r.point.x - 790 * 1.4) < 0.01);
  assert.ok(Math.abs(r.point.y - 540 * 1.4) < 0.01);
  assert.equal(h.calls.some(c => c[0] === 'Input.dispatchMouseEvent' && c[1].type === 'mousePressed'), false);
});

test('changed layout or replaced button stops before native press', async () => {
  for (const h of [harness(false, '发布', 1, true), harness(false, '发布', 1, false, true)]) {
    const r = await h.context.cmdClickPublishControl();
    assert.equal(r.dispatched, false);
    assert.equal(h.calls.some(c => c[0] === 'Input.dispatchMouseEvent' && c[1].type === 'mousePressed'), false);
  }
});
