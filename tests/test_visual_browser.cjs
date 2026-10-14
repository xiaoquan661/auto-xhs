const { extensionAssetPath } = require("./project_paths.cjs");
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(extensionAssetPath("visual_browser.js"), 'utf8');
function harness() {
  const calls = [];
  let windowState='minimized';
  const frame = {document_id:'a',url:'https://www.xiaohongshu.com/explore',width:1000,
    height:600,scroll_x:0,scroll_y:0,device_pixel_ratio:2};
  const context = vm.createContext({ console, setTimeout, clearTimeout, Date, Number,
    getOrOpenPlatformTab: async id => ({id:id || 5,windowId:2,active:!id || id===5}),
    platformSpec: () => ({tabUrls:['https://www.xiaohongshu.com/*']}),
    binding: {platform:'xiaohongshu'},
    normalizePlatform: value => value,
    platformForUrl: url => new URL(url).hostname.endsWith('xiaohongshu.com')
      ? 'xiaohongshu' : null,
    _dispatchRealClickAt: async (target,x,y) => calls.push({target,x,y}),
    chrome: {
      windows: {get:async()=>({state:windowState}),
        update:async(id,options)=>{
          calls.push({window:id,...options});windowState=options.state;
          return {state:windowState};
        }},
      tabs: {query:async()=>[{id:5,active:true}],
        update:async(id,options)=>calls.push({tab:id,...options})},
      action: {setBadgeText:async v=>calls.push(v),setBadgeBackgroundColor:async()=>{},
        setTitle:async()=>{}},
      scripting: {executeScript:async ({target})=>[{result:frame}]},
      debugger: {attach:async target=>calls.push({attach:target.tabId}),
        detach:async target=>calls.push({detach:target.tabId}),
        sendCommand:async (target,method,params)=>{
          calls.push({target,method,params});return {data:'PNG'};}}
    }
  });
  vm.runInContext(source,context);
  const background = fs.readFileSync(extensionAssetPath("background.js"), 'utf8');
  vm.runInContext(background.match(/async function cmdScreenshot\([^]*?\n}/)[0], context);
  return {context,calls,frame};
}
test('snapshot captures the requested tab rather than the active tab',async()=>{
  const h=harness();const result=await h.context.cmdVisualSnapshot({_tab_id:9});
  assert.equal(result.tab_id,9);
  assert.ok(h.calls.some(x=>x.target?.tabId===9 && x.method==='Page.captureScreenshot'));
  assert.equal(h.calls[0].state,'normal');
  assert.equal(h.calls[0].focused,true);
  assert.equal(h.calls[1].tab,9);
  assert.equal(h.calls.some(x=>x.detach),false);
  assert.equal(h.calls.at(-2).tab,5);
  assert.equal(h.calls.at(-1).state,'minimized');
  assert.equal(result.window_restored_for_capture,true);
  assert.equal(result.window_state_before,'minimized');
  assert.equal(result.window_state_during_capture,'normal');
  assert.equal(result.window_state_after,'minimized');
});
test('legacy screenshot returns base64 from the requested tab with debugger permissions',async()=>{
  const h=harness();
  const result=await h.context.cmdScreenshot({_tab_id:9});
  assert.equal(result.data,'PNG');
  assert.ok(h.calls.some(x=>x.target?.tabId===9 && x.method==='Page.captureScreenshot'));
  assert.equal(h.calls.at(-1).state,'minimized');
});
test('snapshot reads the frame immediately while page resources are loading',async()=>{
  const h=harness();
  h.context.chrome.scripting.executeScript=async options=>{
    assert.equal(options.injectImmediately,true);
    return [{result:h.frame}];
  };
  assert.equal((await h.context.cmdVisualSnapshot()).data,'PNG');
});
test('snapshot reads the same platform frame through debugger when page injection is refused',async()=>{
  const h=harness();
  h.context.chrome.scripting.executeScript=async()=>{
    throw Error('Cannot access contents of the page. Extension manifest must request permission');
  };
  h.context.chrome.debugger.sendCommand=async(target,method,params)=>{
    h.calls.push({target,method,params});
    if (method==='Runtime.evaluate') return {result:{value:h.frame}};
    return {data:'PNG'};
  };
  const shot=await h.context.cmdVisualSnapshot();
  assert.equal(shot.data,'PNG');
  assert.equal(shot.frame.url,h.frame.url);
  assert.equal(h.calls.filter(x=>x.method==='Runtime.evaluate').length,2);
  await h.context.cmdVisualAction({frame:shot.frame,action:'click',x:20,y:30});
  assert.ok(h.calls.some(x=>x.x===20 && x.y===30));
});
test('stalled viewport injection switches to debugger and still returns an image',async()=>{
  const h=harness();
  h.context.setTimeout=(fn,ms)=>setTimeout(fn,ms===5000?1:ms);
  h.context.chrome.scripting.executeScript=()=>new Promise(()=>{});
  h.context.chrome.debugger.sendCommand=async(target,method)=>{
    h.calls.push({target,method});
    return method==='Runtime.evaluate' ? {result:{value:h.frame}} : {data:'PNG'};
  };
  assert.equal((await h.context.cmdVisualSnapshot()).data,'PNG');
  assert.equal(h.calls.filter(x=>x.method==='Runtime.evaluate').length,2);
});
test('Chrome error page cannot become a visual action frame',async()=>{
  const h=harness();
  h.context.chrome.scripting.executeScript=async()=>{
    throw Error('Cannot access contents of the page');
  };
  h.context.chrome.debugger.sendCommand=async(target,method)=>{
    if (method==='Runtime.evaluate') return {result:{value:{...h.frame,
      url:'chrome-error://chromewebdata/'}}};
    return {data:'PNG'};
  };
  await assert.rejects(h.context.cmdVisualSnapshot(),/页面暂不可截图/);
  assert.ok(h.calls.some(x=>x.detach===5));
});
test('slow image capture can finish after the short viewport deadline',async()=>{
  const h=harness();
  h.context.setTimeout=(fn,ms)=>setTimeout(fn,ms===10000?1:ms===30000?50:ms);
  h.context.chrome.debugger.sendCommand=async(target,method)=>{
    if (method==='Page.captureScreenshot') {
      await new Promise(resolve=>setTimeout(resolve,10));
      return {data:'PNG'};
    }
    return {};
  };
  assert.equal((await h.context.cmdVisualSnapshot()).data,'PNG');
});
test('normal-window screenshot keeps the existing window state',async()=>{
  const h=harness();
  h.context.chrome.windows.get=async()=>({state:'normal'});
  h.frame.scroll_x=10; h.frame.scroll_y=500;
  await h.context.cmdVisualSnapshot();
  const params=h.calls.find(x=>x.method==='Page.captureScreenshot').params;
  assert.equal(params.captureBeyondViewport,false);
  assert.equal(params.clip,undefined);
  assert.equal(h.calls.some(x=>'window' in x),false);
});
test('inactive target tab is brought forward for capture and previous tab is restored',async()=>{
  const h=harness();
  h.context.chrome.windows.get=async()=>({state:'normal'});
  await h.context.cmdVisualSnapshot({_tab_id:9});
  assert.equal(h.calls.find(x=>x.tab===9).active,true);
  assert.ok(h.calls.findIndex(x=>x.method==='Page.bringToFront') <
    h.calls.findIndex(x=>x.method==='Page.captureScreenshot'));
  assert.equal(h.calls.at(-1).tab,5);
});
test('debugger attachment failure still returns the window to minimized',async()=>{
  const h=harness();
  h.context.chrome.debugger.attach=async()=>{throw Error('debugger busy');};
  await assert.rejects(h.context.cmdVisualSnapshot(),/debugger busy/);
  assert.equal(h.calls.at(-1).state,'minimized');
});
test('legacy screenshot releases debugger when capture fails',async()=>{
  const h=harness();
  h.context.chrome.debugger.sendCommand=async(target,method,params)=>{
    h.calls.push({target,method,params});
    if (method==='Page.captureScreenshot') throw Error('capture failed');
    return {};
  };
  await assert.rejects(h.context.cmdScreenshot(),/capture failed/);
  assert.equal(h.calls.at(-2).detach,5);
  assert.equal(h.calls.at(-1).state,'minimized');
});
test('minimized snapshot recaptures a resized viewport within one attachment',async()=>{
  const h=harness();
  let reads=0, captures=0;
  h.context.chrome.scripting.executeScript=async()=>[{result:{...h.frame,
    height:++reads===1?600:574}}];
  h.context.chrome.debugger.sendCommand=async(target,method,params)=>{
    if (method!=='Page.captureScreenshot') return {};
    assert.equal(params.fromSurface,true);
    assert.equal(params.captureBeyondViewport,false);
    return {data:`PNG-${++captures}`};
  };
  const result=await h.context.cmdVisualSnapshot({_tab_id:9});
  assert.equal(result.data,'PNG-2');
  assert.equal(result.frame.height,574);
  assert.equal(result.capture_attempts,2);
  assert.equal(result.frame_adjustments[0].height.before,600);
  assert.equal(result.frame_adjustments[0].height.after,574);
  assert.equal(result.window_state_before,'minimized');
  assert.equal(result.window_state_after,'minimized');
  assert.equal(h.calls.filter(x=>x.attach).length,1);
  assert.equal(h.calls.at(-1).state,'minimized');
});
test('continuously changing page returns frame differences and releases debugger',async()=>{
  const h=harness();
  let reads=0;
  h.context.chrome.scripting.executeScript=async()=>[{result:{...h.frame,scroll_y:++reads}}];
  await assert.rejects(h.context.cmdVisualSnapshot(),/页面布局持续变化.*scroll_y/);
  assert.equal(h.calls.filter(x=>x.method==='Page.captureScreenshot').length,3);
  assert.equal(h.calls.at(-2).detach,5);
  assert.equal(h.calls.at(-1).state,'minimized');
});
test('changed frame rejects clicks before dispatch',async()=>{
  const h=harness();
  await assert.rejects(h.context.cmdVisualAction({_tab_id:5,frame:{...h.frame,scroll_y:40},
    action:'click',x:20,y:30}),/VISUAL_FRAME_CHANGED/);
  assert.equal(h.calls.some(x=>x.x !== undefined),false);
  assert.ok(h.calls.some(x=>x.detach===5));
});
test('visual click uses CSS pixels and detaches',async()=>{
  const h=harness();
  await h.context.cmdVisualAction({_tab_id:5,frame:h.frame,action:'click',x:20,y:30});
  assert.ok(h.calls.some(x=>x.x===20 && x.y===30));assert.ok(h.calls.some(x=>x.detach===5));
});
test('activity clears the busy badge on success and reports failure',async()=>{
  const h=harness();
  h.context.chrome.scripting.executeScript=async options=>{
    assert.equal(options.injectImmediately,true);
    return [{result:null}];
  };
  await h.context.withBridgeActivity({method:'visual_snapshot'},async()=>42);
  assert.deepEqual(h.calls.filter(x=>'text' in x).map(x=>x.text),['调用','']);
  await assert.rejects(h.context.withBridgeActivity({method:'visual_action'},async()=>{
    throw Error('failed');
  }),/failed/);
  assert.equal(h.calls.at(-1).text,'!');
});
test('a suspended page activity label does not block the command result',async()=>{
  const h=harness();
  h.context.setTimeout=(fn)=>setTimeout(fn,1);
  h.context.chrome.scripting.executeScript=()=>new Promise(()=>{});
  assert.equal(await h.context.withBridgeActivity({method:'visual_snapshot'},async()=>42),42);
});


