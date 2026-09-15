// Browser observations and actions share the same tab and viewport.
async function visualFrame(tabId) {
  const [result] = await chrome.scripting.executeScript({
    target: { tabId }, world: "ISOLATED",
    func: () => {
      window.__xhsVisualDocument ||= crypto.randomUUID();
      return { document_id: window.__xhsVisualDocument, url: location.href,
        width: innerWidth, height: innerHeight, scroll_x: scrollX, scroll_y: scrollY,
        device_pixel_ratio: devicePixelRatio };
    },
  });
  return result.result;
}

async function cmdVisualSnapshot(params = {}) {
  const tab = await getOrOpenPlatformTab(params._tab_id);
  const target = { tabId: tab.id };
  await chrome.debugger.attach(target, "1.3");
  try {
    const frame = await visualFrame(tab.id);
    const { data } = await chrome.debugger.sendCommand(target, "Page.captureScreenshot", {
      format: "png", captureBeyondViewport: false,
    });
    const after = await visualFrame(tab.id);
    if (!sameVisualFrame(frame, after)) throw new Error("页面已变化，请重新截图");
    return { data, tab_id: tab.id, frame, captured_at: new Date().toISOString() };
  } finally {
    await chrome.debugger.detach(target).catch(() => {});
  }
}

function sameVisualFrame(before, after) {
  return ["document_id", "url", "width", "height", "scroll_x", "scroll_y",
    "device_pixel_ratio"].every(key => before[key] === after[key]);
}

async function cmdVisualAction(params) {
  const tab = await getOrOpenPlatformTab(params._tab_id);
  const frame = await visualFrame(tab.id);
  if (!params.frame || !sameVisualFrame(params.frame, frame)) {
    throw new Error("VISUAL_FRAME_CHANGED: 页面已变化，请重新截图后操作");
  }
  const { action, x, y } = params;
  if (!["click", "scroll", "type", "key"].includes(action)) throw new Error("不支持的视觉操作");
  if (["click", "scroll"].includes(action) &&
      (!Number.isFinite(x) || !Number.isFinite(y) || x < 0 || y < 0 ||
       x >= frame.width || y >= frame.height)) throw new Error("操作坐标超出截图范围");
  const keys = { Escape: 27, Tab: 9, ArrowLeft: 37, ArrowRight: 39,
    ArrowUp: 38, ArrowDown: 40, Backspace: 8 };
  if (action === "key" && !keys[params.key]) throw new Error("不支持的按键");
  const target = { tabId: tab.id };
  await chrome.debugger.attach(target, "1.3");
  try {
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
    throw new Error(`RESULT_UNKNOWN: 操作已尝试，请先截图核对；${error.message}`);
  } finally {
    await chrome.debugger.detach(target).catch(() => {});
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
    target: { tabId }, world: "ISOLATED", args: [text, busy, failed],
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
  await showBridgeActivity(tabId, text, true).catch(() => {});
  let failed = false;
  try { return await run(); }
  catch (error) { failed = true; throw error; }
  finally {
    if (sequence === activitySequence) {
      await showBridgeActivity(tabId, failed ? "操作异常" : "调用完成", false, failed).catch(() => {});
    }
  }
}
