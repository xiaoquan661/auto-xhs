const api = "/api/v1";
let sessionToken = "";
let accountStatusByName = new Map();
document.documentElement.classList.add("js-ready");

const taskCapabilities = {
  "browse-feeds": {
    label: "自动浏览首页",
    help: "在一个首页会话中自动滚动、点开并阅读笔记；达到时间或数量任一上限即停止。",
    targetLabel: "",
    placeholder: "",
    targetRequired: false,
    targetVisible: false,
    tokenRequired: false,
    browseSettings: true,
  },
  "list-feeds": {
    label: "获取首页推荐",
    help: "读取当前首页推荐列表一次，不会自动滚动或点开笔记。",
    targetLabel: "任务备注（可选）",
    placeholder: "例如：上午选题采集",
    targetRequired: false,
    targetVisible: false,
    tokenRequired: false,
  },
  "search-feeds": {
    label: "搜索笔记",
    help: "按关键词搜索笔记，并返回标题、作者、互动数据和笔记 ID。",
    targetLabel: "搜索关键词",
    placeholder: "输入要搜索的关键词",
    targetRequired: true,
    targetVisible: true,
    tokenRequired: false,
  },
  "get-feed-detail": {
    label: "查看笔记详情",
    help: "读取指定笔记的正文、作者、互动数据和评论。可从搜索结果一键带入参数。",
    targetLabel: "笔记 ID",
    placeholder: "输入目标笔记 ID",
    targetRequired: true,
    targetVisible: true,
    tokenRequired: true,
  },
  "user-profile": {
    label: "查看用户主页",
    help: "读取指定用户的主页资料和公开笔记。可从搜索结果一键带入参数。",
    targetLabel: "用户 ID",
    placeholder: "输入目标用户 ID",
    targetRequired: true,
    targetVisible: true,
    tokenRequired: true,
  },
  "like-feed": {
    label: "点赞 / 取消点赞",
    help: "修改指定笔记的点赞状态，执行前会核验账号身份并受操作配额限制。",
    targetLabel: "笔记 ID",
    placeholder: "输入目标笔记 ID",
    targetRequired: true,
    targetVisible: true,
    tokenRequired: true,
  },
  "favorite-feed": {
    label: "收藏 / 取消收藏",
    help: "修改指定笔记的收藏状态，执行前会核验账号身份并受操作配额限制。",
    targetLabel: "笔记 ID",
    placeholder: "输入目标笔记 ID",
    targetRequired: true,
    targetVisible: true,
    tokenRequired: true,
  },
  "keyword-engagement": {
    label: "关键词随机点赞收藏",
    help: "按关键词搜索后模拟向下滑动，边滑边去重搜集候选；达到候选池、时间上限或连续无新增时停止，再随机抽取执行。",
    targetLabel: "笔记筛选关键词",
    placeholder: "例如：露营装备、AI 视频",
    targetRequired: true,
    targetVisible: true,
    tokenRequired: false,
    engagementSettings: true,
  },
};

const taskTemplates = {
  browse: ["browse-feeds"],
  search: ["search-feeds"],
  analysis: ["get-feed-detail", "user-profile"],
  engagement: ["keyword-engagement"],
};

