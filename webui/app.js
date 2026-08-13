const api = "/api/v1";
let sessionToken = "";

const $ = (selector) => document.querySelector(selector);
const text = (value) => String(value ?? "");

function signal(label, value) {
  const row = document.createElement("div");
  row.className = "signal";
  const name = document.createElement("span");
  name.textContent = label;
  const result = document.createElement("strong");
  result.textContent = value;
  row.append(name, result);
  return row;
}

function stateStyle(state) {
  if (state === "READY") return "ready";
  if (state === "ERROR") return "error";
  if (state === "IDENTITY_REQUIRED" || state === "IDENTITY_CHECK_REQUIRED") return "pending";
  return "blocked";
}

function stateLabel(state) {
  return {
    READY: "已就绪",
    BLOCKED: "需处理",
    ERROR: "异常",
    IDENTITY_REQUIRED: "待记录身份",
    IDENTITY_CHECK_REQUIRED: "待核验 UID",
    IDENTITY_MISMATCH: "身份不一致",
  }[state] || state;
}

function accountCard(account, status) {
  const card = document.createElement("article");
  card.className = "account-card";

  const head = document.createElement("div");
  head.className = "account-head";
  const identity = document.createElement("div");
  identity.className = "account-identity";
  const avatar = document.createElement("span");
  avatar.className = "avatar";
  avatar.textContent = text(account.name).slice(0, 2).toUpperCase();
  const titleWrap = document.createElement("div");
  const title = document.createElement("h3");
  title.textContent = account.name;
  const profile = document.createElement("p");
  profile.className = "profile";
  profile.textContent = `Chrome · ${account.chrome_profile_directory || "Default"}`;
  titleWrap.append(title, profile);
  identity.append(avatar, titleWrap);

  const badge = document.createElement("span");
  const state = status?.status || "ERROR";
  badge.className = `badge ${stateStyle(state)}`;
  badge.textContent = stateLabel(state);
  head.append(identity, badge);

  const signals = document.createElement("div");
  signals.className = "signals";
  signals.append(
    signal("BRIDGE", status?.server_running ? "在线" : "离线"),
    signal("扩展", status?.extension_connected ? "已连接" : "未连接"),
    signal("PROFILE", status?.profile_verified ? "已核验" : "待核验")
  );

  card.append(head, signals);
  const next = document.createElement("p");
  next.className = "next-action";
  const identityName = status?.identity?.nickname || status?.identity?.user_id;
  next.textContent = status?.next_action
    ? `下一步：${status.next_action}`
    : `当前身份：${text(identityName || "已核验")}`;
  card.append(next);
  const actions = document.createElement("div");
  actions.className = "account-actions";
  const pair = document.createElement("button");
  pair.type = "button";
  pair.className = "secondary-button";
  pair.textContent = "发起配对";
  pair.addEventListener("click", () => beginPairing(account.name, next));
  const identityButton = document.createElement("button");
  identityButton.type = "button";
  identityButton.className = "secondary-button";
  identityButton.textContent = status?.identity?.recorded ? "核验当前 UID" : "检查并确认 UID";
  identityButton.addEventListener("click", () => verifyIdentity(account.name, status?.identity?.recorded, next));
  actions.append(pair, identityButton);
  const bridgeButton = document.createElement("button");
  bridgeButton.type = "button";
  bridgeButton.className = "secondary-button";
  bridgeButton.textContent = status?.server_running ? "重启 Bridge" : "启动 Bridge";
  bridgeButton.addEventListener("click", () => updateBridge(account.name, status?.server_running ? "restart" : "start", next));
  const autostartButton = document.createElement("button");
  autostartButton.type = "button";
  autostartButton.className = "secondary-button";
  autostartButton.textContent = "Bridge 自启动";
  autostartButton.addEventListener("click", () => updateAutostart(account.name, next));
  actions.append(bridgeButton, autostartButton);
  card.append(actions);
  return card;
}

async function updateBridge(account, action, messageNode) {
  if (!window.confirm(`${action === "start" ? "启动" : "重启"}槽位 ${account} 的 Bridge？此操作不会打开或关闭 Chrome。`)) return;
  try {
    await mutateJson(`${api}/accounts/${encodeURIComponent(account)}/bridge/${action}`, "POST", {});
    messageNode.textContent = "Bridge 操作已完成，正在刷新账号状态。";
    await loadDashboard();
  } catch (error) { messageNode.textContent = error.message; }
}

