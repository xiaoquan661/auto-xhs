const api = "/api/v1";

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
  return card;
}

function emptyAccounts() {
  const empty = document.createElement("article");
  empty.className = "empty-card";
  const copy = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = "还没有账号槽位";
  const body = document.createElement("span");
  body.textContent = "添加账号向导将在下一增量接入，这里会自动展示配置结果。";
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
    const [healthData, accountData, capabilityData, diagnosisData] = await Promise.all([
      fetchJson(`${api}/health`),
      fetchJson(`${api}/accounts`),
      fetchJson(`${api}/capabilities`),
      fetchJson(`${api}/doctor`),
    ]);
    health.dataset.state = healthData.status === "ok" ? "ok" : "error";
    health.lastElementChild.textContent = healthData.status === "ok" ? "本地服务正常" : "本地服务异常";
    $("#capability-count").textContent = capabilityData.summary.enabled_in_v1;
    $("#account-total").textContent = accountData.accounts.length;
    renderDiagnosis(diagnosisData);

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

$("#refresh").addEventListener("click", loadDashboard);
loadDashboard();