const capabilityTemplate = Object.entries(taskTemplates).reduce((mapping, [template, capabilities]) => {
  capabilities.forEach((capability) => mapping.set(capability, template));
  return mapping;
}, new Map());

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
  if (state === "READY" || state === "SUCCESS" || state === "EXECUTED") return "ready";
  if (state === "PARTIAL_SUCCESS") return "blocked";
  if (state === "ERROR" || state === "FAILED" || state === "RESULT_UNKNOWN") return "error";
  if (["IDENTITY_REQUIRED", "IDENTITY_CHECK_REQUIRED", "QUEUED", "RUNNING", "WAITING_APPROVAL", "DRAFT", "CONFIRMED"].includes(state)) return "pending";
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
    QUEUED: "排队中",
    RUNNING: "执行中",
    WAITING_APPROVAL: "待确认",
    SUCCESS: "执行成功",
    PARTIAL_SUCCESS: "部分完成",
    FAILED: "执行失败",
    CANCELLED: "已取消",
    RESULT_UNKNOWN: "结果待核对",
    DRAFT: "草稿",
    CONFIRMED: "已确认",
    APPROVED_FOR_EXECUTION: "执行中",
    EXECUTED: "已执行",
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
  const extensionPath = document.createElement("p");
  extensionPath.className = "profile extension-path";
  extensionPath.textContent = `扩展 · ${account.extension_dir || "未配置"}`;
  extensionPath.title = account.extension_dir || "";
  titleWrap.append(title, profile, extensionPath);
  identity.append(avatar, titleWrap);

  const badge = document.createElement("span");
  const state = status?.status || "ERROR";
  card.dataset.state = state;
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
    message.textContent = `${payload.account.name} 已添加。扩展目录：${payload.account.extension_dir}。下一步：${payload.next_action}`;
    event.target.reset();
    await loadDashboard();
  } catch (error) {
    message.textContent = error.message;
  }
}

function taskTime(value) {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "" : parsed.toLocaleString("zh-CN", { hour12: false });
}

function taskCapabilityLabel(capability) {
  return taskCapabilities[capability]?.label || capability;
}

function renderTaskTemplateActions(preferredCapability = "") {
  const template = $("#task-template").value;
  const select = $("#task-capability");
  const capabilities = taskTemplates[template] || [];
  select.replaceChildren();
  capabilities.forEach((capability) => {
    const option = document.createElement("option");
    option.value = capability;
    option.textContent = taskCapabilityLabel(capability);
    select.append(option);
  });
  if (preferredCapability && capabilities.includes(preferredCapability)) {
    select.value = preferredCapability;
  }
  updateTaskFields();
}