async function updateAutostart(account, messageNode) {
  try {
    const current = await fetchJson(`${api}/accounts/${encodeURIComponent(account)}/autostart`);
    const enabled = !current.autostart.enabled;
    if (!window.confirm(`${enabled ? "启用" : "关闭"} ${account} 的 Windows 登录 Bridge 自启动？不会自动启动 Chrome。`)) return;
    await mutateJson(`${api}/accounts/${encodeURIComponent(account)}/autostart/update`, "POST", { enabled, confirmed: true });
    messageNode.textContent = `Bridge 自启动已${enabled ? "启用" : "关闭"}。`;
  } catch (error) { messageNode.textContent = error.message; }
}

async function beginPairing(account, messageNode) {
  if (!window.confirm(`将在槽位 ${account} 创建一次性配对请求，是否继续？`)) return;
  try {
    const payload = await mutateJson(`${api}/accounts/${encodeURIComponent(account)}/pairing/begin`, "POST", { confirmed: true });
    await navigator.clipboard.writeText(payload.pairing.pairing_bundle);
    messageNode.textContent = "配对信息已复制。请在目标 Chrome Profile 的扩展弹窗中粘贴并确认。";
  } catch (error) {
    messageNode.textContent = error.message;
  }
}

async function verifyIdentity(account, alreadyRecorded, messageNode) {
  try {
    const checked = await mutateJson(`${api}/accounts/${encodeURIComponent(account)}/identity/check`, "POST", {});
    const identity = checked.identity;
    if (!alreadyRecorded) {
      const accepted = window.confirm(`当前账号：${identity.nickname || "未命名"}\nUID：${identity.user_id}\n确认把它记录为槽位 ${account} 的身份吗？`);
      if (!accepted) {
        messageNode.textContent = "已读取当前身份，但没有记录。";
        return;
      }
      await mutateJson(`${api}/accounts/${encodeURIComponent(account)}/identity/record`, "POST", { confirmed: true });
    }
    messageNode.textContent = `已核验当前 UID：${identity.user_id}`;
    await loadDashboard();
  } catch (error) {
    messageNode.textContent = error.message;
  }
}

function emptyAccounts() {
  const empty = document.createElement("article");
  empty.className = "empty-card";
  const copy = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = "还没有账号槽位";
  const body = document.createElement("span");
  body.textContent = "从下方添加账号开始，系统会引导你完成 Profile、配对和 UID 核验。";
  copy.append(title, body);
  empty.append(copy);
  return empty;
}

async function fetchJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok || payload.success === false) {
    throw new Error(payload.error?.message || "请求失败");
  }
  return payload;
}

async function mutateJson(path, method, body) {
  if (!sessionToken) {
    const session = await fetchJson(`${api}/session`);
    sessionToken = session.session_token;
  }
  const response = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", "X-Auto-XHS-Session": sessionToken },
    body: JSON.stringify(body || {}),
  });
  const payload = await response.json();
  if (!response.ok || payload.success === false) throw new Error(payload.error?.message || "请求失败");
  return payload;
}

async function discoverProfiles() {
  const message = $("#setup-message");
  message.textContent = "正在扫描本机 Chrome Profile…";
  try {
    const payload = await mutateJson(`${api}/accounts/discover`, "POST", {});
    const select = $("#profile-select");
    select.replaceChildren();
    payload.profiles.forEach((profile) => {
      const option = document.createElement("option");
      option.value = JSON.stringify({
        user_data_dir: profile.profile_path.replace(/[\\/]?[^\\/]+$/, ""),
        profile_directory: profile.profile_directory,
      });
      option.textContent = `${profile.display_name} · ${profile.profile_directory}${profile.bound_account ? `（已绑定 ${profile.bound_account}）` : ""}`;
      option.disabled = Boolean(profile.bound_account);
      select.append(option);
    });
    message.textContent = payload.profiles.length ? "请选择未绑定的 Profile。" : "没有发现可用的 Chrome Profile。";
  } catch (error) {
    message.textContent = error.message;
  }
}

