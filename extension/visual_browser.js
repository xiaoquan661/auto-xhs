// Keep the debugger banner stable while the Agent reads a screenshot and acts on it.
const visualAttachments = new Map();
async function releaseVisualDebugger(tabId) {
  const timer = visualAttachments.get(tabId);
  if (timer === undefined) return;
  clearTimeout(timer);
  visualAttachments.delete(tabId);
  await chrome.debugger.detach({tabId}).catch(() => {});
}
async function releaseVisualDebuggers() {
  for (const tabId of [...visualAttachments.keys()]) await releaseVisualDebugger(tabId);
}
async function acquireVisualDebugger(target) {
  if (!visualAttachments.has(target.tabId)) await chrome.debugger.attach(target, "1.3");
  clearTimeout(visualAttachments.get(target.tabId));
  const timer = setTimeout(() => releaseVisualDebugger(target.tabId), 180000);
  timer?.unref?.();
  visualAttachments.set(target.tabId, timer);
}
chrome.debugger.onDetach?.addListener(source => {
  clearTimeout(visualAttachments.get(source.tabId));
  visualAttachments.delete(source.tabId);
});

// Browser observations and actions share the same tab and viewport.
async function visualFrame(tabId) {
  const [result] = await chrome.scripting.executeScript({
    target: { tabId }, world: "ISOLATED", injectImmediately: true,
    func: () => {
      window.__xhsVisualDocument ||= crypto.randomUUID();
      return { document_id: window.__xhsVisualDocument, url: location.href,
        width: innerWidth, height: innerHeight, scroll_x: scrollX, scroll_y: scrollY,
        device_pixel_ratio: devicePixelRatio };
    },
  });
  return result.result;
}

async function visualStep(label, operation, timeout = 10000) {
  let timer;
  try {
    return await Promise.race([operation, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`截图步骤超时：${label}`)), timeout);
    })]);
  } finally { clearTimeout(timer); }
}

async function cmdVisualSnapshot(params = {}) {
  const tab = await getOrOpenPlatformTab(params._tab_id);
  const target = { tabId: tab.id };
  const windowBefore = await chrome.windows.get(tab.windowId);
  const restoreWindow = windowBefore.state === "minimized";
  let attached = false;
  let previousActiveTab;
  let snapshot;
  let captureWindowState = windowBefore.state;
  try {
    if (restoreWindow) {
      [previousActiveTab] = await chrome.tabs.query({ windowId: tab.windowId, active: true });
      const restored = await chrome.windows.update(tab.windowId, { state: "normal", focused: true });
      captureWindowState = restored.state;
      await chrome.tabs.update(tab.id, { active: true });
    }
    await acquireVisualDebugger(target);
    attached = true;
    const adjustments = [];
    // Window restoration and the debugger banner can resize the viewport.
    // Keep the same attachment while taking a fresh image of the settled layout.
    for (let attempt = 1; attempt <= 3; attempt++) {
      const frame = await visualStep("读取截图前视口", visualFrame(tab.id));
      const { data } = await visualStep("获取页面图像",
        chrome.debugger.sendCommand(target, "Page.captureScreenshot", {
          format: "png", fromSurface: true, captureBeyondViewport: false,
        }));
      const after = await visualStep("读取截图后视口", visualFrame(tab.id));
      if (sameVisualFrame(frame, after)) {
        snapshot = { data, tab_id: tab.id, frame, captured_at: new Date().toISOString(),
          capture_attempts: attempt, frame_adjustments: adjustments };
        break;
      }
      adjustments.push(visualFrameChanges(frame, after));
      if (attempt < 3) await new Promise(resolve => setTimeout(resolve, 100));
    }
    if (!snapshot) throw new Error(`页面布局持续变化，请重新截图：${JSON.stringify(adjustments)}`);
  } finally {
    try {
      if (attached && !snapshot) await releaseVisualDebugger(tab.id);
      if (previousActiveTab && previousActiveTab.id !== tab.id) {
        await chrome.tabs.update(previousActiveTab.id, { active: true });
      }
    } finally {
      if (restoreWindow) await chrome.windows.update(tab.windowId, { state: "minimized" });
    }
  }
  const windowAfter = await chrome.windows.get(tab.windowId);
  return { ...snapshot, window_restored_for_capture: restoreWindow,
    window_state_before: windowBefore.state, window_state_during_capture: captureWindowState,
    window_state_after: windowAfter.state };
}

function sameVisualFrame(before, after) {
  return Object.keys(visualFrameChanges(before, after)).length === 0;
}

function visualFrameChanges(before, after) {
  return Object.fromEntries(["document_id", "url", "width", "height", "scroll_x", "scroll_y",
    "device_pixel_ratio"].filter(key => before[key] !== after[key])
    .map(key => [key, { before: before[key], after: after[key] }]));
}