function showTaskPanel(name) {
  document.querySelectorAll("[data-task-panel]").forEach((button) => {
    const active = button.dataset.taskPanel === name;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll("[data-task-view]").forEach((panel) => {
    const active = panel.dataset.taskView === name;
    panel.hidden = !active;
    panel.classList.toggle("active", active);
  });
}

function setupTaskPanels() {
  document.querySelectorAll("[data-task-panel]").forEach((button) => {
    button.addEventListener("click", () => showTaskPanel(button.dataset.taskPanel));
  });
}

function addResultMetric(container, label, value) {
  if (value === undefined || value === null || value === "") return;
  const metric = document.createElement("span");
  metric.textContent = `${label} ${value}`;
  container.append(metric);
}

function useResultAsTask(capability, target, token = "") {
  const template = capabilityTemplate.get(capability);
  if (template) $("#task-template").value = template;
  renderTaskTemplateActions(capability);
  $("#task-target").value = target || "";
  $("#task-token").value = token || "";
  showTaskPanel("immediate");
  $("#task-form").scrollIntoView({ behavior: "smooth", block: "center" });
  $("#task-target").focus();
}

function resultAction(label, capability, target, token) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "result-action";
  button.textContent = label;
  button.addEventListener("click", () => useResultAsTask(capability, target, token));
  return button;
}

function feedResultRow(feed, { interactive = false } = {}) {
  const row = document.createElement("article");
  row.className = "result-feed";
  const body = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = feed.displayTitle || "未命名笔记";
  const author = document.createElement("p");
  author.textContent = feed.user?.nickname ? `作者：${feed.user.nickname}` : "作者信息未返回";
  const metrics = document.createElement("div");
  metrics.className = "result-metrics";
  addResultMetric(metrics, "赞", feed.interactInfo?.likedCount);
  addResultMetric(metrics, "藏", feed.interactInfo?.collectedCount);
  addResultMetric(metrics, "评", feed.interactInfo?.commentCount);
  const identifier = document.createElement("small");
  identifier.textContent = `笔记 ID：${feed.id || "—"}`;
  body.append(title, author, metrics, identifier);
  row.append(body);
  if (interactive && feed.id && feed.xsecToken) {
    const actions = document.createElement("div");
    actions.className = "result-actions";
    actions.append(resultAction("查看详情", "get-feed-detail", feed.id, feed.xsecToken));
    if (feed.user?.userId) {
      actions.append(resultAction("查看作者", "user-profile", feed.user.userId, feed.xsecToken));
    }
    row.append(actions);
  }
  return row;
}

function appendFeedResults(container, feeds, options) {
  const list = document.createElement("div");
  list.className = "result-feed-list";
  feeds.forEach((feed) => list.append(feedResultRow(feed, options)));
  container.append(list);
}

function appendObjectFacts(container, values) {
  const facts = document.createElement("dl");
  facts.className = "result-facts";
  Object.entries(values || {}).forEach(([key, value]) => {
    if (value === "" || value === null || value === undefined || typeof value === "object") return;
    const term = document.createElement("dt");
    term.textContent = ({ nickname: "昵称", redId: "小红书号", desc: "简介", ipLocation: "地区", title: "标题", body: "正文", noteId: "笔记 ID", type: "类型" }[key] || key);
    const description = document.createElement("dd");
    description.textContent = String(value);
    facts.append(term, description);
  });
  if (facts.childElementCount) container.append(facts);
}

function appendBrowseResults(container, result) {
  const overview = document.createElement("div");
  overview.className = "browse-result-overview";
  addResultMetric(overview, "实际点开", `${result.count ?? 0} 篇`);
  addResultMetric(overview, "实际用时", `${Math.round(result.elapsed_seconds ?? 0)} 秒`);
  addResultMetric(overview, "结束原因", ({ count_reached: "达到数量", time_limit: "达到时间", no_more_feeds: "暂无更多笔记" }[result.stop_reason] || result.stop_reason));
  container.append(overview);
  const list = document.createElement("ol");
  list.className = "browse-result-list";
  result.items.forEach((item) => {
    const row = document.createElement("li");
    const body = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = item.title || "未命名笔记";
    const meta = document.createElement("span");
    meta.textContent = [item.author ? `作者：${item.author}` : "", item.read_seconds !== undefined ? `阅读 ${Math.round(item.read_seconds)} 秒` : ""].filter(Boolean).join(" · ");
    const identifier = document.createElement("small");
    identifier.textContent = `笔记 ID：${item.feed_id || "—"}`;
    body.append(title, meta, identifier);
    row.append(body);
    list.append(row);
  });
  container.append(list);
}

function appendKeywordEngagementResults(container, result) {
  const overview = document.createElement("div");
  overview.className = "browse-result-overview";
  addResultMetric(overview, "关键词", result.keyword);
  addResultMetric(overview, "候选", `${result.candidate_count ?? 0} 篇`);
  addResultMetric(overview, "实际滑动", `${result.scroll_count ?? 0} 次`);
  addResultMetric(overview, "搜集用时", `${Math.round(result.collection_elapsed_seconds ?? 0)} 秒`);
  addResultMetric(overview, "停止原因", ({ pool_reached: "候选池已满", time_limit: "达到时间上限", no_new_results: "连续无新增" }[result.collection_stop_reason] || result.collection_stop_reason));
  addResultMetric(overview, "成功", `${result.succeeded_count ?? 0} 篇`);
  addResultMetric(overview, "失败", `${result.failed_count ?? 0} 篇`);
  container.append(overview);

  const actionLabels = { like: "点赞", favorite: "收藏" };
  const statusLabels = { success: "成功", skipped: "已跳过", failed: "失败" };
  const list = document.createElement("ol");
  list.className = "browse-result-list engagement-result-list";
  result.items.forEach((item) => {
    const row = document.createElement("li");
    const body = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = item.title || "未命名笔记";
    const meta = document.createElement("span");
    meta.textContent = item.author ? `作者：${item.author}` : "作者信息未返回";
    const actions = document.createElement("div");
    actions.className = "engagement-action-results";
    Object.entries(item.actions || {}).forEach(([name, action]) => {
      const badge = document.createElement("span");
      badge.dataset.state = action.status || "failed";
      badge.textContent = `${actionLabels[name] || name}：${statusLabels[action.status] || action.status}`;
      badge.title = action.message || "";
      actions.append(badge);
    });
    const identifier = document.createElement("small");
    identifier.textContent = `笔记 ID：${item.feed_id || "—"}`;
    body.append(title, meta, actions, identifier);
    row.append(body);
    list.append(row);
  });
  container.append(list);
}

function appendTaskResult(card, item, { interactive = false } = {}) {
  const result = item.result;
  if (!result || typeof result !== "object" || !Object.keys(result).length) return;
  const details = document.createElement("details");
  details.className = "task-result";
  const toggle = document.createElement("summary");
  const count = Array.isArray(result.feeds) ? result.feeds.length : Array.isArray(result.items) ? result.items.length : null;
  toggle.textContent = count === null ? "查看执行结果" : `查看 ${count} 条结果`;
  details.append(toggle);
  const content = document.createElement("div");
  content.className = "task-result-content";
  if (result.result_type === "keyword_engagement" && Array.isArray(result.items)) {
    appendKeywordEngagementResults(content, result);
  } else if (Array.isArray(result.items)) {
    appendBrowseResults(content, result);
  } else if (result.note) {
    appendObjectFacts(content, result.note);
    if (Array.isArray(result.comments)) {
      const commentNote = document.createElement("p");
      commentNote.className = "result-note";
      commentNote.textContent = `同时返回 ${result.comments.length} 条评论。`;
      content.append(commentNote);
    }
  } else if (result.basicInfo) {
    appendObjectFacts(content, result.basicInfo);
    if (Array.isArray(result.interactions) && result.interactions.length) {
      const metrics = document.createElement("div");
      metrics.className = "result-metrics profile-metrics";
      result.interactions.forEach((metric) => addResultMetric(metrics, metric.name || metric.type || "数据", metric.count));
      content.append(metrics);
    }
    if (Array.isArray(result.feeds)) appendFeedResults(content, result.feeds, { interactive });
  } else if (Array.isArray(result.feeds)) {
    appendFeedResults(content, result.feeds, { interactive });
  } else {
    appendObjectFacts(content, result);
  }
  details.append(content);
  card.append(details);
}

async function updateTask(task, action, button) {
  const label = action === "retry" ? "重试" : "取消";
  const prompt = action === "retry"
    ? `确认重新执行 ${task.account_slot} 的 ${task.capability} 任务？`
    : `确认取消这条任务？取消后不会执行。`;
  if (!window.confirm(prompt)) return;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = `${label}中…`;
  try {
    await mutateJson(`${api}/tasks/${task.task_id}/${action}`, "POST", {});
    await loadDashboard();
  } catch (error) {
    button.disabled = false;
    button.textContent = original;
    window.alert(error.message);
  }
}

function renderTaskItems(items, target, emptyText, { interactive = false } = {}) {
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
    card.dataset.state = item.state || "UNKNOWN";
    const state = document.createElement("span");
    state.className = `badge ${stateStyle(item.state)}`;
    state.textContent = stateLabel(item.state);
    const title = document.createElement("strong");
    title.textContent = `${item.account_slot} · ${taskCapabilityLabel(item.capability)}`;
    const summary = document.createElement("p");
    summary.textContent = item.result_summary || item.recommended_action || item.request_summary || "等待执行";
    const meta = document.createElement("small");
    meta.className = "task-meta";
    const metaParts = [item.request_summary && item.request_summary !== summary.textContent ? `请求：${item.request_summary}` : "", item.error_code ? `原因：${item.error_code}` : "", taskTime(item.finished_at || item.created_at)];
    meta.textContent = metaParts.filter(Boolean).join(" · ");
    card.append(state, title, summary);
    if (meta.textContent) card.append(meta);
    appendTaskResult(card, item, { interactive });
    if (interactive && ["BLOCKED", "QUEUED", "WAITING_APPROVAL"].includes(item.state)) {
      const actions = document.createElement("div");
      actions.className = "task-actions";
      if (item.state === "BLOCKED") {
        const retry = document.createElement("button");
        retry.type = "button";
        retry.className = "secondary-button";
        retry.textContent = "重试";
        retry.addEventListener("click", () => updateTask(item, "retry", retry));
        actions.append(retry);
      }
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "secondary-button";
      cancel.textContent = "取消";
      cancel.addEventListener("click", () => updateTask(item, "cancel", cancel));
      actions.append(cancel);
      card.append(actions);
    }
    container.append(card);
  });
}