async function submitAccount(event) {
  event.preventDefault();
  const message = $("#setup-message");
  const name = $("#account-name").value.trim();
  const mode = $("#account-mode").value;
  message.textContent = "正在创建本地账号槽位…";
  try {
    let payload;
    if (mode === "existing") {
      const selected = $("#profile-select").value;
      if (!selected) throw new Error("请先扫描并选择一个 Chrome Profile");
      const profile = JSON.parse(selected);
      payload = await mutateJson(`${api}/accounts/import`, "POST", {
        name,
        user_data_dir: profile.user_data_dir,
        profile_directory: profile.profile_directory,
        confirmed: true,
      });
    } else {
      payload = await mutateJson(`${api}/accounts`, "POST", { name, confirmed: true });
    }
    message.textContent = `${payload.account.name} 已添加。下一步：${payload.next_action}`;
    event.target.reset();
    await loadDashboard();
  } catch (error) {
    message.textContent = error.message;
  }
}

function renderTaskItems(items, target, emptyText) {
  const container = $(target);
  container.replaceChildren();
  if (!items.length) {
    const empty = emptyAccounts();
    empty.querySelector("strong").textContent = emptyText;
    container.append(empty);
    return;
  }
  items.forEach((item) => {
    const card = document.createElement("article");
    card.className = "record-card";
    const state = document.createElement("span");
    state.className = `badge ${stateStyle(item.state)}`;
    state.textContent = stateLabel(item.state);
    const title = document.createElement("strong");
    title.textContent = `${item.account_slot} · ${item.capability}`;
    const summary = document.createElement("p");
    summary.textContent = item.result_summary || item.request_summary || "等待执行";
    card.append(state, title, summary);
    container.append(card);
  });
}

async function loadWorkData(accountData) {
  const [tasks, records, drafts] = await Promise.all([fetchJson(`${api}/tasks`), fetchJson(`${api}/records`), fetchJson(`${api}/drafts`)]);
  renderTaskItems(tasks.tasks, "#task-list", "暂无任务");
  renderTaskItems(records.records, "#record-list", "暂无执行记录");
  const select = $("#task-account");
  select.replaceChildren();
  accountData.accounts.forEach((account) => {
    const option = document.createElement("option");
    option.value = account.name;
    option.textContent = account.name;
    select.append(option);
  });
  const draftSelect = $("#draft-account");
  draftSelect.replaceChildren(...Array.from(select.options).map((item) => item.cloneNode(true)));
  renderDrafts(drafts.drafts);
}

function renderDrafts(drafts) {
  const container = $("#draft-list");
  container.replaceChildren();
  if (!drafts.length) {
    const empty = emptyAccounts();
    empty.querySelector("strong").textContent = "暂无草稿";
    container.append(empty);
    return;
  }
  drafts.forEach((draft) => {
    const card = document.createElement("article");
    card.className = "record-card draft-card";
    const badge = document.createElement("span"); badge.className = "badge pending"; badge.textContent = draft.status;
    const title = document.createElement("strong"); title.textContent = `${draft.account_slot} · ${draft.action_type}`;
    const content = document.createElement("p"); content.textContent = draft.content;
    const confirm = document.createElement("button"); confirm.className = "primary-button"; confirm.type = "button"; confirm.textContent = "核对并确认";
    confirm.addEventListener("click", () => confirmDraft(draft, card));
    card.append(badge, title, content, confirm); container.append(card);
  });
}

async function submitDraft(event) {
  event.preventDefault();
  const account = $("#draft-account").value;
  const accountStatus = await fetchJson(`${api}/accounts/${encodeURIComponent(account)}/status`);
  const uid = accountStatus.identity?.user_id;
  const message = $("#draft-message");
  if (!uid) { message.textContent = "请先在账号卡片中确认 UID。"; return; }
  try {
    await mutateJson(`${api}/drafts`, "POST", { account_slot: account, verified_uid: uid, action_type: $("#draft-action").value, target_id: $("#draft-target").value.trim(), target_summary: $("#draft-summary").value.trim(), content: $("#draft-content").value.trim() });
    message.textContent = "草稿已保存在本机，尚未发送。"; event.target.reset(); await loadDashboard();
  } catch (error) { message.textContent = error.message; }
}