test('action waits for debugger banner viewport before checking screenshot',async()=>{
  const h=harness();
  let attached=false, reads=0;
  h.context.chrome.windows.get=async()=>({state:'maximized'});
  h.context.chrome.debugger.attach=async()=>{attached=true;};
  h.context.chrome.debugger.detach=async()=>{attached=false;};
  h.context.chrome.scripting.executeScript=async()=>{
    assert.equal(attached,true);
    return [{result:{...h.frame,height:++reads<3?604:534}}];
  };
  const result=await h.context.cmdVisualAction({_tab_id:5,
    frame:{...h.frame,height:534},action:'click',x:20,y:500});
  assert.equal(result.dispatched,true);
  assert.ok(reads>=5);
  assert.equal(attached,false);
  assert.equal(h.calls.filter(x=>x.x===20 && x.y===500).length,1);
});

test('real document change stops input after attachment and restores window',async()=>{
  const h=harness();
  h.context.setTimeout=(fn)=>setTimeout(fn,1);
  await assert.rejects(h.context.cmdVisualAction({_tab_id:9,
    frame:{...h.frame,document_id:'previous'},action:'click',x:20,y:30}),
    error=>/VISUAL_FRAME_CHANGED/.test(error.message) && !/RESULT_UNKNOWN/.test(error.message));
  assert.equal(h.calls.some(x=>x.x !== undefined),false);
  assert.ok(h.calls.some(x=>x.detach===9));
  assert.equal(h.calls.at(-2).tab,5);
  assert.equal(h.calls.at(-1).state,'minimized');
});


test('image observation and click share one debugger attachment',async()=>{
  const h=harness();
  h.context.chrome.windows.get=async()=>({state:'maximized'});
  const shot=await h.context.cmdVisualSnapshot({_tab_id:9});
  assert.equal(h.calls.some(x=>x.detach),false);
  await h.context.cmdVisualAction({_tab_id:9,frame:shot.frame,action:'click',x:20,y:30});
  assert.equal(h.calls.filter(x=>x.attach===9).length,1);
  assert.equal(h.calls.filter(x=>x.detach===9).length,1);
});

test('switching to a normal command releases screenshot attachment',async()=>{
  const h=harness();
  await h.context.cmdVisualSnapshot({_tab_id:9});
  await h.context.releaseVisualDebuggers();
  assert.equal(h.calls.filter(x=>x.detach===9).length,1);
  await h.context.releaseVisualDebuggers();
  assert.equal(h.calls.filter(x=>x.detach===9).length,1);
});