function populateAccountSelect(select, accounts, statuses, { readyRequired = false } = {}) {
  const previous = select.value;
  select.replaceChildren();
  accounts.forEach((account, index) => {
    const status = statuses[index] || { status: "ERROR", ready: false };
    const option = document.createElement("option");
    option.value = account.name;
    option.dataset.status = status.status;
    option.disabled = readyRequired && !status.ready;
    option.textContent = `${account.name}（${stateLabel(status.status)}）`;
    select.append(option);
  });
  const selectable = Array.from(select.options).filter((option) => !option.disabled);
  const retained = selectable.find((option) => option.value === previous);
  const ready = selectable.find((option) => option.dataset.status === "READY");
  if (retained) select.value = retained.value;
  else if (ready) select.value = ready.value;
  else if (selectable[0]) select.value = selectable[0].value;
}

function updateTaskAvailability() {
  const select = $("#task-account");
  const button = $("#task-submit");
  const status = accountStatusByName.get(select.value);
  const ready = Boolean(status?.ready);
  button.disabled = !ready;
  $("#task-message").textContent = ready
    ? `账号 ${select.value} 已就绪，可以创建并执行任务。`
    : "暂无 READY 账号，请先按账号卡片提示完成连接和 UID 核验。";
}

async function loadWorkData(accountData, statuses) {
  const [tasks, records, drafts] = await Promise.all([fetchJson(`${api}/tasks`), fetchJson(`${api}/records`), fetchJson(`${api}/drafts`)]);
  const resultByTask = new Map(records.records.filter((record) => record.result && Object.keys(record.result).length).map((record) => [record.task_id, record.result]));
  const tasksWithResults = tasks.tasks.map((task) => ({ ...task, result: resultByTask.get(task.task_id) || task.result || {} }));
  renderTaskItems(tasksWithResults, "#task-list", "暂无任务", { interactive: true });
  renderTaskItems(records.records, "#record-list", "暂无执行记录");
  accountStatusByName = new Map(accountData.accounts.map((account, index) => [account.name, statuses[index]]));
  populateAccountSelect($("#task-account"), accountData.accounts, statuses, { readyRequired: true });
  populateAccountSelect($("#draft-account"), accountData.accounts, statuses);
  updateTaskAvailability();
  $("#confirmation-count").textContent = drafts.drafts.filter((draft) => ["DRAFT", "CONFIRMED"].includes(draft.status)).length;
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
    const badge = document.createElement("span"); badge.className = `badge ${stateStyle(draft.status)}`; badge.textContent = stateLabel(draft.status);
    const title = document.createElement("strong"); title.textContent = `${draft.account_slot} · ${draft.action_type}`;
    const content = document.createElement("p"); content.textContent = draft.content;
    card.append(badge, title, content);
    if (["DRAFT", "CONFIRMED"].includes(draft.status)) {
      const confirm = document.createElement("button"); confirm.className = "primary-button"; confirm.type = "button"; confirm.textContent = draft.status === "CONFIRMED" ? "重新核对并执行" : "核对并确认";
      confirm.addEventListener("click", () => confirmDraft(draft, card, confirm));
      card.append(confirm);
    }
    container.append(card);
  });
}