async function confirmDraft(draft, card) {
  const accepted = window.confirm(`目标账号：${draft.account_slot}\nUID：${draft.verified_uid}\n目标：${draft.target_summary || draft.target_id}\n\n最终文本：\n${draft.content}\n\n确认后仍需填写页面令牌并执行，是否确认这份最终草稿？`);
  if (!accepted) return;
  try {
    const confirmed = await mutateJson(`${api}/drafts/${draft.draft_id}/confirm`, "POST", {});
    const feedId = window.prompt("请输入目标笔记 ID（回复评论也需要）：", draft.action_type === "post-comment" ? draft.target_id : "");
    if (!feedId) return;
    const token = window.prompt("请输入该笔记当前的 XSEC Token：", "");
    if (!token) return;
    const result = await mutateJson(`${api}/drafts/${draft.draft_id}/execute`, "POST", { approval_id: confirmed.approval.approval_id, feed_id: feedId, comment_id: draft.action_type === "reply-comment" ? draft.target_id : "", xsec_token: token });
    card.querySelector("p").textContent = result.task.result_summary || result.task.state;
    await loadDashboard();
  } catch (error) { card.querySelector("p").textContent = error.message; }
}

async function submitTask(event) {
  event.preventDefault();
  const capability = $("#task-capability").value;
  const target = $("#task-target").value.trim();
  const parameters = capability === "search-feeds" ? { keyword: target } : capability === "list-feeds" ? {} : { feed_id: target, xsec_token: $("#task-token").value.trim(), undo: $("#task-undo").checked };
  const message = $("#task-message");
  try {
    const created = await mutateJson(`${api}/tasks`, "POST", { source: "webui", account_slot: $("#task-account").value, capability, request_summary: `${capability}: ${target}`, parameters });
    const executed = await mutateJson(`${api}/tasks/${created.task.task_id}/execute`, "POST", {});
    message.textContent = executed.task.result_summary || executed.task.recommended_action || executed.task.state;
    await loadDashboard();
  } catch (error) { message.textContent = error.message; }
}

function renderDiagnosis(diagnosis) {
  const summary = diagnosis.summary || {};
  $("#healthy-total").textContent = summary.healthy_accounts ?? 0;
  $("#doctor-ready-total").textContent = summary.ready_accounts ?? 0;
  $("#warning-total").textContent = summary.warnings ?? 0;
  const badge = $("#diagnosis-state");
  badge.textContent = diagnosis.healthy ? "配置健康" : "需要检查";
  badge.className = `diagnosis-badge ${diagnosis.healthy ? "good" : "warn"}`;
  $("#diagnosis-note").textContent = diagnosis.healthy
    ? "基础配置检查通过。完整 READY 仍以实时扩展连接和当前 UID 核验为准。"
    : "发现配置或运行问题，请查看账号卡片中的下一步提示。";
}

async function loadDashboard() {
  const refresh = $("#refresh");
  const health = $("#health");
  const grid = $("#account-grid");
  refresh.disabled = true;
  try {
    const [healthData, accountData, capabilityData, diagnosisData, systemData] = await Promise.all([
      fetchJson(`${api}/health`),
      fetchJson(`${api}/accounts`),
      fetchJson(`${api}/capabilities`),
      fetchJson(`${api}/doctor`),
      fetchJson(`${api}/system/status`),
    ]);
    health.dataset.state = healthData.status === "ok" ? "ok" : "error";
    health.lastElementChild.textContent = healthData.status === "ok" ? "本地服务正常" : "本地服务异常";
    $("#capability-count").textContent = capabilityData.summary.enabled_in_v1;
    $("#account-total").textContent = accountData.accounts.length;
    renderDiagnosis(diagnosisData);
    renderSystem(systemData);
    await loadWorkData(accountData);

    grid.replaceChildren();
    if (accountData.accounts.length === 0) {
      grid.append(emptyAccounts());
      $("#connected-total").textContent = "0";
      $("#blocked-total").textContent = "0";
      $("#ready-total").textContent = "0";
      return;
    }

    const statuses = await Promise.all(
      accountData.accounts.map(async (account) => {
        try {
          return await fetchJson(`${api}/accounts/${encodeURIComponent(account.name)}/status`);
        } catch (error) {
          return { status: "ERROR", next_action: error.message };
        }
      })
    );
    accountData.accounts.forEach((account, index) => grid.append(accountCard(account, statuses[index])));

    $("#connected-total").textContent = statuses.filter((item) => item.extension_connected).length;
    $("#blocked-total").textContent = statuses.filter((item) => item.status !== "READY").length;
    $("#ready-total").textContent = statuses.filter((item) => item.status === "READY").length;
  } catch (error) {
    health.dataset.state = "error";
    health.lastElementChild.textContent = "本地服务不可用";
    const empty = emptyAccounts();
    empty.querySelector("strong").textContent = "控制台暂时无法读取状态";
    empty.querySelector("span").textContent = error.message;
    grid.replaceChildren(empty);
    $("#diagnosis-state").textContent = "连接失败";
    $("#diagnosis-state").className = "diagnosis-badge warn";
    $("#diagnosis-note").textContent = "请确认本地 WebUI 服务仍在运行。";
  } finally {
    refresh.disabled = false;
  }
}

