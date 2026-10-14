/**
 * XHS Bridge - Content Script（隔离 world）
 *
 * 负责在页面 MAIN world 的 interceptor.js 与 background.js 之间转发
 * 404 诊断事件和 NetLog 响应信号，并通知 interceptor.js 桥接已就绪。
 */

// ── interceptor.js (MAIN world) → background.js 桥接 ────────
// 注意：扩展 reload 后老 content script 仍在页面，但 chrome.runtime context 失效。
// 此时 sendMessage 会同步抛 "Extension context invalidated"（.catch 抓不到），
// 必须先用 chrome.runtime?.id 检测 context 是否还存活。
window.addEventListener("message", (e) => {
  if (e.source !== window) return;
  if (e.data?.source !== "xhs-interceptor" || e.data?.type !== "BLOCK_EVENT") return;
  if (!chrome.runtime?.id) return; // context invalidated, orphan content script
  try {
    chrome.runtime.sendMessage({ type: "XHS_BLOCK_EVENT", event: e.data.event }).catch(() => {});
  } catch (_) { /* runtime gone between guard and call */ }
});

// 通知 interceptor.js：content.js 已就绪，可以 flush 排队的事件
window.postMessage({ source: "xhs-content-ready" }, "*");

// ─── NetLog 信号转发 ───────────────────────────────────────────

// MAIN world interceptor → content (postMessage) → background (runtime)
window.addEventListener("message", (e) => {
  if (e.source !== window) return;
  if (e.data?.source !== "xhs-netlog-intercept") return;
  if (!chrome.runtime?.id) return; // extension reloaded, content script orphaned
  try {
    chrome.runtime.sendMessage({
      type: "NETLOG_INTERCEPTOR_ENTRY",
      payload: e.data,
    }).catch(() => {});
  } catch (_) { /* runtime gone between guard and call */ }
});