async function submitDraft(event) {
  event.preventDefault();
  const account = $("#draft-account").value;
  const message = $("#draft-message");
  const button = $("#draft-submit");
  if (!account) { message.textContent = "请先配置账号槽位。"; return; }
  button.disabled = true;
  button.textContent = "保存中…";
  try {
    const accountStatus = await fetchJson(`${api}/accounts/${encodeURIComponent(account)}/status`);
    const uid = accountStatus.identity?.user_id;
    if (!uid) throw new Error("请先在账号卡片中确认 UID。");
    await mutateJson(`${api}/drafts`, "POST", { account_slot: account, verified_uid: uid, action_type: $("#draft-action").value, target_id: $("#draft-target").value.trim(), target_summary: $("#draft-summary").value.trim(), content: $("#draft-content").value.trim() });
    message.textContent = "草稿已保存在本机，尚未发送。";
    event.target.reset();
    await loadDashboard();
    showTaskPanel("confirmation");
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
    setActionButtonLabel(button, "保存为待确认草稿");
  }
}

async function confirmDraft(draft, card, button) {
  const feedId = window.prompt("请输入目标笔记 ID（回复评论也需要）：", draft.action_type === "post-comment" ? draft.target_id : "");
  if (!feedId) return;
  const token = window.prompt("请输入该笔记当前的 XSEC Token：", "");
  if (!token) return;
  const accepted = window.confirm(`目标账号：${draft.account_slot}\nUID：${draft.verified_uid}\n目标：${draft.target_summary || draft.target_id}\n\n最终文本：\n${draft.content}\n\n确认后将立即执行，是否继续？`);
  if (!accepted) return;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "执行中…";
  try {
    const confirmed = await mutateJson(`${api}/drafts/${draft.draft_id}/confirm`, "POST", {});
    const result = await mutateJson(`${api}/drafts/${draft.draft_id}/execute`, "POST", { approval_id: confirmed.approval.approval_id, feed_id: feedId, comment_id: draft.action_type === "reply-comment" ? draft.target_id : "", xsec_token: token });
    card.querySelector("p").textContent = result.task.result_summary || result.task.state;
    await loadDashboard();
  } catch (error) {
    card.querySelector("p").textContent = error.message;
    button.disabled = false;
    button.textContent = original;
  }
}