function renderSystem(system) {
  const pause = $("#global-pause");
  pause.dataset.paused = system.global_paused ? "true" : "false";
  pause.textContent = system.global_paused ? "恢复全部任务" : "暂停全部任务";
  $("#product-version").textContent = system.product_version;
  $("#setting-concurrency").value = system.global_concurrency;
  $("#setting-hourly").value = system.l1_limits.hourly;
  $("#setting-daily").value = system.l1_limits.daily;
  $("#setting-dedup").value = system.l1_limits.dedup_minutes;
  $("#setting-failures").value = system.l1_limits.failure_threshold;
  $("#today-task-total").textContent = system.summary.tasks_total;
  $("#waiting-draft-total").textContent = system.summary.drafts_waiting;
  $("#recent-failure-total").textContent = system.summary.recent_failures;
}

async function toggleGlobalPause() {
  const pause = $("#global-pause");
  const shouldPause = pause.dataset.paused !== "true";
  if (!window.confirm(`${shouldPause ? "暂停" : "恢复"}全部新任务？正在执行的操作不会被强制中断。`)) return;
  try {
    await mutateJson(`${api}/system/${shouldPause ? "pause" : "resume"}`, "POST", {});
    await loadDashboard();
  } catch (error) { window.alert(error.message); }
}

async function saveSettings(event) {
  event.preventDefault();
  if (!window.confirm("确认更新全局并发、L1 配额和熔断设置？")) return;
  try {
    await mutateJson(`${api}/system/settings`, "POST", {
      confirmed: true,
      global_concurrency: Number($("#setting-concurrency").value),
      l1_limits: {
        hourly: Number($("#setting-hourly").value),
        daily: Number($("#setting-daily").value),
        dedup_minutes: Number($("#setting-dedup").value),
        failure_threshold: Number($("#setting-failures").value),
      },
    });
    $("#diagnostic-path").textContent = "运行设置已保存。";
    await loadDashboard();
  } catch (error) { $("#diagnostic-path").textContent = error.message; }
}

async function exportDiagnostics() {
  const message = $("#diagnostic-path");
  message.textContent = "正在生成报告…";
  try {
    const result = await mutateJson(`${api}/diagnostics/export`, "POST", {});
    message.textContent = result.path;
  } catch (error) { message.textContent = error.message; }
}

$("#refresh").addEventListener("click", loadDashboard);
$("#global-pause").addEventListener("click", toggleGlobalPause);
$("#settings-form").addEventListener("submit", saveSettings);
$("#export-diagnostics").addEventListener("click", exportDiagnostics);
$("#discover-profiles").addEventListener("click", discoverProfiles);
$("#account-form").addEventListener("submit", submitAccount);
$("#task-form").addEventListener("submit", submitTask);
$("#draft-form").addEventListener("submit", submitDraft);
$("#account-mode").addEventListener("change", (event) => {
  $("#profile-field").hidden = event.target.value !== "existing";
  $("#discover-profiles").hidden = event.target.value !== "existing";
});
loadDashboard();