async function cmdVisualAction(params) {
  const tab = await getOrOpenPlatformTab(params._tab_id);
  const frame = params.frame;
  if (!frame) throw new Error("缺少截图视口，请先截图");
  const { action, x, y } = params;
  if (!["click", "scroll", "type", "key"].includes(action)) throw new Error("不支持的视觉操作");
  if (["click", "scroll"].includes(action) &&
      (!Number.isFinite(x) || !Number.isFinite(y) || x < 0 || y < 0 ||
       x >= frame.width || y >= frame.height)) throw new Error("操作坐标超出截图范围");
  const keys = { Escape: 27, Tab: 9, ArrowLeft: 37, ArrowRight: 39,
    ArrowUp: 38, ArrowDown: 40, Backspace: 8 };
  if (action === "key" && !keys[params.key]) throw new Error("不支持的按键");
  const target = { tabId: tab.id };
  const windowBefore = await chrome.windows.get(tab.windowId);
  const restoreWindow = windowBefore.state === "minimized";
  let previousActiveTab;
  let attached = false;
  let attempted = false;
  try {
    if (restoreWindow) {
      [previousActiveTab] = await chrome.tabs.query({ windowId: tab.windowId, active: true });
      await chrome.windows.update(tab.windowId, { state: "normal", focused: true });
      await chrome.tabs.update(tab.id, { active: true });
    }
    await acquireVisualDebugger(target);
    attached = true;
    // Compare under the same debugger banner and restored-window state as the image.
    let stable = 0;
    let current;
    for (let attempt = 0; attempt < 20 && stable < 3; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 100));
      current = await visualStep("读取操作视口", visualFrame(tab.id));
      stable = sameVisualFrame(frame, current) ? stable + 1 : 0;
    }
    if (stable < 3) {
      throw new Error(`VISUAL_FRAME_CHANGED: 页面已变化，请重新截图后操作；${JSON.stringify(visualFrameChanges(frame, current))}`);
    }
    attempted = true;
    if (action === "click") await _dispatchRealClickAt(target, x, y);
    if (action === "scroll") await chrome.debugger.sendCommand(target,
      "Input.dispatchMouseEvent", { type: "mouseWheel", x, y,
        deltaX: params.delta_x || 0, deltaY: params.delta_y || 0 });
    if (action === "type") await chrome.debugger.sendCommand(target,
      "Input.insertText", { text: params.text });
    if (action === "key") {
      for (const type of ["keyDown", "keyUp"]) await chrome.debugger.sendCommand(target,
        "Input.dispatchKeyEvent", { type, key: params.key,
          windowsVirtualKeyCode: keys[params.key] });
    }
    return { dispatched: true, tab_id: tab.id };
  } catch (error) {
    if (!attempted) throw error;
    throw new Error(`RESULT_UNKNOWN: 操作已尝试，请先截图核对；${error.message}`);
  } finally {
    try {
      if (attached) await releaseVisualDebugger(tab.id);
      if (previousActiveTab && previousActiveTab.id !== tab.id) {
        await chrome.tabs.update(previousActiveTab.id, { active: true });
      }
    } finally {
      if (restoreWindow) await chrome.windows.update(tab.windowId, { state: "minimized" });
    }
  }
}

let activitySequence = 0;
let bridgeActivity = { text: "空闲", busy: false, failed: false };
async function showBridgeActivity(tabId, text, busy, failed = false) {
  bridgeActivity = { text, busy, failed };
  if (typeof broadcastStatus === "function") broadcastStatus();
  await chrome.action.setBadgeText({ text: busy ? "调用" : failed ? "!" : "" });
  await chrome.action.setBadgeBackgroundColor({ color: failed ? "#b45309" : "#2563eb" });
  await chrome.action.setTitle({ title: `XHS Bridge · ${text}` });
  if (!tabId) return;
  await chrome.scripting.executeScript({
    target: { tabId }, world: "ISOLATED", injectImmediately: true, args: [text, busy, failed],
    func: (text, busy, failed) => {
      let host = document.getElementById("xhs-agent-activity");
      if (!host) {
        host = document.createElement("div"); host.id = "xhs-agent-activity";
        host.style.cssText = "position:fixed;bottom:18px;left:18px;z-index:2147483647;pointer-events:none";
        host.attachShadow({ mode: "open" }); document.documentElement.appendChild(host);
      }
      clearTimeout(window.__xhsActivityTimer);
      const label = document.createElement("div");
      label.style.cssText = "padding:10px 16px;border-radius:20px;color:white;"
        + "font:14px system-ui;box-shadow:0 3px 16px #0003;background:"
        + (failed ? "#b45309" : "#2563eb");
      label.textContent = `${busy ? "● " : ""}员工 Agent · ${text}`;
      host.shadowRoot.replaceChildren(label);
      window.__xhsActivityTimer = setTimeout(() => host.remove(), busy ? 95000 : 2500);
    },
  });
}

async function withBridgeActivity(msg, run) {
  const sequence = ++activitySequence;
  let tabId;
  try {
    const tabs = await chrome.tabs.query({ url: platformSpec().tabUrls });
    tabId = msg.params?._tab_id ?? tabs.find(tab => tab.active)?.id ?? tabs[0]?.id;
  } catch (_) {}
  const method = msg.method || "";
  const text = method.includes("snapshot") || method === "screenshot_element" ? "正在截图"
    : method.startsWith("wait") ? "等待页面"
    : /click|type|press|send|navigate|visual_action/.test(method) ? "正在操作"
    : "正在读取";
  await visualStep("显示调用提示", showBridgeActivity(tabId, text, true), 1000).catch(() => {});
  let failed = false;
  try { return await run(); }
  catch (error) { failed = true; throw error; }
  finally {
    if (sequence === activitySequence) {
      await visualStep("更新调用提示",
        showBridgeActivity(tabId, failed ? "操作异常" : "调用完成", false, failed), 1000)
        .catch(() => {});
    }
  }
}