async function submitTask(event) {
  event.preventDefault();
  const button = $("#task-submit");
  const account = $("#task-account").value;
  if (!accountStatusByName.get(account)?.ready) {
    $("#task-message").textContent = "目标账号尚未 READY，任务没有创建。";
    return;
  }
  const capability = $("#task-capability").value;
  const target = $("#task-target").value.trim();
  const token = $("#task-token").value.trim();
  const durationMinutes = Number($("#task-duration").value);
  const browseCount = Number($("#task-count").value);
  const engagementAction = $("#task-engagement-action").value;
  const engagementCount = Number($("#task-engagement-count").value);
  const candidatePoolSize = Number($("#task-candidate-pool").value);
  const collectionMinutes = Number($("#task-collection-minutes").value);
  const parameters = capability === "browse-feeds"
    ? { duration_minutes: durationMinutes, count: browseCount }
    : capability === "search-feeds"
      ? { keyword: target }
      : capability === "keyword-engagement"
        ? { keyword: target, action: engagementAction, count: engagementCount, candidate_pool_size: candidatePoolSize, collection_minutes: collectionMinutes }
      : capability === "list-feeds"
        ? {}
        : capability === "user-profile"
          ? { user_id: target, xsec_token: token }
          : { feed_id: target, xsec_token: token };
  const message = $("#task-message");
  const capabilityName = taskCapabilityLabel(capability);
  const requestSummary = capability === "browse-feeds"
    ? `${capabilityName}：${durationMinutes} 分钟 / ${browseCount} 篇`
    : capability === "keyword-engagement"
      ? `${capabilityName}：${target} / 抽取 ${engagementCount} 篇 / 候选池 ${candidatePoolSize} 篇`
    : target ? `${capabilityName}：${target}` : capabilityName;
  button.disabled = true;
  button.textContent = "执行中…";
  try {
    const created = await mutateJson(`${api}/tasks`, "POST", { source: "webui", account_slot: account, capability, request_summary: requestSummary, parameters });
    const executed = await mutateJson(`${api}/tasks/${created.task.task_id}/execute`, "POST", {});
    message.textContent = executed.task.result_summary || executed.task.recommended_action || executed.task.state;
    await loadDashboard();
  } catch (error) {
    message.textContent = error.message;
  } finally {
    setActionButtonLabel(button, "创建并执行");
    updateTaskAvailability();
  }
}

