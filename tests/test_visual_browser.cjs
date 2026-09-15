const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../extension/visual_browser.js'), 'utf8');
function harness() {
  const calls = [];
  const frame = {document_id:'a',url:'https://www.xiaohongshu.com/explore',width:1000,
    height:600,scroll_x:0,scroll_y:0,device_pixel_ratio:2};
  const context = vm.createContext({ console, setTimeout, Date, Number,
    getOrOpenPlatformTab: async id => ({id:id || 5}),
    platformSpec: () => ({tabUrls:['https://www.xiaohongshu.com/*']}),
    _dispatchRealClickAt: async (target,x,y) => calls.push({target,x,y}),
    chrome: {
      tabs: {query:async()=>[{id:5,active:true}]},
      action: {setBadgeText:async v=>calls.push(v),setBadgeBackgroundColor:async()=>{},
        setTitle:async()=>{}},
      scripting: {executeScript:async ({target})=>[{result:frame}]},
      debugger: {attach:async target=>calls.push({attach:target.tabId}),
        detach:async target=>calls.push({detach:target.tabId}),
        sendCommand:async (target,method)=>{calls.push({target,method});return {data:'PNG'};}}
    }
  });
  vm.runInContext(source,context);
  return {context,calls,frame};
}
test('snapshot captures the requested tab rather than the active tab',async()=>{
  const h=harness();const result=await h.context.cmdVisualSnapshot({_tab_id:9});
  assert.equal(result.tab_id,9);
  assert.ok(h.calls.some(x=>x.target?.tabId===9 && x.method==='Page.captureScreenshot'));
  assert.equal(h.calls.at(-1).detach,9);
});
test('changed frame rejects clicks before dispatch',async()=>{
  const h=harness();
  await assert.rejects(h.context.cmdVisualAction({_tab_id:5,frame:{...h.frame,scroll_y:40},
    action:'click',x:20,y:30}),/VISUAL_FRAME_CHANGED/);
  assert.equal(h.calls.length,0);
});
test('visual click uses CSS pixels and detaches',async()=>{
  const h=harness();
  await h.context.cmdVisualAction({_tab_id:5,frame:h.frame,action:'click',x:20,y:30});
  assert.ok(h.calls.some(x=>x.x===20 && x.y===30));assert.equal(h.calls.at(-1).detach,5);
});
test('activity clears the busy badge on success and reports failure',async()=>{
  const h=harness();
  await h.context.withBridgeActivity({method:'visual_snapshot'},async()=>42);
  assert.deepEqual(h.calls.filter(x=>'text' in x).map(x=>x.text),['调用','']);
  await assert.rejects(h.context.withBridgeActivity({method:'visual_action'},async()=>{
    throw Error('failed');
  }),/failed/);
  assert.equal(h.calls.at(-1).text,'!');
});
