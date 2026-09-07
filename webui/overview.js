(() => {
  "use strict";

  const { api, fetchJson, mutateJson } = window.AutoXhsApi;
  const DASHBOARD_REFRESH_INTERVAL_MS = 30_000;
  const ACTIVE_STATES = new Set([
    "ACCEPTED",
    "DISPATCHING",
    "PENDING_DISPATCH",
    "PLANNING",
    "PREPARING",
    "QUEUED",
    "RUNNING",
    "WAITING_AGENT",
  ]);
  const EXCEPTION_STATES = new Set([
    "BLOCKED",
    "CHECK_UNKNOWN",
    "DISPATCH_UNKNOWN",
    "FAILED",
    "OFFLINE",
    "RECONCILIATION_REQUIRED",
    "RESULT_UNKNOWN",
    "UNKNOWN",
  ]);
  const SUCCESS_STATES = new Set(["COMPLETED", "PARTIAL_SUCCESS", "SUCCESS"]);
  const PUBLISH_CAPABILITIES = new Set([
    "fill-publish",
    "fill-publish-video",
    "long-article",
  ]);
  const MODULES = [
    { id: "discovery", name: "内容浏览", icon: "览" },
    { id: "interaction", name: "评论互动", icon: "评" },
    { id: "publish", name: "内容发布", icon: "发" },
    { id: "messages", name: "私信回复", icon: "信" },
    { id: "replies", name: "评论回复", icon: "回" },
  ];
  const LABELS = {
    ACCEPTED: "已接收",
    BLOCKED: "已阻塞",
    CHECK_UNKNOWN: "检查待确认",
    COMPLETED: "已完成",
    DISPATCHING: "正在下发",
    DISPATCH_UNKNOWN: "派发待确认",
    FAILED: "失败",
    IDLE: "空闲",
    OFFLINE: "离线",
    ONLINE: "在线",
    PARTIAL_SUCCESS: "部分完成",
    PAUSED: "已暂停",
    PENDING_DISPATCH: "等待派发",
    PLANNING: "规划中",
    PREPARING: "准备中",
    QUEUED: "排队中",
    READY: "可运行",
    RECONCILIATION_REQUIRED: "需要对账",
    RESULT_UNKNOWN: "结果待确认",
    RUNNING: "执行中",
    SUCCESS: "成功",
    UNKNOWN: "状态未知",
    WARNING: "需要确认",
    WAITING_AGENT: "等待Agent",
    WAITING_APPROVAL: "等待确认",
  };

  const state = {
    snapshot: {},
    machines: [],
    accounts: [],
    tasks: [],
    results: [],
    alerts: [],
    localTasks: [],
    selectedMachineId: localStorage.getItem("auto-xhs:selected-node") || "",
    selectedTab: localStorage.getItem("auto-xhs:selected-tab") || "accounts",
    activeAccount: null,
    activePublishTask: null,
    lastUpdated: "",
  };

  let dashboardRefreshPromise = null;
  let dashboardRefreshQueued = false;
  let dashboardRefreshTimer = null;

  function $(selector) {
    return document.querySelector(selector);
  }

  function el(tag, className = "", text = "") {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== "") node.textContent = String(text);
    return node;
  }

  function append(parent, ...children) {
    children.filter(Boolean).forEach((child) => parent.appendChild(child));
    return parent;
  }

  function setText(selector, value) {
    const node = $(selector);
    if (node) node.textContent = String(value ?? "—");
  }

  function upper(value, fallback = "UNKNOWN") {
    return String(value || fallback).trim().toUpperCase();
  }

  function label(value) {
    const normalized = upper(value);
    return LABELS[normalized] || normalized;
  }

  function tone(value) {
    const normalized = upper(value);
    if (["READY", "SUCCESS", "ONLINE", "COMPLETED"].includes(normalized)) {
      return "success";
    }
    if (["RUNNING", "ACCEPTED", "DISPATCHING", "PLANNING"].includes(normalized)) {
      return "info";
    }
    if (
      [
        "WARNING",
        "WAITING_APPROVAL",
        "PENDING_DISPATCH",
        "PAUSED",
        "CHECK_UNKNOWN",
        "DISPATCH_UNKNOWN",
        "RECONCILIATION_REQUIRED",
        "RESULT_UNKNOWN",
        "UNKNOWN",
      ].includes(normalized)
    ) {
      return "warning";
    }
    if (["BLOCKED", "FAILED", "OFFLINE"].includes(normalized)) return "danger";
    return "neutral";
  }

  function badge(value) {
    const node = el("span", "status-badge", label(value));
    node.dataset.tone = tone(value);
    return node;
  }

  function formatTime(value, short = false) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    if (short) {
      return date.toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
    }
    return date.toLocaleString("zh-CN", { hour12: false });
  }

  function machineStatus(machine) {
    if (machine.stale) return "UNKNOWN";
    if (upper(machine.ssh_state) !== "ONLINE") return upper(machine.ssh_state, "OFFLINE");
    return upper(machine.status, "UNKNOWN");
  }

  function machineOnline(machine) {
    return upper(machine.ssh_state) === "ONLINE" && !machine.stale;
  }

  function accountsForMachine(machineId) {
    return state.accounts.filter((account) => account.machine_id === machineId);
  }

  function tasksForMachine(machineId) {
    return state.tasks.filter((task) => {
      return (task.machine_id || task.target_machine_id) === machineId;
    });
  }

  function resultsForMachine(machineId) {
    return state.results.filter((result) => result.machine_id === machineId);
  }

  function alertsForMachine(machineId) {
    return state.alerts.filter((alert) => alert.machine_id === machineId && alert.active !== false);
  }

  function unresolvedResultIssues(machineId = "") {
    const results = machineId ? resultsForMachine(machineId) : state.results;
    return results.filter((result) => {
      return (
        result.manual_action_required === true ||
        ["DISPATCH_UNKNOWN", "RECONCILIATION_REQUIRED", "RESULT_UNKNOWN"].includes(
          upper(result.state)
        )
      );
    });
  }

  function resultTime(result) {
    return result.recorded_at || result.collected_at || result.created_at || "";
  }

  function taskTime(task) {
    return task.updated_at || task.created_at || task.acknowledged_at || "";
  }

  function initials(value) {
    const text = String(value || "—").trim();
    return text.slice(0, 2).toUpperCase();
  }

  function requestedCapability(task) {
    return task.capability || task.request?.capability || task.function_id || "未知能力";
  }

  function moduleIdsForCapability(value) {
    const capability = String(value || "").toLowerCase();
    if (!capability) return [];
    if (capability === "home-engagement" || capability === "keyword-engagement") {
      return ["discovery", "interaction"];
    }
    if (
      capability.includes("private-message") ||
      capability.includes("message") ||
      capability.includes("conversation") ||
      capability.includes("inbox") ||
      capability.includes("dm-")
    ) {
      return ["messages"];
    }
    if (
      capability.includes("reply-comment") ||
      capability.includes("comment-reply") ||
      capability.includes("note-comment") ||
      capability.includes("reply")
    ) {
      return ["replies"];
    }
    if (
      capability.includes("publish") ||
      capability.includes("article") ||
      capability.includes("save-draft")
    ) {
      return ["publish"];
    }
    if (
      capability.includes("like") ||
      capability.includes("favorite") ||
      capability.includes("comment") ||
      capability.includes("follow") ||
      capability.includes("interact")
    ) {
      return ["interaction"];
    }
    if (
      capability.includes("feed") ||
      capability.includes("search") ||
      capability.includes("browse") ||
      capability.includes("profile") ||
      capability.includes("collect")
    ) {
      return ["discovery"];
    }
    return [];
  }

  function moduleForMetric(value) {
    const key = String(value || "").toLowerCase();
    if (
      key.includes("private_message") ||
      key.includes("private-message") ||
      key.includes("message") ||
      key.includes("conversation") ||
      key.includes("contact") ||
      key.includes("dm")
    ) {
      return "messages";
    }
    if (key.includes("reply")) return "replies";
    if (key.includes("publish") || key.includes("post") || key.includes("article")) {
      return "publish";
    }
    if (
      key.includes("like") ||
      key.includes("favorite") ||
      key.includes("comment") ||
      key.includes("follow") ||
      key.includes("interact")
    ) {
      return "interaction";
    }
    if (
      key.includes("browse") ||
      key.includes("search") ||
      key.includes("read") ||
      key.includes("feed") ||
      key.includes("discover")
    ) {
      return "discovery";
    }
    return "";
  }

  function emptyBusinessStats() {
    return Object.fromEntries(
      MODULES.map((module) => [
        module.id,
        {
          target: 0,
          hasTarget: false,
          actual: 0,
          hasActual: false,
          success: 0,
          exception: 0,
          recent: null,
        },
      ])
    );
  }

  function successfulItemCount(result) {
    const items = result.item_results;
    if (Array.isArray(items)) {
      return items.filter((item) => {
        if (!item || typeof item !== "object") return false;
        if (item.success === true) return true;
        if (upper(item.status, "") === "SUCCESS") return true;
        return Boolean(item.browse || item.like || item.favorite || item.comment || item.reply);
      }).length;
    }
    if (items && typeof items === "object") {
      return items.success === false || upper(items.status, "") === "FAILED" ? 0 : 1;
    }
    return SUCCESS_STATES.has(upper(result.state)) ? 1 : 0;
  }

  function addResultActuals(stats, result, modules) {
    const items = Array.isArray(result.item_results) ? result.item_results : [];
    const capability = String(result.capability || result.task_request?.capability || "");
    if (capability === "home-engagement" && items.length) {
      const browseCount = items.filter((item) => item?.browse).length;
      const interactionCount = items.filter((item) => {
        return item?.like || item?.favorite || item?.comment;
      }).length;
      stats.discovery.actual += browseCount;
      stats.discovery.hasActual = true;
      stats.interaction.actual += interactionCount;
      stats.interaction.hasActual = true;
      return;
    }
    const count = successfulItemCount(result);
    modules.forEach((moduleId) => {
      stats[moduleId].actual += count;
      stats[moduleId].hasActual = true;
    });
  }

  function businessStats(machineId = "") {
    const stats = emptyBusinessStats();
    const tasks = machineId ? tasksForMachine(machineId) : state.tasks;
    const results = machineId ? resultsForMachine(machineId) : state.results;
    const seenGoals = new Set();

    tasks.forEach((task) => {
      const request = task.request || {};
      const goal = request.goal_snapshot;
      const goalKey = `${task.machine_id || task.target_machine_id}:${request.goal_id || goal?.goal_id || ""}:${goal?.version || ""}`;
      if (!goal || seenGoals.has(goalKey)) return;
      seenGoals.add(goalKey);
      const targetMetrics = goal.target_metrics || {};
      Object.entries(targetMetrics).forEach(([key, value]) => {
        const moduleId = moduleForMetric(key);
        const numeric = Number(value);
        if (!moduleId || !Number.isFinite(numeric)) return;
        stats[moduleId].target += numeric;
        stats[moduleId].hasTarget = true;
      });
    });

    results.forEach((result) => {
      const modules = moduleIdsForCapability(
        result.capability || result.task_request?.capability || result.function_id
      );
      if (!modules.length) return;
      addResultActuals(stats, result, modules);
      const resultState = upper(result.state);
      modules.forEach((moduleId) => {
        if (SUCCESS_STATES.has(resultState)) stats[moduleId].success += 1;
        if (EXCEPTION_STATES.has(resultState)) stats[moduleId].exception += 1;
        const recent = stats[moduleId].recent;
        if (!recent || new Date(resultTime(result)) > new Date(resultTime(recent))) {
          stats[moduleId].recent = result;
        }
      });
    });
    return stats;
  }

  function pendingPublishTasks() {
    return state.localTasks.filter((task) => {
      return (
        PUBLISH_CAPABILITIES.has(task.capability) &&
        upper(task.state) === "WAITING_APPROVAL" &&
        task.parameters?.stage === "preview_ready"
      );
    });
  }

  function machineIdForPublishTask(task) {
    const explicit = task.machine_id || task.parameters?.machine_id;
    if (explicit && state.machines.some((machine) => machine.machine_id === explicit)) {
      return explicit;
    }
    const matches = state.accounts.filter((account) => account.account_slot === task.account_slot);
    return matches.length === 1 ? matches[0].machine_id : "";
  }

  function pendingPublishesForMachine(machineId) {
    return pendingPublishTasks().filter((task) => machineIdForPublishTask(task) === machineId);
  }

  function machinePriority(machine) {
    const machineId = machine.machine_id;
    if (!machineOnline(machine)) return 0;
    if (accountsForMachine(machineId).some((account) => upper(account.health_state) === "BLOCKED")) {
      return 1;
    }
    if (
      resultsForMachine(machineId).some((result) => EXCEPTION_STATES.has(upper(result.state)))
    ) {
      return 2;
    }
    if (tasksForMachine(machineId).some((task) => ACTIVE_STATES.has(upper(task.state)))) {
      return 3;
    }
    return 4;
  }

  function sortedMachines() {
    return [...state.machines].sort((left, right) => {
      return machinePriority(left) - machinePriority(right) ||
        String(left.machine_id).localeCompare(String(right.machine_id));
    });
  }

  function renderServiceState(healthy, message = "") {
    const dot = $("#manager-service-dot");
    if (dot) dot.dataset.tone = healthy ? "success" : "danger";
    setText("#manager-service-state", healthy ? "经理机服务正常" : "经理机连接异常");
    setText("#auto-refresh-state", message || (healthy ? "每30秒读取缓存" : "保留最后一次数据"));
  }

  function renderGlobalStatus() {
    const online = state.machines.filter(machineOnline).length;
    const readyAccounts = state.accounts.filter((account) => {
      return upper(account.health_state) === "READY" && !account.stale;
    }).length;
    const activeTasks = state.tasks.filter((task) => ACTIVE_STATES.has(upper(task.state))).length;
    const accountExceptions = state.accounts.filter((account) => {
      return upper(account.health_state) !== "READY" || account.stale;
    }).length;
    const taskExceptions = unresolvedResultIssues().length;
    const stats = businessStats();
    const target = Object.values(stats).reduce((sum, item) => sum + item.target, 0);
    const actual = Object.values(stats).reduce((sum, item) => sum + item.actual, 0);
    const hasTarget = Object.values(stats).some((item) => item.hasTarget);
    const hasActual = Object.values(stats).some((item) => item.hasActual);
    const exceptionTotal = accountExceptions + taskExceptions + state.alerts.length;

    setText("#global-machine-total", `${online} / ${state.machines.length}`);
    setText("#global-ready-accounts", `${readyAccounts} / ${state.accounts.length}`);
    setText("#global-active-tasks", activeTasks);
    setText(
      "#global-target-actual",
      `${hasTarget ? target : "—"} / ${hasActual ? actual : "—"}`
    );
    setText("#global-publish-pending", pendingPublishTasks().length);
    setText("#global-exceptions", exceptionTotal);

    const hasMachines = state.machines.length > 0;
    const healthy = hasMachines && online === state.machines.length && exceptionTotal === 0;
    const heroBadge = $("#hero-health-value");
    setText(
      "#page-title",
      healthy ? "全部员工机运行正常" : hasMachines ? `${exceptionTotal} 项需要关注` : "等待员工机状态"
    );
    setText("#hero-health-value", healthy ? "正常" : hasMachines ? `${exceptionTotal} 项` : "等待数据");
    if (heroBadge) {
      heroBadge.dataset.tone = healthy ? "success" : hasMachines ? "warning" : "neutral";
    }
    setText("#hero-health-detail", `${online} 台在线，${readyAccounts} 个账号可运行`);
  }

  function nodeCard(machine) {
    const machineId = machine.machine_id;
    const accounts = accountsForMachine(machineId);
    const ready = accounts.filter((account) => upper(account.health_state) === "READY").length;
    const active = tasksForMachine(machineId).filter((task) => {
      return ACTIVE_STATES.has(upper(task.state));
    }).length;
    const exceptions = accounts.filter((account) => upper(account.health_state) !== "READY").length +
      alertsForMachine(machineId).length + unresolvedResultIssues(machineId).length;
    const button = el("button", "node-card");
    button.type = "button";
    button.dataset.machineId = machineId;
    button.setAttribute("role", "listitem");
    button.setAttribute("aria-pressed", String(machineId === state.selectedMachineId));
    if (machineId === state.selectedMachineId) button.classList.add("active");

    const heading = el("div", "node-card-head");
    const title = el("div", "node-card-title");
    const avatar = el("span", "node-avatar", initials(machine.host_alias || machineId));
    const copy = el("div");
    append(copy, el("strong", "", machine.host_alias || machineId), el("small", "", machineId));
    append(title, avatar, copy);
    append(heading, title, badge(machineStatus(machine)));

    const stats = el("div", "node-card-stat");
    [["账号", `${ready}/${accounts.length}`], ["运行", active], ["异常", exceptions]].forEach(
      ([name, value]) => {
        const item = el("div");
        append(item, el("span", "", name), el("strong", "", value));
        stats.appendChild(item);
      }
    );
    const foot = el("div", "node-card-foot");
    append(
      foot,
      el("span", "", `同步 ${formatTime(machine.last_seen_at || machine.updated_at, true)}`),
      el("span", "", "查看节点 →")
    );
    return append(button, heading, stats, foot);
  }

  function renderNodes() {
    const list = $("#node-list");
    const empty = $("#node-empty");
    setText("#node-rail-count", `${state.machines.length} 个`);
    list.replaceChildren();
    sortedMachines().forEach((machine) => list.appendChild(nodeCard(machine)));
    empty.hidden = state.machines.length > 0;
    list.hidden = state.machines.length === 0;
  }

  function currentMachine() {
    return state.machines.find((machine) => machine.machine_id === state.selectedMachineId) || null;
  }

  function renderNodeContext() {
    const machine = currentMachine();
    const workspace = $("#node-workspace");
    if (!machine) {
      workspace.hidden = true;
      return;
    }
    workspace.hidden = false;
    const accounts = accountsForMachine(machine.machine_id);
    const ready = accounts.filter((account) => upper(account.health_state) === "READY").length;
    const active = tasksForMachine(machine.machine_id).filter((task) => {
      return ACTIVE_STATES.has(upper(task.state));
    }).length;
    setText("#selected-node-avatar", initials(machine.host_alias || machine.machine_id));
    setText("#selected-node-title", machine.host_alias || machine.machine_id);
    setText(
      "#selected-node-description",
      `${machine.machine_id} · 项目版本 ${machine.installed_version || "未回读"}`
    );
    setText("#selected-node-status", label(machineStatus(machine)));
    setText("#selected-node-accounts", `${ready} / ${accounts.length} READY`);
    setText("#selected-node-tasks", active);
    setText("#selected-node-sync", formatTime(machine.last_seen_at || machine.updated_at, true));
  }

  function signalState(account, kind) {
    const values = {
      profile: account.profile_status,
      extension: account.extension_status,
      bridge: account.bridge_status,
    };
    return upper(values[kind] || account.health_state);
  }

  function identityState(account) {
    const identity = account.identity || {};
    if (identity.matches_record === true || (account.uid && account.connection_ready)) return "READY";
    if (identity.matches_record === false) return "BLOCKED";
    return "UNKNOWN";
  }

  function signalLabel(value) {
    const normalized = upper(value);
    if (normalized === "READY") return "正常";
    if (normalized === "BLOCKED") return "异常";
    if (normalized === "WARNING") return "待确认";
    return "未知";
  }

  function accountProfile(account) {
    const snapshot = account.raw_snapshot || {};
    return {
      name: snapshot.profile_display_name || snapshot.chrome_profile_directory || "固定Profile",
      directory: snapshot.chrome_profile_directory || "目录未回读",
    };
  }

  function taskForAccount(account) {
    return tasksForMachine(account.machine_id).find((task) => {
      const slot = task.account_slot || task.request?.account_slot;
      return slot === account.account_slot && ACTIVE_STATES.has(upper(task.state));
    });
  }

  function latestResultForAccount(account) {
    return resultsForMachine(account.machine_id)
      .filter((result) => {
        return (result.account_slot || result.task_request?.account_slot) === account.account_slot;
      })
      .sort((left, right) => new Date(resultTime(right)) - new Date(resultTime(left)))[0];
  }

  function accountSignal(name, value) {
    const node = el("div", "account-signal");
    node.dataset.tone = tone(value);
    return append(node, el("span", "", name), el("strong", "", signalLabel(value)));
  }

  function accountBinding(name, value, detail) {
    const node = el("div", "account-binding-row");
    const copy = el("div");
    append(copy, el("strong", "", value || "—"), el("small", "", detail || "—"));
    return append(node, el("span", "", name), copy);
  }

  function accountActivity(labelText, title, detail, stateValue) {
    const node = el("div", "account-activity");
    node.dataset.tone = tone(stateValue);
    const copy = el("div");
    append(copy, el("span", "", labelText), el("strong", "", title), el("small", "", detail));
    return append(node, el("i"), copy, badge(stateValue));
  }

  function accountCard(account) {
    const card = el("article", "account-card");
    const health = upper(account.health_state);
    const profile = accountProfile(account);
    const task = taskForAccount(account);
    const recent = latestResultForAccount(account);
    card.dataset.state = health;

    const head = el("div", "account-card-head");
    const identity = el("div", "account-card-identity");
    const identityCopy = el("div");
    append(
      identityCopy,
      el("h4", "", account.nickname || account.name || account.account_slot),
      el("p", "", `小红书 · ${profile.directory} · ${account.account_slot}`)
    );
    append(
      identity,
      el("span", "account-avatar", initials(account.nickname || account.account_slot)),
      identityCopy
    );
    append(head, identity, badge(health));

    const signals = el("div", "account-signals");
    append(
      signals,
      accountSignal("PROFILE", signalState(account, "profile")),
      accountSignal("扩展", signalState(account, "extension")),
      accountSignal("BRIDGE", signalState(account, "bridge")),
      accountSignal("UID", identityState(account))
    );

    const bindings = el("div", "account-bindings");
    append(
      bindings,
      accountBinding("固定 Profile", profile.name, profile.directory),
      accountBinding(
        "当前小红书账号",
        account.nickname || account.identity?.nickname || "尚未核验",
        account.uid ? `UID ${account.uid}` : "完成核验后显示UID"
      )
    );

    const activities = el("div", "account-activities");
    append(
      activities,
      accountActivity(
        "当前Agent任务",
        task ? task.request_summary || requestedCapability(task) : "当前没有运行中的任务",
        task
          ? `${requestedCapability(task)} · ${formatTime(taskTime(task), true)}`
          : `最后同步 ${formatTime(account.last_seen_at || account.updated_at, true)}`,
        task?.state || "IDLE"
      ),
      accountActivity(
        "最近结果",
        recent?.result_summary || recent?.message || "暂无已回收结果",
        recent
          ? `${requestedCapability(recent.task_request || recent)} · ${formatTime(resultTime(recent), true)}`
          : "等待Agent回收结果",
        recent?.state || "UNKNOWN"
      )
    );

    const guidance = el(
      "p",
      "account-guidance",
      account.abnormal_reason || account.last_error ||
        (health === "READY"
          ? `当前身份：${account.nickname || account.uid || "已核验"}`
          : account.recommended_action || "查看状态并按建议恢复账号连接")
    );
    const actions = el("div", "account-card-actions");
    const action = el(
      "button",
      `account-primary-action ${health === "READY" ? "ready" : "recovery"}`,
      health === "READY" ? "查看账号状态" : "恢复账号"
    );
    action.type = "button";
    action.dataset.accountKey = account.account_key;
    actions.appendChild(action);
    return append(card, head, signals, bindings, activities, guidance, actions);
  }

  function renderAccounts() {
    const accounts = accountsForMachine(state.selectedMachineId);
    const grid = $("#account-list");
    grid.replaceChildren();
    accounts.forEach((account) => grid.appendChild(accountCard(account)));
    $("#account-empty").hidden = accounts.length > 0;
    grid.hidden = accounts.length === 0;
    const ready = accounts.filter((account) => upper(account.health_state) === "READY").length;
    const blocked = accounts.filter((account) => upper(account.health_state) === "BLOCKED").length;
    setText("#account-summary", `${ready} READY · ${blocked} BLOCKED · 共 ${accounts.length} 个账号`);
  }

  function businessCard(module, stats, publishTasks) {
    const card = el("article", "business-card");
    const head = el("div", "business-card-head");
    append(head, el("span", "business-icon", module.icon), badge(stats.exception ? "WARNING" : "READY"));
    append(card, head, el("h4", "", module.name));
    const numbers = el("div", "business-numbers");
    [
      ["目标", stats.hasTarget ? stats.target : "—"],
      ["实际", stats.hasActual ? stats.actual : "—"],
      ["成功", stats.success],
      ["异常", stats.exception],
    ].forEach(([name, value]) => {
      const item = el("div");
      append(item, el("span", "", name), el("strong", "", value));
      numbers.appendChild(item);
    });
    card.appendChild(numbers);
    const recent = el("p", "business-last");
    append(
      recent,
      document.createTextNode("最近结果"),
      el(
        "strong",
        "",
        stats.recent?.result_summary || stats.recent?.message || "暂无已回收结果"
      )
    );
    card.appendChild(recent);
    if (module.id === "publish" && publishTasks.length) {
      const button = el("button", "table-action publish-entry", `处理 ${publishTasks.length} 项发布确认`);
      button.type = "button";
      button.dataset.openPublish = publishTasks[0].task_id;
      card.appendChild(button);
    }
    return card;
  }

  function renderBusiness() {
    const stats = businessStats(state.selectedMachineId);
    const publishTasks = pendingPublishesForMachine(state.selectedMachineId);
    const grid = $("#business-grid");
    grid.replaceChildren();
    MODULES.forEach((module) => {
      grid.appendChild(businessCard(module, stats[module.id], publishTasks));
    });
    const target = Object.values(stats).reduce((sum, item) => sum + item.target, 0);
    const actual = Object.values(stats).reduce((sum, item) => sum + item.actual, 0);
    const hasTarget = Object.values(stats).some((item) => item.hasTarget);
    const hasActual = Object.values(stats).some((item) => item.hasActual);
    setText(
      "#business-summary",
      `目标 ${hasTarget ? target : "—"} · 实际 ${hasActual ? actual : "—"}`
    );
  }

  function taskCard(item, kind) {
    const button = el("button", "record-card");
    button.type = "button";
    button.dataset.taskDetailKind = kind;
    button.dataset.taskDetailId = kind === "task" ? item.task_id : item.record_id || item.task_id;
    const copy = el("div");
    const title = kind === "task"
      ? item.request?.request_summary || item.message || requestedCapability(item)
      : item.result_summary || item.message || item.task_id;
    const account = item.account_slot || item.request?.account_slot || item.task_request?.account_slot || "未标注账号";
    const capability = kind === "task" ? requestedCapability(item) : item.capability || "未知能力";
    append(
      copy,
      el("strong", "", title),
      el("small", "", `${account} · ${capability} · ${formatTime(kind === "task" ? taskTime(item) : resultTime(item), true)}`)
    );
    append(button, copy, badge(item.state || item.business_state));
    return button;
  }

  function renderRecordList(container, records, kind, emptyText) {
    container.replaceChildren();
    if (!records.length) {
      container.appendChild(el("p", "empty-inline", emptyText));
      return;
    }
    records.forEach((record) => container.appendChild(taskCard(record, kind)));
  }

  function renderTasks() {
    const tasks = tasksForMachine(state.selectedMachineId);
    const results = [...resultsForMachine(state.selectedMachineId)].sort((left, right) => {
      return new Date(resultTime(right)) - new Date(resultTime(left));
    });
    const active = tasks
      .filter((task) => ACTIVE_STATES.has(upper(task.state || task.business_state)))
      .sort((left, right) => new Date(taskTime(right)) - new Date(taskTime(left)))
      .slice(0, 12);
    const recent = results.slice(0, 12);
    const exceptions = tasks.filter((task) => EXCEPTION_STATES.has(upper(task.state))).length;
    setText("#task-summary", `${active.length} 运行中 · ${recent.length} 条最近结果 · ${exceptions} 异常`);
    setText("#active-task-count", active.length);
    setText("#recent-result-count", recent.length);
    renderRecordList($("#active-task-list"), active, "task", "当前没有运行中的Agent任务");
    renderRecordList($("#recent-result-list"), recent, "result", "该节点还没有回收结果");
  }

  function alertCard(item) {
    const account = item.account || item.account_slot;
    const card = el(account ? "button" : "article", "record-card");
    if (account) {
      card.type = "button";
      const match = accountsForMachine(state.selectedMachineId).find((candidate) => {
        return candidate.account_slot === account;
      });
      if (match) card.dataset.accountKey = match.account_key;
    }
    const copy = el("div");
    append(
      copy,
      el("strong", "", item.message || item.abnormal_reason || item.result_summary || "需要关注"),
      el("small", "", `${account || item.task_id || "节点"} · ${item.recommended_action || "查看详情"}`)
    );
    append(card, copy, badge(item.severity || item.health_state || item.state || "WARNING"));
    return card;
  }

  function trendDays(results) {
    const days = [];
    const now = new Date();
    for (let offset = 6; offset >= 0; offset -= 1) {
      const date = new Date(now.getFullYear(), now.getMonth(), now.getDate() - offset);
      const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
      days.push({ key, label: `${date.getMonth() + 1}/${date.getDate()}`, success: 0, error: 0 });
    }
    const byKey = new Map(days.map((day) => [day.key, day]));
    results.forEach((result) => {
      const date = new Date(resultTime(result));
      if (Number.isNaN(date.getTime())) return;
      const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
      const day = byKey.get(key);
      if (!day) return;
      if (SUCCESS_STATES.has(upper(result.state))) day.success += 1;
      else if (EXCEPTION_STATES.has(upper(result.state))) day.error += 1;
    });
    return days;
  }

  function renderTrend(results) {
    const chart = $("#trend-chart");
    const days = trendDays(results);
    const maxValue = Math.max(1, ...days.flatMap((day) => [day.success, day.error]));
    chart.replaceChildren();
    days.forEach((day) => {
      const group = el("div", "trend-day");
      const bars = el("div", "trend-bars");
      const success = el("span", "trend-bar");
      success.style.height = `${Math.max(3, (day.success / maxValue) * 180)}px`;
      success.title = `${day.label} 成功 ${day.success}`;
      const error = el("span", "trend-bar error");
      error.style.height = `${Math.max(3, (day.error / maxValue) * 180)}px`;
      error.title = `${day.label} 异常 ${day.error}`;
      append(bars, success, error);
      append(group, bars, el("small", "", day.label));
      chart.appendChild(group);
    });
  }

  function renderInsights() {
    const accounts = accountsForMachine(state.selectedMachineId);
    const results = resultsForMachine(state.selectedMachineId);
    const accountIssues = accounts.filter((account) => {
      return upper(account.health_state) !== "READY" || account.stale;
    });
    const taskIssues = results.filter((result) => EXCEPTION_STATES.has(upper(result.state)));
    const alerts = [...alertsForMachine(state.selectedMachineId), ...accountIssues, ...taskIssues]
      .sort((left, right) => {
        const leftTime = left.updated_at || left.created_at || resultTime(left);
        const rightTime = right.updated_at || right.created_at || resultTime(right);
        return new Date(rightTime || 0) - new Date(leftTime || 0);
      })
      .slice(0, 16);
    const list = $("#alert-list");
    list.replaceChildren();
    if (!alerts.length) list.appendChild(el("p", "empty-inline", "当前节点没有需要关注的异常"));
    else alerts.forEach((item) => list.appendChild(alertCard(item)));
    setText("#alert-count", alerts.length);
    setText("#insight-summary", alerts.length ? `${alerts.length} 项需要关注` : "当前节点运行平稳");
    renderTrend(results);
  }

  function renderNodeWorkspace() {
    renderNodeContext();
    if (!currentMachine()) return;
    renderAccounts();
    renderBusiness();
    renderTasks();
    renderInsights();
    selectTab(state.selectedTab, false);
  }

  function renderAll() {
    renderGlobalStatus();
    renderNodes();
    renderNodeWorkspace();
  }

  function selectMachine(machineId, scroll = true) {
    if (!state.machines.some((machine) => machine.machine_id === machineId)) return;
    state.selectedMachineId = machineId;
    localStorage.setItem("auto-xhs:selected-node", machineId);
    renderNodes();
    renderNodeWorkspace();
    if (scroll) $("#node-workspace").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function selectTab(tabId, persist = true) {
    const allowed = new Set(["accounts", "business", "tasks", "insights"]);
    const next = allowed.has(tabId) ? tabId : "accounts";
    state.selectedTab = next;
    if (persist) localStorage.setItem("auto-xhs:selected-tab", next);
    document.querySelectorAll("[data-node-tab]").forEach((button) => {
      const active = button.dataset.nodeTab === next;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    document.querySelectorAll("[data-node-panel]").forEach((panel) => {
      const active = panel.dataset.nodePanel === next;
      panel.classList.toggle("active", active);
      panel.hidden = !active;
    });
  }

  function detailRow(term, value) {
    const item = el("div");
    append(item, el("dt", "", term), el("dd", "", value || "—"));
    return item;
  }

  function accountRecoveryActions(account, machine) {
    if (!machineOnline(machine)) return [];
    const actions = [];
    const bridgeReady = signalState(account, "bridge") === "READY";
    const profileReady = signalState(account, "profile") === "READY";
    const extensionReady = signalState(account, "extension") === "READY";
    const identityReady = identityState(account) === "READY";
    if (
      upper(account.health_state) === "READY" &&
      bridgeReady &&
      profileReady &&
      extensionReady &&
      identityReady
    ) {
      return [];
    }
    if (!bridgeReady) actions.push(["bridge-restart", "重启Bridge", true]);
    else if (!profileReady || !extensionReady) actions.push(["profile-start", "重新连接账号", true]);
    else if (!identityReady) actions.push(["identity-check", "重新核验UID", true]);
    else if (upper(account.health_state) !== "READY") actions.push(["doctor", "运行账号诊断", true]);
    if (!actions.some(([id]) => id === "doctor")) actions.push(["doctor", "运行账号诊断", false]);
    if (!actions.some(([id]) => id === "identity-check")) {
      actions.push(["identity-check", "重新核验UID", false]);
    }
    return actions;
  }

  function accountConnectionStep(name, value) {
    const node = el("div", "connection-step");
    append(node, el("span", "", name), el("strong", "", signalLabel(value)));
    node.dataset.tone = tone(value);
    return node;
  }

  function openAccountDialog(accountKey) {
    const account = state.accounts.find((item) => item.account_key === accountKey);
    const machine = state.machines.find((item) => item.machine_id === account?.machine_id);
    if (!account || !machine) return;
    state.activeAccount = account;
    setText("#account-dialog-title", account.nickname || account.name || account.account_slot);
    setText("#account-dialog-subtitle", `${machine.host_alias || machine.machine_id} · ${account.account_slot}`);
    const flow = $("#account-connection-flow");
    flow.replaceChildren(
      accountConnectionStep("Profile", signalState(account, "profile")),
      accountConnectionStep("扩展", signalState(account, "extension")),
      accountConnectionStep("Bridge", signalState(account, "bridge")),
      accountConnectionStep("UID", identityState(account))
    );
    const details = $("#account-identity-details");
    details.replaceChildren(
      detailRow("账号槽位", account.account_slot),
      detailRow("平台", account.platform || "xiaohongshu"),
      detailRow("当前昵称", account.nickname),
      detailRow("已核验UID", account.uid),
      detailRow("最后检查", formatTime(account.last_check_at)),
      detailRow("最后在线", formatTime(account.last_seen_at))
    );
    setText("#account-recovery-state", label(account.health_state));
    $("#account-recovery-state").dataset.tone = tone(account.health_state);
    setText(
      "#account-recovery-reason",
      account.abnormal_reason || account.last_error ||
        (upper(account.health_state) === "READY" ? "账号连接与身份状态正常。" : "根据当前故障提供恢复步骤。")
    );
    const actionList = $("#account-recovery-actions");
    actionList.replaceChildren();
    const actions = accountRecoveryActions(account, machine);
    if (!actions.length) {
      const message = machineOnline(machine)
        ? "账号状态正常，无需执行恢复操作。"
        : "员工机当前离线，恢复操作暂不可用。";
      actionList.appendChild(el("p", "empty-inline", message));
    } else {
      actions.forEach(([action, text, recommended]) => {
        const button = el(
          "button",
          `recovery-button ${recommended ? "recommended" : ""}`.trim(),
          text
        );
        button.type = "button";
        button.dataset.recoveryAction = action;
        actionList.appendChild(button);
      });
    }
    setText("#account-recovery-message", "");
    $("#account-recovery-dialog").showModal();
  }

  async function runAccountRecovery(action, button) {
    const account = state.activeAccount;
    if (!account) return;
    const actionLabels = {
      doctor: "运行账号诊断",
      "bridge-restart": "重启Bridge",
      "profile-start": "重新连接账号",
      "identity-check": "重新核验UID",
    };
    const actionLabel = actionLabels[action] || "执行恢复";
    if (!window.confirm(`将在员工机 ${account.machine_id} 对账号 ${account.account_slot} ${actionLabel}，确认继续？`)) {
      return;
    }
    const encodedMachine = encodeURIComponent(account.machine_id);
    const encodedAccount = encodeURIComponent(account.account_slot);
    const paths = {
      doctor: `${api}/manager/machines/${encodedMachine}/accounts/${encodedAccount}/doctor`,
      "bridge-restart": `${api}/manager/machines/${encodedMachine}/accounts/${encodedAccount}/bridge/restart`,
      "profile-start": `${api}/manager/machines/${encodedMachine}/accounts/${encodedAccount}/profile/start`,
      "identity-check": `${api}/manager/machines/${encodedMachine}/accounts/${encodedAccount}/identity/check`,
    };
    const path = paths[action];
    if (!path) return;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    setText("#account-recovery-message", `${actionLabel}中，正在等待员工机回读…`);
    try {
      const payload = await mutateJson(path, "POST", {});
      const accountPayload = payload.account || {};
      setText(
        "#account-recovery-message",
        payload.message || accountPayload.abnormal_reason || `${actionLabel}已完成，正在刷新状态。`
      );
      await loadDashboard("recovery");
      const refreshed = state.accounts.find((item) => item.account_key === account.account_key);
      if (refreshed) {
        $("#account-recovery-dialog").close();
        openAccountDialog(refreshed.account_key);
      }
    } catch (error) {
      setText("#account-recovery-message", error.message);
    } finally {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }

  function publishAssetName(value) {
    const parts = String(value || "").split(/[\\/]/).filter(Boolean);
    return parts[parts.length - 1] || "未命名素材";
  }

  function publishAccount(task) {
    const machineId = machineIdForPublishTask(task);
    return state.accounts.find((account) => {
      return account.machine_id === machineId && account.account_slot === task.account_slot;
    }) || null;
  }

  function openPublishDialog(taskId) {
    const task = pendingPublishTasks().find((item) => item.task_id === taskId);
    if (!task) return;
    state.activePublishTask = task;
    const preview = task.parameters?.preview || {};
    const account = publishAccount(task);
    const identity = account?.identity || {};
    const assets = Array.isArray(preview.assets) ? preview.assets : [];
    setText("#publish-dialog-subtitle", `${machineIdForPublishTask(task) || "未识别节点"} · ${task.account_slot}`);
    setText("#publish-account-avatar", initials(account?.nickname || task.account_slot));
    setText("#publish-account-name", account?.nickname || task.account_slot);
    setText(
      "#publish-account-identity",
      `UID ${identity.live_user_id || account?.uid || "未核验"}`
    );
    setText("#publish-kind", { image: "图文", video: "视频", long_article: "长文" }[preview.kind] || "发布");
    setText("#publish-title", preview.title || "（无标题）");
    setText("#publish-content", preview.description || preview.content || "（无正文）");
    setText(
      "#publish-tags",
      Array.isArray(preview.tags) && preview.tags.length
        ? preview.tags.map((tag) => `#${tag}`).join(" ")
        : "无标签"
    );
    setText("#publish-visibility", preview.visibility || "公开可见");
    setText("#publish-schedule", preview.schedule_at || "立即发布");
    setText("#publish-asset-count", `${assets.length} 个`);
    const list = $("#publish-assets");
    list.replaceChildren();
    if (!assets.length) list.appendChild(el("li", "", "未记录素材"));
    assets.forEach((asset, index) => {
      const item = el("li");
      append(
        item,
        el("span", "", String(index + 1).padStart(2, "0")),
        el("strong", "", publishAssetName(asset))
      );
      item.title = String(asset);
      list.appendChild(item);
    });
    const ready = Boolean(
      account && upper(account.health_state) === "READY" && (account.uid || identity.live_user_id)
    );
    const checkbox = $("#publish-preview-confirmed");
    checkbox.checked = false;
    checkbox.disabled = !ready;
    $("#publish-confirm-submit").disabled = true;
    $("#publish-save-draft").disabled = !ready;
    setText(
      "#publish-confirm-message",
      ready
        ? "请在绑定的Chrome Profile中核对真实预览，再勾选确认。"
        : "当前账号不是READY状态，请先完成账号恢复。"
    );
    $("#publish-confirm-dialog").showModal();
  }

  async function executePublishConfirmation(action) {
    const task = state.activePublishTask;
    if (!task) return;
    const publish = action === "publish-confirm";
    if (publish && !$("#publish-preview-confirmed").checked) return;
    if (!publish && !window.confirm("确认不发布，并把当前内容保存到小红书草稿箱？")) return;
    const account = publishAccount(task);
    const identity = account?.identity || {};
    const primary = $("#publish-confirm-submit");
    const draft = $("#publish-save-draft");
    primary.disabled = true;
    draft.disabled = true;
    setText(
      "#publish-confirm-message",
      publish ? "正在确认发布并等待平台结果回读…" : "正在保存到草稿箱…"
    );
    try {
      const payload = await mutateJson(`${api}/tasks/${task.task_id}/${action}`, "POST", {
        account_slot: task.account_slot,
        verified_uid: identity.live_user_id || account?.uid || "",
        confirmed: true,
        ...(publish ? { preview_confirmed: true } : {}),
      });
      const finalTask = payload.task || {};
      setText("#publish-confirm-message", finalTask.result_summary || label(finalTask.state));
      await loadDashboard("publish-confirmation");
      if (["SUCCESS", "CANCELLED"].includes(upper(finalTask.state))) {
        $("#publish-confirm-dialog").close();
      }
    } catch (error) {
      setText("#publish-confirm-message", error.message);
      primary.disabled = !$("#publish-preview-confirmed").checked;
      draft.disabled = false;
    }
  }

  function openTaskDetail(kind, id) {
    const item = kind === "task"
      ? state.tasks.find((task) => task.task_id === id)
      : state.results.find((result) => (result.record_id || result.task_id) === id);
    if (!item) return;
    const isTask = kind === "task";
    const request = item.request || item.task_request || {};
    setText("#task-detail-title", isTask ? "Agent任务详情" : "执行结果详情");
    setText("#task-detail-subtitle", item.task_id || item.record_id || "—");
    const body = $("#task-detail-body");
    const grid = el("div", "task-detail-grid");
    [
      ["员工机", item.machine_id || item.target_machine_id],
      ["账号", item.account_slot || request.account_slot],
      ["能力", isTask ? requestedCapability(item) : item.capability],
      ["状态", label(item.state || item.business_state)],
      ["创建时间", formatTime(item.created_at || item.recorded_at)],
      ["更新时间", formatTime(isTask ? taskTime(item) : resultTime(item))],
      ["目标摘要", request.request_summary || item.message],
      ["结果摘要", item.result_summary || item.message],
      ["错误分类", item.error_classification || item.error_code],
      ["建议动作", item.recommended_action],
    ].forEach(([name, value]) => {
      if (value === undefined || value === null || value === "") return;
      const card = el("article");
      append(card, el("span", "", name), el("strong", "", value));
      grid.appendChild(card);
    });
    body.replaceChildren(grid);
    $("#task-detail-dialog").showModal();
  }

  function closeDialog(selector) {
    const dialog = $(selector);
    if (dialog.open) dialog.close();
  }

  function normalizeSnapshot(snapshot) {
    state.snapshot = snapshot;
    state.machines = Array.isArray(snapshot.machines)
      ? snapshot.machines.filter((machine) => upper(machine.machine_role, "EXECUTOR") !== "MANAGER")
      : [];
    state.accounts = Array.isArray(snapshot.accounts) ? snapshot.accounts : [];
    state.tasks = Array.isArray(snapshot.tasks) ? snapshot.tasks : [];
    state.results = Array.isArray(snapshot.results) ? snapshot.results : [];
    state.alerts = Array.isArray(snapshot.alerts) ? snapshot.alerts : [];
    state.lastUpdated = snapshot.generated_at || new Date().toISOString();
    if (!state.machines.some((machine) => machine.machine_id === state.selectedMachineId)) {
      state.selectedMachineId = sortedMachines()[0]?.machine_id || "";
      if (state.selectedMachineId) {
        localStorage.setItem("auto-xhs:selected-node", state.selectedMachineId);
      }
    }
  }

  function scheduleRefresh(delay = DASHBOARD_REFRESH_INTERVAL_MS) {
    window.clearTimeout(dashboardRefreshTimer);
    if (document.hidden) return;
    dashboardRefreshTimer = window.setTimeout(() => loadDashboard("auto"), delay);
  }

  async function performDashboardRefresh(reason) {
    setText("#auto-refresh-state", reason === "initial" ? "正在读取最新缓存" : "正在同步状态");
    const [snapshotResult, tasksResult] = await Promise.allSettled([
      fetchJson(`${api}/manager/dashboard-snapshot`),
      fetchJson(`${api}/tasks`),
    ]);
    if (snapshotResult.status === "rejected") throw snapshotResult.reason;
    normalizeSnapshot(snapshotResult.value);
    if (tasksResult.status === "fulfilled") {
      state.localTasks = Array.isArray(tasksResult.value.tasks) ? tasksResult.value.tasks : [];
    }
    setText("#last-updated", formatTime(state.lastUpdated, true));
    renderServiceState(true);
    renderAll();
  }

  function loadDashboard(reason = "auto") {
    if (dashboardRefreshPromise) {
      dashboardRefreshQueued = true;
      return dashboardRefreshPromise;
    }
    dashboardRefreshPromise = performDashboardRefresh(reason)
      .catch((error) => {
        renderServiceState(false, error.message || "读取失败");
      })
      .finally(() => {
        dashboardRefreshPromise = null;
        if (dashboardRefreshQueued) {
          dashboardRefreshQueued = false;
          loadDashboard("queued");
          return;
        }
        scheduleRefresh();
      });
    return dashboardRefreshPromise;
  }

  function pauseVisibleDataPolls() {
    window.clearTimeout(dashboardRefreshTimer);
    dashboardRefreshTimer = null;
  }

  function handleDashboardVisibilityChange() {
    if (document.hidden) {
      pauseVisibleDataPolls();
      return;
    }
    loadDashboard("resume");
  }

  function bindEvents() {
    $("#node-list").addEventListener("click", (event) => {
      const button = event.target.closest("[data-machine-id]");
      if (button) selectMachine(button.dataset.machineId);
    });
    document.querySelectorAll("[data-node-tab]").forEach((button) => {
      button.addEventListener("click", () => selectTab(button.dataset.nodeTab));
    });
    $("#account-list").addEventListener("click", (event) => {
      const button = event.target.closest("[data-account-key]");
      if (button) openAccountDialog(button.dataset.accountKey);
    });
    $("#alert-list").addEventListener("click", (event) => {
      const button = event.target.closest("[data-account-key]");
      if (button) openAccountDialog(button.dataset.accountKey);
    });
    $("#business-grid").addEventListener("click", (event) => {
      const button = event.target.closest("[data-open-publish]");
      if (button) openPublishDialog(button.dataset.openPublish);
    });
    $("#account-recovery-actions").addEventListener("click", (event) => {
      const button = event.target.closest("[data-recovery-action]");
      if (button) runAccountRecovery(button.dataset.recoveryAction, button);
    });
    [$("#active-task-list"), $("#recent-result-list")]
      .forEach((container) => {
        container.addEventListener("click", (event) => {
          const button = event.target.closest("[data-task-detail-kind]");
          if (button) openTaskDetail(button.dataset.taskDetailKind, button.dataset.taskDetailId);
        });
      });
    $("#account-dialog-close").addEventListener("click", () => closeDialog("#account-recovery-dialog"));
    $("#publish-dialog-close").addEventListener("click", () => closeDialog("#publish-confirm-dialog"));
    $("#task-detail-close").addEventListener("click", () => closeDialog("#task-detail-dialog"));
    $("#publish-preview-confirmed").addEventListener("change", (event) => {
      $("#publish-confirm-submit").disabled = !event.target.checked;
    });
    $("#publish-confirm-submit").addEventListener("click", () => {
      executePublishConfirmation("publish-confirm");
    });
    $("#publish-save-draft").addEventListener("click", () => {
      executePublishConfirmation("save-draft");
    });
    document.addEventListener("visibilitychange", handleDashboardVisibilityChange);
  }

  bindEvents();
  loadDashboard("initial");
})();