function setActionButtonLabel(button, label) {
  const arrow = document.createElement("span");
  arrow.setAttribute("aria-hidden", "true");
  arrow.textContent = "→";
  button.replaceChildren(document.createTextNode(label), arrow);
}

function updateTaskFields() {
  const capability = $("#task-capability").value;
  const config = taskCapabilities[capability] || taskCapabilities["browse-feeds"];
  const target = $("#task-target");
  target.required = config.targetRequired;
  target.placeholder = config.placeholder;
  $("#task-target-field").hidden = !config.targetVisible;
  $("#task-target-label").textContent = config.targetLabel;
  $("#task-capability-help").textContent = config.help;
  $("#browse-settings").hidden = !config.browseSettings;
  $("#engagement-settings").hidden = !config.engagementSettings;
  $("#token-field").hidden = !config.tokenRequired;
  $("#task-token").required = config.tokenRequired;
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
  refresh.setAttribute("aria-busy", "true");
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

    grid.replaceChildren();
    const statuses = await Promise.all(
      accountData.accounts.map(async (account) => {
        try {
          return await fetchJson(`${api}/accounts/${encodeURIComponent(account.name)}/status`);
        } catch (error) {
          return { status: "ERROR", next_action: error.message };
        }
      })
    );
    await loadWorkData(accountData, statuses);
    if (accountData.accounts.length === 0) grid.append(emptyAccounts());
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
    refresh.removeAttribute("aria-busy");
  }
}

function setupNavigation() {
  const links = Array.from(document.querySelectorAll(".nav-item[href^='#']"));
  const sections = links
    .map((link) => document.querySelector(link.getAttribute("href")))
    .filter(Boolean);
  if (!("IntersectionObserver" in window)) return;
  const observer = new IntersectionObserver((entries) => {
    const visible = entries
      .filter((entry) => entry.isIntersecting)
      .sort((left, right) => right.intersectionRatio - left.intersectionRatio)[0];
    if (!visible) return;
    links.forEach((link) => {
      const active = link.getAttribute("href") === `#${visible.target.id}`;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
  }, { rootMargin: "-20% 0px -65%", threshold: [0, .2, .6] });
  sections.forEach((section) => observer.observe(section));
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
$("#task-account").addEventListener("change", updateTaskAvailability);
$("#task-template").addEventListener("change", () => {
  $("#task-target").value = "";
  $("#task-token").value = "";
  $("#task-engagement-action").value = "like";
  $("#task-engagement-count").value = "3";
  $("#task-candidate-pool").value = "20";
  $("#task-collection-minutes").value = "2";
  renderTaskTemplateActions();
});
$("#task-capability").addEventListener("change", updateTaskFields);
$("#task-engagement-count").addEventListener("input", (event) => {
  const pool = $("#task-candidate-pool");
  const count = Math.max(1, Number(event.target.value) || 1);
  pool.min = String(count);
  if (Number(pool.value) < count) pool.value = String(count);
});
$("#draft-form").addEventListener("submit", submitDraft);
$("#account-mode").addEventListener("change", (event) => {
  $("#profile-field").hidden = event.target.value !== "existing";
  $("#discover-profiles").hidden = event.target.value !== "existing";
});
renderTaskTemplateActions();
setupTaskPanels();
setupNavigation();
loadDashboard();
