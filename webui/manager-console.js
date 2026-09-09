(() => {
  "use strict";

  const { api, fetchJson, mutateJson } = window.AutoXhsApi;
  const managerApi = `${api}/manager`;
  const DASHBOARD_REFRESH_INTERVAL_MS = 30_000;
  const TASK_REFRESH_INTERVAL_MS = 5_000;
  const VIEW_STORAGE_KEY = "auto-xhs:manager-view";
  const state = {
    available: false,
    machines: [],
    tasks: [],
    schedules: [],
    operationReports: [],
    transfers: [],
    results: [],
    resultHistory: [],
    incrementalRecords: [],
    resultSummary: {},
    versions: [],
    accounts: [],
    alerts: [],
    sync: {},
    errors: {},
    localTasks: [],
    activePublishTask: null,
    activeManagedAccount: null,
    system: {},
    personas: [],
    goals: [],
    dailyPlans: [],
    operationTasks: [],
    agentMessages: [],
    snapshot: {},
  };
  const pendingActions = new Set();
  const expandedPersonas = new Set();
  const expandedWorkerDays = new Set();
  let editingPersona = null;
  let planning = null;
  let employeePanel = null;
  let resultView = null;
  let selectedEmployeeAccount = localStorage.getItem("auto-xhs:employee-account") || "";
  let accountTools = null;
  let selectedMachineId = localStorage.getItem("auto-xhs:manager-machine") || "";
  let selectedAccountNode = localStorage.getItem("auto-xhs:manager-account-node") || "all";
  let selectedOperationTaskId = "";
  let selectedGoalId = "";
  let selectedProgressTab = "goals";
  let selectedView = localStorage.getItem(VIEW_STORAGE_KEY) || "overview";
  let refreshTimer = null;
  let refreshPromise = null;
  const viewScrollPositions = new Map();

  const terminalTaskStates = new Set([
    "SUCCESS",
    "PARTIAL_SUCCESS",
    "FAILED",
    "BLOCKED",
    "RESULT_UNKNOWN",
    "CANCELLED",
    "COMPLETED",
  ]);
  const reconciliationStates = new Set([
    "DISPATCH_UNKNOWN",
    "RECONCILIATION_REQUIRED",
    "RESULT_UNKNOWN",
  ]);
  const publishCapabilities = new Set([
    "fill-publish",
    "fill-publish-video",
    "long-article",
  ]);
  const stateLabels = {
    ACCEPTED: "已接收",
    ACKNOWLEDGED: "已接收",
    ACTION_REQUIRED: "仍需现场处理",
    ACTIVE: "运行中",
    BLOCKED: "已阻塞",
    CANCELLED: "已取消",
    CHECK_UNKNOWN: "检查结果待核对",
    COMPLETED: "已完成",
    CONTACT_LOST: "连接中断",
    CURRENT: "当前版本",
    DISABLED: "已停用",
    DRAFT: "草稿",
    DISPATCH_UNKNOWN: "派发结果待核对",
    DISPATCHING: "正在下发",
    FAILED: "失败",
    FORMAL: "正式",
    IDLE: "空闲",
    IN_PROGRESS: "现场处理中",
    INACTIVE: "未启用",
    MISSING: "数据缺失",
    MIXED: "部分缺失",
    OFFLINE: "离线",
    OBSERVED: "已回传",
    ONLINE: "在线",
    PARTIAL_SUCCESS: "部分完成",
    PAUSED: "已暂停",
    PENDING: "等待现场处理",
    PENDING_PULL: "等待回收",
    PLANNING: "经理 Agent 规划中",
    PAUSE_PENDING: "暂停待同步",
    PAUSE_REQUESTED: "暂停处理中",
    PENDING_DISPATCH: "等待派发",
    PREPARING: "准备中",
    READY: "可调度",
    RECONCILIATION_REQUIRED: "需要对账",
    REGISTERED: "已登记",
    RESULT_UNKNOWN: "结果待核对",
    RESUME_PENDING: "恢复待同步",
    RUNNING: "执行中",
    SUCCESS: "成功",
    TRIAL: "试用",
    UNVERIFIED: "待检查",
    UNKNOWN: "状态未知",
    UPLOADING: "传输中",
    WARNING: "需要关注",
    WAITING_AGENT: "等待经理 Agent",
  };
  const messageTypeLabels = {
    TASK_AVAILABLE: "运营任务已创建",
    TASK_CLAIMED: "经理 Agent 已领取",
    PLAN_READY: "执行计划已生成",
    TASK_DISPATCH: "已向员工机下发任务",
    TASK_ACK: "员工机已接收任务",
    TASK_PROGRESS: "员工机正在执行",
    TASK_RESULT: "员工机已产生结果",
    RESULT_ACK: "经理机已收到结果",
    TASK_FINISHED: "运营任务已完成",
  };
  const progressStageLabels = {
    starting: "开始执行",
    browsing: "浏览与点赞",
    reading_context: "读取评论上下文",
    comment_generated: "评论文本已生成",
    platform_submitted: "平台操作已提交",
    outbox_written: "结果已写入 outbox",
    result_collected: "经理机已回收结果",
  };
  const metricLabels = {
    browse: "浏览",
    like: "点赞",
    comment: "评论",
    reply: "回复",
    publish: "发布",
    friend_request: "关注博主",
  };

  function $(selector) {
    return document.querySelector(selector);
  }

  function element(tag, className = "", text = "") {
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

  function formatTime(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString("zh-CN", { hour12: false });
  }

  function labelFor(value) {
    const normalized = String(value || "UNKNOWN").toUpperCase();
    return stateLabels[normalized] || normalized;
  }

  function toneFor(value) {
    const normalized = String(value || "").toUpperCase();
    if (
      ["ACTIVE", "READY", "SUCCESS", "COMPLETED", "ONLINE", "FORMAL", "CURRENT", "OBSERVED"]
        .includes(normalized)
    ) {
      return "success";
    }
    if (
      ["RUNNING", "ACCEPTED", "UPLOADING", "TRIAL", "PLANNING", "DISPATCHING"]
        .includes(normalized)
    ) return "info";
    if (
      [
        "REGISTERED",
        "UNVERIFIED",
        "PAUSED",
        "PENDING_DISPATCH",
        "PREPARING",
        "CHECK_UNKNOWN",
        "DISPATCH_UNKNOWN",
        "RECONCILIATION_REQUIRED",
        "RESULT_UNKNOWN",
        "UNKNOWN",
        "WARNING",
        "WAITING_AGENT",
        "MISSING",
        "MIXED",
      ].includes(normalized)
    ) {
      return "warning";
    }
    if (["OFFLINE", "BLOCKED", "FAILED"].includes(normalized)) return "danger";
    return "neutral";
  }

  function stateBadge(value) {
    const badge = element("span", "manager-state-badge", labelFor(value));
    badge.dataset.tone = toneFor(value);
    return badge;
  }

  function accountSignalLabel(kind, value) {
    const state = String(value || "UNKNOWN").toUpperCase();
    const readyLabels = { bridge: "在线", extension: "已连接", profile: "已核验" };
    if (state === "READY") return readyLabels[kind] || "正常";
    if (state === "WARNING") return kind === "extension" ? "待连接" : "待确认";
    if (state === "BLOCKED") return "异常";
    return "未知";
  }

  function emptyCard(title, detail) {
    const card = element("article", "empty-card");
    const copy = element("div");
    append(copy, element("strong", "", title), element("span", "", detail));
    return append(card, copy);
  }

  function errorCard(message) {
    return element("article", "manager-error-card", message);
  }

  function factList(values, className = "manager-record-facts") {
    const list = element("dl", className);
    values.forEach(([label, value]) => {
      const item = element("div");
      append(item, element("dt", "", label), element("dd", "", value || "—"));
      list.appendChild(item);
    });
    return list;
  }

  function actionButton(label, action, identity, kind = "") {
    const button = element("button", `manager-action-button ${kind}`.trim(), label);
    button.type = "button";
    button.dataset.managerAction = action;
    button.dataset.managerIdentity = identity;
    if (pendingActions.has(`${action}:${identity}`)) {
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
    }
    return button;
  }

  function accountActionButton(label, action, account, kind = "") {
    const button = element("button", `manager-action-button ${kind}`.trim(), label);
    button.type = "button";
    button.dataset.managerAccountAction = action;
    button.dataset.managerMachine = account.machine_id;
    button.dataset.managerAccount = account.account_slot;
    const pendingKey = `account-${action}:${account.account_key}`;
    if (pendingActions.has(pendingKey)) {
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
    }
    return button;
  }

  function alertsForMachine(machineId) {
    return state.alerts.filter((alert) => alert.machine_id === machineId && alert.active !== false);
  }

  function highestAlertState(machineId) {
    const severities = new Set(alertsForMachine(machineId).map((alert) => alert.severity));
    if (severities.has("BLOCKED")) return "BLOCKED";
    if (severities.has("WARNING")) return "WARNING";
    if (severities.has("UNKNOWN")) return "UNKNOWN";
    return "";
  }

  function setMessage(selector, message, tone = "") {
    const node = $(selector);
    if (!node) return;
    node.textContent = message;
    node.dataset.tone = tone;
  }

  function populateMachineSelects() {
    const selectors = [
      "#manager-result-machine",
    ];
    selectors.forEach((selector) => {
      const select = $(selector);
      if (!select) return;
      const previous = select.value;
      const allOption = selector === "#manager-result-machine";
      const placeholder = element("option", "", allOption ? "全部执行机" : "请选择执行机");
      placeholder.value = "";
      select.replaceChildren(placeholder);
      state.machines.forEach((machine) => {
        const option = element(
          "option",
          "",
          `${machine.host_alias || machine.machine_id} · ${labelFor(machine.status)}`,
        );
        option.value = machine.machine_id;
        select.appendChild(option);
      });
      if ([...select.options].some((option) => option.value === previous)) select.value = previous;
    });
  }

  function machineActionButtons(machine) {
    const actions = element("div", "manager-card-actions");
    const status = String(machine.status || "REGISTERED").toUpperCase();
    const sshState = String(machine.ssh_state || "UNVERIFIED").toUpperCase();
    const executorService = machine.last_health?.executor_service
      || machine.last_health?.local_service;
    const executorServiceReady = executorService?.ready;
    if (["REGISTERED", "OFFLINE", "BLOCKED", "CHECK_UNKNOWN", "UNKNOWN"].includes(status)) {
      actions.appendChild(actionButton("连接检查", "machine-check", machine.machine_id, "primary"));
    }
    if (sshState === "ONLINE" && executorServiceReady === false) {
      actions.appendChild(
        actionButton(
          "启动执行服务",
          "machine-executor-start",
          machine.machine_id,
          "primary",
        ),
      );
    }
    if (sshState === "ONLINE" && executorServiceReady === true) {
      actions.appendChild(
        actionButton("重启执行服务", "machine-executor-restart", machine.machine_id),
      );
    }
    if (status === "READY") {
      actions.appendChild(actionButton("暂停调度", "machine-pause", machine.machine_id, "warning"));
    }
    if (status === "PAUSED") {
      actions.appendChild(actionButton("恢复调度", "machine-resume", machine.machine_id, "primary"));
    }
    if (["READY", "PAUSED"].includes(status)) {
      actions.appendChild(actionButton("回收结果", "machine-collect", machine.machine_id));
    }
    return actions;
  }

  function recordsForMachine(records, machineId) {
    return records.filter((record) => record.machine_id === machineId);
  }

  function employeeAccountKey(account) {
    return `${account.machine_id}:${account.account_slot}`;
  }

  function accountUnitNavButton(account) {
    const button = element("button", "manager-machine-nav-item");
    button.type = "button";
    button.dataset.employeeAccountSelect = employeeAccountKey(account);
    const active = selectedView === "employee-units"
      && selectedEmployeeAccount === employeeAccountKey(account);
    button.setAttribute("aria-pressed", String(active));
    if (active) button.classList.add("active");

    const name = account.nickname || account.account_slot;
    const platform = account.platform === "rednote" ? "RedNote" : "小红书";
    button.title = `${name} · ${account.machine_id} / ${account.account_slot} · ${platform}`;
    const avatar = element("span", "manager-rail-avatar", Array.from(name).slice(0, 2).join(""));
    avatar.dataset.tone = toneFor(account.health_state);
    const copy = element("span", "manager-rail-copy");
    append(
      copy,
      element("strong", "", name),
      element("small", "", `${account.machine_id} · ${platform} · ${account.account_slot}`),
    );
    const tail = element("span", "manager-node-state");
    tail.appendChild(stateBadge(account.health_state || "UNKNOWN"));
    append(button, avatar, copy, tail);
    return button;
  }

  function renderMachineNavigation() {
    const nav = $("#manager-machine-nav");
    const overviewSwitch = $("#manager-overview-switch");
    if (!nav || !overviewSwitch) return;
    if (
      selectedMachineId &&
      !state.machines.some((machine) => machine.machine_id === selectedMachineId)
    ) {
      selectedMachineId = "";
    }
    if (state.available && selectedEmployeeAccount && !state.accounts.some((account) =>
      employeeAccountKey(account) === selectedEmployeeAccount)) {
      selectedEmployeeAccount = "";
      localStorage.setItem("auto-xhs:employee-account", "");
    }
    setText("#manager-rail-total", `${state.accounts.length} 个`);
    setText("#manager-overview-count", state.accounts.length);
    $(".control-room").dataset.machineScope = selectedMachineId ? "single" : "all";
    const overviewActive = selectedView === "employee-units" && !selectedEmployeeAccount;
    overviewSwitch.classList.toggle("active", overviewActive);
    overviewSwitch.setAttribute("aria-pressed", String(overviewActive));
    nav.replaceChildren(...state.accounts.map(accountUnitNavButton));
  }

  function compactTaskCard(task) {
    const request = task.request || {};
    const card = element("article", "manager-compact-card");
    const head = element("div", "manager-card-head");
    const copy = element("div", "manager-record-copy");
    append(
      copy,
      element("strong", "", request.request_summary || task.task_id),
      element(
        "small",
        "",
        [task.task_id, request.account_slot, request.capability].filter(Boolean).join(" · "),
      ),
    );
    append(head, copy, stateBadge(taskPrimaryState(task)));
    append(card, head);
    const detail = task.result_summary || task.message || task.recommended_action;
    if (detail) card.appendChild(element("p", "manager-inline-message", detail));
    const actions = taskActions(task);
    if (actions.childElementCount) card.appendChild(actions);
    return card;
  }

  function pendingPublishTasks() {
    return state.localTasks.filter((task) => {
      return (
        publishCapabilities.has(task.capability) &&
        String(task.state || "").toUpperCase() === "WAITING_APPROVAL" &&
        task.parameters?.stage === "preview_ready"
      );
    });
  }

  function machineIdForPublishTask(task) {
    const explicit = task.machine_id || task.parameters?.machine_id;
    if (explicit) return explicit;
    const matches = state.accounts.filter((account) => account.account_slot === task.account_slot);
    return matches.length === 1 ? matches[0].machine_id : "";
  }

  function publishAccount(task) {
    const machineId = machineIdForPublishTask(task);
    const accounts = state.accounts;
    return accounts.find((account) => {
      return account.machine_id === machineId && account.account_slot === task.account_slot;
    }) || null;
  }

  function publishAssetName(value) {
    const parts = String(value || "").split(/[\\/]/).filter(Boolean);
    return parts[parts.length - 1] || "未命名素材";
  }

  function renderPublishReviews() {
    const panel = $("#manager-publish-review-panel");
    const list = $("#manager-publish-review-list");
    if (!panel || !list) return;
    const tasks = pendingPublishTasks();
    panel.hidden = tasks.length === 0;
    list.replaceChildren(
      ...tasks.map((task) => {
        const card = element("article", "manager-compact-card");
        const head = element("div", "manager-card-head");
        const copy = element("div", "manager-record-copy");
        append(
          copy,
          element("strong", "", task.parameters?.preview?.title || task.request_summary || task.task_id),
          element(
            "small",
            "",
            `${machineIdForPublishTask(task) || "本机"} / ${task.account_slot || "—"}`,
          ),
        );
        const button = element("button", "manager-action-button primary", "核对发布预览");
        button.type = "button";
        button.dataset.managerPublishReview = task.task_id;
        append(head, copy, stateBadge(task.state));
        append(card, head, button);
        return card;
      }),
    );
  }

  function openPublishDialog(taskId) {
    const task = pendingPublishTasks().find((item) => item.task_id === taskId);
    if (!task) return;
    state.activePublishTask = task;
    const preview = task.parameters?.preview || {};
    const account = publishAccount(task);
    const identity = account?.identity || {};
    const assets = Array.isArray(preview.assets) ? preview.assets : [];
    setText(
      "#publish-dialog-subtitle",
      `${machineIdForPublishTask(task) || "未识别节点"} / ${task.account_slot || "—"}`,
    );
    setText("#publish-account-avatar", String(account?.nickname || task.account_slot || "账").slice(0, 1));
    setText("#publish-account-name", account?.nickname || task.account_slot);
    setText("#publish-account-identity", `UID ${identity.live_user_id || account?.uid || "未核验"}`);
    setText(
      "#publish-kind",
      { image: "图文", video: "视频", long_article: "长文" }[preview.kind] || "发布",
    );
    setText("#publish-title", preview.title || "（无标题）");
    setText("#publish-content", preview.description || preview.content || "（无正文）");
    setText(
      "#publish-tags",
      Array.isArray(preview.tags) && preview.tags.length
        ? preview.tags.map((tag) => `#${tag}`).join(" ")
        : "无标签",
    );
    setText("#publish-visibility", preview.visibility || "公开可见");
    setText("#publish-schedule", preview.schedule_at || "立即发布");
    setText("#publish-asset-count", `${assets.length} 个`);
    const list = $("#publish-assets");
    list.replaceChildren();
    if (!assets.length) list.appendChild(element("li", "", "未记录素材"));
    assets.forEach((asset, index) => {
      const item = element("li");
      append(
        item,
        element("span", "", String(index + 1).padStart(2, "0")),
        element("strong", "", publishAssetName(asset)),
      );
      item.title = String(asset);
      list.appendChild(item);
    });
    const ready = Boolean(
      account &&
      String(account.health_state || "").toUpperCase() === "READY" &&
      (account.uid || identity.live_user_id),
    );
    const checkbox = $("#publish-preview-confirmed");
    checkbox.checked = false;
    checkbox.disabled = !ready;
    $("#publish-confirm-submit").disabled = true;
    $("#publish-save-draft").disabled = !ready;
    setText(
      "#publish-confirm-message",
      ready
        ? "请在绑定的 Chrome Profile 中核对真实预览，再勾选确认。"
        : "当前账号尚未达到可发布状态，请先处理账号连接或身份。",
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
      publish ? "正在确认发布并等待平台结果回读…" : "正在保存到草稿箱…",
    );
    try {
      const payload = await mutateJson(`${api}/tasks/${task.task_id}/${action}`, "POST", {
        account_slot: task.account_slot,
        verified_uid: identity.live_user_id || account?.uid || "",
        confirmed: true,
        preview_revision: task.parameters?.preview_revision,
        ...(publish ? { preview_confirmed: true } : {}),
      });
      const finalTask = payload.task || {};
      setText("#publish-confirm-message", finalTask.result_summary || labelFor(finalTask.state));
      await load();
      if (["SUCCESS", "CANCELLED"].includes(String(finalTask.state || "").toUpperCase())) {
        $("#publish-confirm-dialog").close();
      }
    } catch (error) {
      setText("#publish-confirm-message", error.message);
      primary.disabled = !$("#publish-preview-confirmed").checked;
      draft.disabled = false;
    }
  }

  function activityTime(record) {
    const value =
      record.recorded_at || record.collected_at || record.updated_at || record.created_at || "";
    const timestamp = new Date(value).getTime();
    return Number.isNaN(timestamp) ? 0 : timestamp;
  }

  function compactActivityCard(record, type) {
    const request = record.request || {};
    const isTransfer = type === "transfer";
    const card = element("article", "manager-compact-card");
    const head = element("div", "manager-card-head");
    const copy = element("div", "manager-record-copy");
    const title = isTransfer
      ? record.transfer_id || "功能包传输"
      : record.result_summary || record.task_id || "远程结果";
    const meta = isTransfer
      ? [request.transfer_type || record.transfer_type, record.actual_version]
      : [record.task_id || record.remote_execution_id, record.function_id];
    append(
      copy,
      element("strong", "", title),
      element("small", "", meta.filter(Boolean).join(" · ") || "远程回读"),
    );
    append(
      head,
      copy,
      stateBadge(isTransfer ? transferState(record) : record.state || record.status),
    );
    append(card, head);
    const detail = isTransfer
      ? record.message || record.installed_path || record.remote_path
      : record.recommended_action;
    if (detail) card.appendChild(element("p", "manager-inline-message", detail));
    card.appendChild(
      element(
        "small",
        "manager-compact-time",
        formatTime(
          record.recorded_at || record.collected_at || record.updated_at || record.created_at,
        ),
      ),
    );
    return card;
  }

  function compactAccountCard(account, allowActions = true) {
    const persona = accountPersona(account);
    const activeTask = state.tasks.find((task) => {
      const request = task.request || {};
      return (
        task.machine_id === account.machine_id &&
        request.account_slot === account.account_slot &&
        taskIsActive(task)
      );
    });
    const fieldRequest = account.field_request || {};
    const card = element("article", "manager-account-detail-card");
    const head = element("div", "manager-card-head");
    const copy = element("div", "manager-record-copy");
    append(
      copy,
      element("strong", "", account.nickname || account.account_slot),
      element(
        "small",
        "",
        [account.account_slot, account.uid ? `UID ${account.uid}` : "UID 待读取"]
          .filter(Boolean)
          .join(" · "),
      ),
    );
    append(head, copy, stateBadge(account.health_state));
    append(
      card,
      head,
      factList([
        ["机器 / 槽位", `${account.machine_id} / ${account.account_slot}`],
        ["平台", account.platform || "xiaohongshu"],
        ["登录状态", labelFor(account.login_status || account.health_state)],
        ["Bridge", accountSignalLabel("bridge", account.bridge_status)],
        ["扩展", accountSignalLabel("extension", account.extension_status)],
        ["Profile", accountSignalLabel("profile", account.profile_status)],
        ["绑定人设", persona?.display_name || "尚未绑定"],
        ["当前任务", activeTask?.request?.request_summary || labelFor(account.agent_task_status)],
        ["现场处理", fieldRequest.request_id ? labelFor(fieldRequest.state) : "无待处理请求"],
        ["账号备注", account.account_note || "尚未填写"],
        ["调度状态", account.scheduling_enabled === false ? "暂停调度" : "允许调度"],
        ["身份检查", formatTime(account.last_check_at)],
        ["数据来源", account.source_label || "员工机回执"],
        ["管理来源", account.management_source_label || "经理机配置"],
        ["配置同步", labelFor(account.management_sync_status || "UNKNOWN")],
        ["更新时间", formatTime(account.updated_at || account.last_seen_at || account.last_check_at)],
      ]),
    );
    if (account.abnormal_reason) {
      card.appendChild(element("p", "manager-inline-message", account.abnormal_reason));
    }
    if (account.recommended_action && account.health_state !== "READY") {
      card.appendChild(
        element("p", "manager-account-recommendation", `建议：${account.recommended_action}`),
      );
    }
    if (allowActions) {
      const actions = element("div", "manager-card-actions");
      actions.append(
        accountActionButton("管理信息", "manage", account),
        accountActionButton("运行诊断", "doctor", account),
        accountActionButton("启动 Profile", "profile-start", account, "primary"),
        accountActionButton("重启 Bridge", "bridge-restart", account),
        accountActionButton("检查身份", "identity-check", account),
      );
      card.appendChild(actions);
      const more = element("details", "manager-account-more");
      more.appendChild(element("summary", "", "更多账号管理"));
      const tools = element("div", "manager-card-actions");
      for (const [label, action] of [["扩展配对", "pair-begin"], ["切换账号", "switch-begin"],
        ["完成换号", "switch-complete"], ["退出登录", "logout"],
        ["停止 Bridge", "bridge-stop"], ["删除账号槽位", "delete"]]) {
        const button = element("button", "secondary-button", label);
        button.type = "button";
        button.addEventListener("click", () => accountTools.open(action, account));
        tools.appendChild(button);
      }
      append(more, tools); card.appendChild(more);
    }
    card.appendChild(accountPersonaPanel(account));
    return card;
  }

  function compactAlertCard(alert) {
    const card = element("article", "manager-alert-card");
    const head = element("div", "manager-card-head");
    const copy = element("div", "manager-record-copy");
    append(
      copy,
      element("strong", "", `${alert.account_slot} · ${alert.reason || "账号状态待处理"}`),
      element("small", "", formatTime(alert.last_seen_at)),
    );
    append(head, copy, stateBadge(alert.severity));
    append(card, head);
    if (alert.recommended_action) {
      card.appendChild(element("p", "manager-inline-message", alert.recommended_action));
    }
    return card;
  }

  function sameLocalDay(value, reference = new Date()) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return false;
    return date.toLocaleDateString("zh-CN") === reference.toLocaleDateString("zh-CN");
  }

  function resultOperationCount(result) {
    return Array.isArray(result.item_results) ? result.item_results.length : 1;
  }

  function resultLinks(result) {
    const links = [];
    const add = (label, value) => {
      const url = String(value || "");
      if (/^https?:\/\//i.test(url) && !links.some((item) => item.url === url)) {
        links.push({ label, url });
      }
    };
    [result, ...(Array.isArray(result.item_results) ? result.item_results : [])].forEach((item) => {
      if (!item || typeof item !== "object") return;
      add("笔记", item.note_url || item.feed_url || item.url);
      add("证据", item.evidence_url);
    });
    return links;
  }

  function compactTodayResultCard(result) {
    const card = compactActivityCard(result, "result");
    const links = resultLinks(result);
    if (links.length) {
      const row = element("div", "manager-result-links");
      links.forEach((item) => {
        const link = element("a", "", item.label);
        link.href = item.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        row.appendChild(link);
      });
      card.appendChild(row);
    }
    return card;
  }

  function workerMetric(record, kind) {
    const capability = resultCapability(record);
    if (/^(collect-|prepare-|generate-|fill-|save-|check-|list-|search-)/.test(capability)) {
      return { target: null, actual: null };
    }
    const [target, actual] = actionTargetActualText([record], kind).split(" / ");
    return { target: numericCount(target === "—" ? null : target),
      actual: numericCount(actual === "—" ? null : actual) };
  }

  function workerTaskDate(task) {
    return localDateValue(task.created_at || task.request?.created_at);
  }

  function workerDayMetrics(machineId, accountSlot, date) {
    const observed = (state.employeeSummary?.daily_metrics || []).find((row) =>
      row.machine_id === machineId && row.account_slot === accountSlot && row.date === date);
    const plan = (state.dailyPlans || []).find((item) => item.plan_date === date)
      ?.accounts.find((row) => row.machine_id === machineId && row.account_slot === accountSlot);
    const tasks = state.tasks.filter((task) => task.machine_id === machineId
      && task.request?.account_slot === accountSlot && workerTaskDate(task) === date
      && taskPrimaryState(task) !== "CANCELLED");
    const results = state.results.filter((result) => result.machine_id === machineId
      && resultAccount(result) === accountSlot && resultDate(result) === date);
    const goals = state.goals.filter((goal) => goal.period_start === date && goal.period_end === date
      && !["DRAFT", "CANCELLED"].includes(goal.state) && goal.targets?.length === 1
      && goal.targets[0].machine_id === machineId && goal.targets[0].account_slot === accountSlot);
    const planned = tasks.map((task) => ({ task_request: task.request }));
    // A receipt can outlive its task in the manager cache; count its request only once.
    const taskIds = new Set(state.tasks.filter((task) => task.machine_id === machineId)
      .map((task) => task.task_id));
    results.filter((result) => !taskIds.has(result.task_id))
      .forEach((result) => planned.push(result));
    return Object.keys(metricLabels).map((kind) => {
      const targets = goals.map((goal) => numericCount(goal.target_metrics?.[kind]));
      targets.push(...planned.filter((record) => !goals.some((goal) =>
        goal.goal_id === (record.goal_id || resultRequest(record).goal_id)
        && numericCount(goal.target_metrics?.[kind]) !== null))
        .map((record) => workerMetric(record, kind).target));
      const knownTargets = targets.filter((value) => value !== null);
      const observations = results.map((result) => workerMetric(result, kind));
      const knownActuals = observations.map((item) => item.actual).filter((value) => value !== null);
      const target = plan ? (plan.participating ? numericCount(plan.metrics[kind]) : 0)
        : knownTargets.length ? knownTargets.reduce((sum, n) => sum + n, 0) : null;
      const actual = knownActuals.length ? knownActuals.reduce((sum, n) => sum + n, 0) : null;
      const incomplete = observations.some((item) => item.target !== null && item.actual === null);
      let status = "未设置目标";
      let tone = "info";
      if (incomplete || (actual === null && target !== null)) status = "等待回执";
      else if (target !== null && actual !== null) {
        status = actual >= target ? "完成" : `${actual === 0 ? "未开始" : "进行中"} · 差 ${target - actual}`;
        tone = actual >= target ? "success" : "warning";
      } else if (actual !== null) status = "已回传";
      const server = observed?.metrics[kind];
      return server && (server.actual !== null || server.target !== null)
        ? {...server, target: server.target ?? target}
        : { kind, target, actual, status, tone, incomplete };
    });
  }

  function workerDayCard(machineId, accountSlot, date) {
    const account = state.accounts.find((item) => item.machine_id === machineId
      && item.account_slot === accountSlot);
    const card = element("section", "worker-day-account");
    append(card, append(element("div", "worker-account-strip"),
      element("span", "", "执行账号"), element("strong", "", account?.nickname || accountSlot),
      element("small", "", `${machineId} / ${accountSlot}`)));
    const rows = workerDayMetrics(machineId, accountSlot, date).map((metric) => {
      const badge = element("span", "manager-state-badge", metric.status);
      badge.dataset.tone = metric.tone;
      const actual = metric.actual === null ? "—"
        : metric.incomplete ? `${metric.actual}（部分回传）` : metric.actual;
      return [metricLabels[metric.kind], metric.target ?? "—", actual, badge];
    });
    card.appendChild(observationTable(["指标", "目标", "实际", "状态"], rows,
      `${date} ${accountSlot} 目标与实际完成情况`));
    return card;
  }

  function workerDayAccounts(machineId, date) {
    const accounts = new Set();
    (state.dailyPlans || []).filter((plan) => plan.plan_date === date)
      .forEach((plan) => plan.accounts.filter((row) => row.machine_id === machineId)
        .forEach((row) => accounts.add(row.account_slot)));
    state.tasks.filter((task) => task.machine_id === machineId && workerTaskDate(task) === date)
      .forEach((task) => accounts.add(task.request?.account_slot));
    state.results.filter((result) => result.machine_id === machineId && resultDate(result) === date)
      .forEach((result) => accounts.add(resultAccount(result)));
    state.goals.filter((goal) => goal.period_start === date && goal.period_end === date)
      .forEach((goal) => (goal.targets || []).filter((target) => target.machine_id === machineId)
        .forEach((target) => accounts.add(target.account_slot)));
    if (date === localDateValue(new Date())) {
      state.accounts.filter((account) => account.machine_id === machineId)
        .forEach((account) => accounts.add(account.account_slot));
    }
    return [...accounts].filter(Boolean).sort();
  }

  function nextWorkerPlan(machineId, today) {
    return [...(state.dailyPlans || [])].filter((plan) => plan.plan_date > today
      && plan.accounts.some((row) => row.machine_id === machineId && row.participating))
      .sort((a, b) => a.plan_date.localeCompare(b.plan_date))[0];
  }

  function renderWorkerNextDay(machineId, today) {
    const plan = nextWorkerPlan(machineId, today);
    const host = $("#worker-next-day");
    $("#worker-next-plan-link").onclick = () => {
      if (plan) planning.selectDate(plan.plan_date);
    };
    if (!plan) {
      host.replaceChildren(emptyCard("待规划", "保存该员工机的未来计划后，在这里展示操作数量。"));
      return;
    }
    host.replaceChildren(...plan.accounts.filter((row) => row.machine_id === machineId
      && row.participating).map((row) => {
      const account = state.accounts.find((item) => item.machine_id === machineId
        && item.account_slot === row.account_slot);
      const card = element("article", "worker-next-account");
      const heading = element("div", "room-section-head");
      const badge = element("span", "manager-state-badge", "已规划");
      badge.dataset.tone = "success";
      append(heading, element("h3", "", `${plan.plan_date} · ${account?.nickname || row.account_slot}`), badge);
      const metrics = element("div", "daily-plan-numbers");
      ["browse", "like", "comment", "publish"].forEach((kind) => {
        append(metrics, append(element("div"), element("span", "", metricLabels[kind]),
          element("strong", "", String(row.metrics[kind] ?? 0))));
      });
      append(card, heading, metrics,
        element("p", "room-footnote", row.notes || `${machineId} / ${row.account_slot} · 由经理 Agent 安排执行`));
      return card;
    }));
  }

  function renderMachineDetail(machine) {
    setText("#manager-machine-detail-kicker", `员工机 · ${machine.machine_id}`);
    setText("#manager-machine-detail-title", machine.host_alias || machine.machine_id);
    setText("#manager-machine-detail-description", "按账号查看运营目标与实际完成情况。");
    const today = localDateValue(new Date());
    renderWorkerNextDay(machine.machine_id, today);
    setText("#worker-today-date", today);
    const accounts = workerDayAccounts(machine.machine_id, today);
    $("#worker-today-progress").replaceChildren(...(accounts.length
      ? accounts.map((slot) => workerDayCard(machine.machine_id, slot, today))
      : [emptyCard("暂无当日运营记录", "账号和目标下发后自动展示完成情况。")]));
    const dates = new Set([
      ...(state.dailyPlans || []).filter((plan) => plan.accounts.some((row) =>
        row.machine_id === machine.machine_id)).map((plan) => plan.plan_date),
      ...state.tasks.filter((task) => task.machine_id === machine.machine_id).map(workerTaskDate),
      ...state.results.filter((result) => result.machine_id === machine.machine_id).map(resultDate),
      ...state.goals.filter((goal) => goal.period_start === goal.period_end
        && goal.targets?.some((target) => target.machine_id === machine.machine_id))
        .map((goal) => goal.period_start),
    ].filter((date) => date && date < today));
    setText("#worker-history-count", `${dates.size} 天`);
    $("#worker-history-list").replaceChildren(...(dates.size
      ? [...dates].sort().reverse().map((date) => {
        const detail = element("details", "worker-history-day");
        const key = `${machine.machine_id}:${date}`;
        detail.open = expandedWorkerDays.has(key);
        detail.addEventListener("toggle", () => {
          if (detail.open) expandedWorkerDays.add(key);
          else expandedWorkerDays.delete(key);
        });
        append(detail, element("summary", "", date),
          ...workerDayAccounts(machine.machine_id, date)
            .map((slot) => workerDayCard(machine.machine_id, slot, date)));
        return detail;
      }) : [element("p", "manager-inline-message", "暂无往日记录。") ]));
  }

  function renderMachineStage() {
    const overview = $("#manager-cluster-overview-panel");
    const detail = $("#manager-machine-detail");
    if (!overview || !detail) return;
    const machine = state.machines.find((item) => item.machine_id === selectedMachineId);
    overview.hidden = Boolean(machine);
    detail.hidden = !machine;
    if (machine) renderMachineDetail(machine);
    else {
      const cards = state.machines.map(machineThumbnailCard);
      $("#manager-all-machine-list").replaceChildren(...(cards.length ? cards
        : [emptyCard("等待员工机同步", "已登记的员工机及账号状态会显示在这里。")]));
    }
  }

  function selectMachine(machineId) {
    const nextId = String(machineId || "");
    selectedMachineId = state.machines.some((machine) => machine.machine_id === nextId)
      ? nextId
      : "";
    localStorage.setItem("auto-xhs:manager-machine", selectedMachineId);
    renderMachineNavigation();
    renderMachineStage();
    selectManagerView("workers");
    viewScrollPositions.set("workers", 0);
  }

  function selectEmployeeAccount(accountKey) {
    selectedEmployeeAccount = state.accounts.some((account) => employeeAccountKey(account) === accountKey)
      ? accountKey : "";
    localStorage.setItem("auto-xhs:employee-account", selectedEmployeeAccount);
    selectManagerView("employee-units");
    viewScrollPositions.set("employee-units", 0);
  }

  function accountsForSelectedNode() {
    if (selectedAccountNode === "all") return state.accounts;
    return state.accounts.filter((account) => account.machine_id === selectedAccountNode);
  }

  function accountHasActiveTask(account) {
    return state.tasks.some((task) => {
      const request = task.request || {};
      return (
        task.machine_id === account.machine_id &&
        request.account_slot === account.account_slot &&
        taskIsExecuting(task)
      );
    });
  }

  function accountPersona(account) {
    return state.personas.find((persona) => {
      return (
        persona.machine_id === account.machine_id &&
        persona.account_slot === account.account_slot &&
        persona.status === "ACTIVE"
      );
    });
  }

  function populateAccountFilters(accounts) {
    const status = $("#manager-account-filter-status");
    const persona = $("#manager-account-filter-persona");
    if (!status || !persona) return;
    const selectedStatus = status.value;
    const selectedPersona = persona.value;
    const statuses = [...new Set(accounts.map((account) => account.health_state).filter(Boolean))];
    status.replaceChildren(element("option", "", "全部状态"));
    status.firstElementChild.value = "";
    statuses.sort().forEach((value) => {
      const option = element("option", "", labelFor(value));
      option.value = value;
      status.appendChild(option);
    });
    const personas = [...new Map(
      accounts
        .map(accountPersona)
        .filter(Boolean)
        .map((item) => [item.persona_id, item]),
    ).values()];
    persona.replaceChildren(element("option", "", "全部人设"));
    persona.firstElementChild.value = "";
    personas.forEach((item) => {
      const option = element("option", "", item.display_name || item.persona_id);
      option.value = item.persona_id;
      persona.appendChild(option);
    });
    status.value = selectedStatus;
    persona.value = selectedPersona;
  }

  function visibleAccounts() {
    const accounts = accountsForSelectedNode();
    populateAccountFilters(accounts);
    const status = $("#manager-account-filter-status")?.value || "";
    const persona = $("#manager-account-filter-persona")?.value || "";
    const task = $("#manager-account-filter-task")?.value || "";
    return accounts.filter((account) => {
      const boundPersona = accountPersona(account);
      const active = accountHasActiveTask(account);
      return (
        (!status || account.health_state === status) &&
        (!persona || boundPersona?.persona_id === persona) &&
        (!task || (task === "active" ? active : !active))
      );
    });
  }

  function accountNodeButton(machine) {
    const button = element("button", "manager-account-node");
    button.type = "button";
    button.dataset.managerAccountNode = machine.machine_id;
    button.setAttribute("aria-pressed", String(selectedAccountNode === machine.machine_id));
    if (selectedAccountNode === machine.machine_id) button.classList.add("active");
    const copy = element("span");
    append(
      copy,
      element("strong", "", machine.host_alias || machine.machine_id),
      element("small", "", `${machine.machine_id} · ${labelFor(machine.status)}`),
    );
    const count = state.accounts.filter((account) => account.machine_id === machine.machine_id).length;
    const tail = element("span", "manager-account-node-tail");
    const alertState = highestAlertState(machine.machine_id);
    if (alertState) {
      const marker = element("i", "manager-node-alert-marker");
      marker.dataset.tone = toneFor(alertState);
      marker.title = `${alertsForMachine(machine.machine_id).length} 个账号需要处理`;
      tail.appendChild(marker);
    }
    tail.appendChild(element("b", "", count));
    append(button, copy, tail);
    return button;
  }

  function renderAccountNavigation() {
    const nav = $("#manager-account-node-list");
    if (!nav) return;
    if (
      selectedAccountNode !== "all"
      && !state.machines.some((machine) => machine.machine_id === selectedAccountNode)
    ) {
      selectedAccountNode = "all";
    }
    nav.replaceChildren(...state.machines.map(accountNodeButton));
    document.querySelectorAll("[data-manager-account-node]").forEach((button) => {
      const active = button.dataset.managerAccountNode === selectedAccountNode;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    setText("#manager-account-node-all-count", state.accounts.length);
  }

  function renderAccountWorkspace() {
    const grid = $("#manager-remote-account-grid");
    if (!grid) return;
    renderAccountNavigation();
    const accounts = visibleAccounts();
    setText("#manager-account-view-title", selectedAccountNode === "all"
      ? "全部员工机账号" : `${selectedAccountNode} 账号`);
    const counts = new Map();
    accounts.forEach((account) => {
      const key = String(account.health_state || "UNKNOWN").toUpperCase();
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    setText("#manager-account-total", accounts.length);
    setText("#manager-account-ready", counts.get("READY") || 0);
    setText("#manager-account-warning", counts.get("WARNING") || 0);
    setText("#manager-account-blocked", counts.get("BLOCKED") || 0);
    setText("#manager-account-unknown", counts.get("UNKNOWN") || 0);
    setText("#manager-account-running", accounts.filter(accountHasActiveTask).length);
    setText("#blocked-total", state.accounts.length);
    const cards = accounts.map((account) => compactAccountCard(account));
    grid.replaceChildren(...(cards.length ? cards : [emptyCard("暂无匹配的员工机账号",
      "账号与专属人设同步后显示在这里，也可调整筛选条件。")]));
  }

  function accountPersonaPanel(account) {
    const persona = state.personas.find((item) => item.machine_id === account.machine_id
      && item.account_slot === account.account_slot);
    const panel = element("details", "manager-account-persona");
    const accountKey = `${account.machine_id}:${account.account_slot}`;
    panel.open = expandedPersonas.has(accountKey);
    panel.addEventListener("toggle", () => {
      if (panel.open) expandedPersonas.add(accountKey);
      else expandedPersonas.delete(accountKey);
    });
    const title = persona?.display_name || "待配置";
    panel.appendChild(element("summary", "", `账号人设 · ${title}`));
    const edit = element("button", "secondary-button", persona ? "编辑人设" : "配置人设");
    edit.type = "button";
    edit.addEventListener("click", () => openPersonaEditor(account));
    panel.appendChild(append(element("div", "manager-persona-toolbar"),
      persona ? stateBadge(persona.status) : element("span", "", "为此账号配置专属人设"), edit));
    if (!persona) {
      return panel;
    }
    append(panel, factList([
      ["角色定位", persona.role], ["目标受众", persona.audience],
      ["内容主题", (persona.content_topics || []).join("、")], ["语言", persona.language],
      ["表达语气", persona.tone], ["互动方式", persona.interaction_style],
      ["身份背景", persona.identity_background], ["专业方向", persona.professional_direction],
      ["回复长度", persona.reply_length], ["行为边界", persona.behavior_boundaries],
      ["记忆身份", persona.identity?.uid || "待核验"], ["生效时间", formatTime(persona.effective_at)],
      ["事实边界", persona.fact_boundaries], ["知识说明", persona.knowledge],
      ["版本", `v${persona.version || 1}`], ["更新时间", formatTime(persona.updated_at)],
    ]));
    const history = element("details", "");
    history.appendChild(element("summary", "", "人格版本记录"));
    for (const version of [...(persona.versions || [])].reverse()) {
      history.appendChild(factList([
        ["版本", `v${version.version}`],
        ["生效时间", formatTime(version.effective_at || version.saved_at)],
        ["身份背景", version.identity_background], ["专业方向", version.professional_direction],
        ["表达语气", version.tone], ["互动方式", version.interaction_style],
        ["回复长度", version.reply_length], ["行为边界", version.behavior_boundaries],
        ["事实边界", version.fact_boundaries], ["知识说明", version.knowledge],
        ["记忆身份", version.identity?.uid || "待核验"],
      ]));
    }
    panel.appendChild(history);
    return panel;
  }

  function openPersonaEditor(account) {
    const persona = state.personas.find((item) => item.machine_id === account.machine_id
      && item.account_slot === account.account_slot) || {};
    editingPersona = { machine_id: account.machine_id, account_slot: account.account_slot,
      persona_id: persona.persona_id || "" };
    const form = $("#manager-persona-editor-form");
    form.reset();
    for (const field of ["display_name", "role", "audience", "language", "tone",
      "interaction_style", "fact_boundaries", "knowledge", "identity_background",
      "professional_direction", "reply_length", "behavior_boundaries", "status", "content_topics"]) {
      const value = persona[field];
      form.elements.namedItem(field).value = Array.isArray(value) ? value.join("、")
        : value || (field === "language" ? "中文" : field === "status" ? "ACTIVE" : "");
    }
    setText("#manager-persona-editor-account", `${account.machine_id} / ${account.account_slot}`);
    setText("#manager-persona-editor-version", persona.version ? `当前版本 v${persona.version}` : "首次配置");
    setMessage("#manager-persona-editor-message", "");
    $("#manager-persona-editor").showModal();
  }

  async function savePersonaEditor(event) {
    event.preventDefault();
    if (!editingPersona) return;
    const form = event.currentTarget;
    const button = form.querySelector("button[type='submit']");
    const body = { ...editingPersona };
    for (const field of ["display_name", "role", "audience", "language", "tone",
      "interaction_style", "fact_boundaries", "knowledge", "identity_background",
      "professional_direction", "reply_length", "behavior_boundaries", "status"]) {
      body[field] = form.elements.namedItem(field).value.trim();
    }
    body.content_topics = form.elements.namedItem("content_topics").value
      .split(/[、，,\n]/).map((value) => value.trim()).filter(Boolean);
    button.disabled = true;
    setMessage("#manager-persona-editor-message", "正在保存人设…");
    try {
      const payload = await mutateJson(`${managerApi}/personas`, "POST", body);
      state.personas = [...state.personas.filter((item) => item.persona_id !== payload.persona.persona_id),
        payload.persona];
      expandedPersonas.add(`${body.machine_id}:${body.account_slot}`);
      $("#manager-persona-editor").close();
      renderAccountWorkspace();
      setMessage("#manager-account-message", `人设已保存 · v${payload.persona.version}`, "success");
    } catch (error) {
      setMessage("#manager-persona-editor-message", error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  function metricSummary(metrics, emptyLabel = "以期望成果为准") {
    const labels = { browse: "浏览", like: "点赞", comment: "评论", reply: "回复", publish: "发布" };
    return Object.entries(metrics || {})
      .filter(([, value]) => Number(value) > 0)
      .map(([key, value]) => `${labels[key] || key} ${value}`)
      .join(" · ") || emptyLabel;
  }

  function observationTable(headers, rows, caption) {
    const wrapper = element("div", "manager-table-scroll");
    const table = element("table", "manager-observation-table");
    const title = element("caption", "sr-only", caption);
    const head = element("thead");
    const heading = element("tr");
    headers.forEach((label) => {
      const cell = element("th", "", label);
      cell.scope = "col";
      heading.appendChild(cell);
    });
    head.appendChild(heading);
    const body = element("tbody");
    rows.forEach((values) => {
      const row = element("tr");
      values.forEach((value) => {
        const cell = element("td");
        if (value && typeof value === "object") cell.appendChild(value);
        else cell.textContent = String(value ?? "—");
        row.appendChild(cell);
      });
      body.appendChild(row);
    });
    append(table, title, head, body);
    wrapper.appendChild(table);
    return wrapper;
  }

  function recordTitle(title, subtitle) {
    return append(element("div", "manager-record-copy"),
      element("strong", "", title || "—"), element("small", "", subtitle || ""));
  }

  function targetNames(targets) {
    return (targets || []).map((target) =>
      `${target.machine_id || "LOCAL"} / ${target.account_slot || "—"}`).join("、") || "—";
  }

  function goalMetricProgress(goal) {
    const panel = element("div", "manager-metric-progress");
    const actual = goal.progress?.actual_metrics || {};
    const targets = goal.target_metrics || {};
    const keys = [...new Set([...Object.keys(targets), ...Object.keys(actual)])];
    keys.forEach((key) => {
      const target = Number(targets[key] || 0);
      const value = actual[key];
      if (!target && value == null) return;
      const row = element("div", "manager-metric-progress-row");
      append(row, element("span", "", metricLabels[key] || key),
        element("strong", "", `${value ?? "—"} / ${target || "—"}`));
      if (target > 0 && value != null) {
        const progress = element("progress");
        progress.max = target;
        progress.value = Number(value);
        progress.setAttribute("aria-label", `${metricLabels[key] || key} ${value} / ${target}`);
        row.appendChild(progress);
      }
      panel.appendChild(row);
    });
    if (!panel.childElementCount) panel.appendChild(element("small", "", "按目标说明验收"));
    return panel;
  }

  function goalMatchesFilters(goal) {
    const status = $("#manager-operation-filter-state")?.value || "";
    const machine = $("#manager-operation-filter-machine")?.value || "";
    const account = $("#manager-operation-filter-account")?.value || "";
    const date = $("#manager-operation-filter-date")?.value || "";
    const keyword = ($("#manager-operation-filter-keyword")?.value || "").trim().toLowerCase();
    const targets = goal.targets || [];
    const text = [goal.title, goal.goal_id, goal.desired_outcome, targetNames(targets)]
      .filter(Boolean).join(" ").toLowerCase();
    return (!status || goal.state === status)
      && ((!machine && !account) || targets.some((target) =>
        (!machine || target.machine_id === machine) && (!account || target.account_slot === account)))
      && (!date || (goal.period_start && goal.period_end
        && date >= goal.period_start && date <= goal.period_end))
      && (!keyword || text.includes(keyword));
  }

  function renderGoals() {
    const list = $("#manager-goal-list");
    if (!list) return;
    setText("#manager-goal-total", state.goals.length);
    for (const [id, status] of [["active", "ACTIVE"], ["completed", "COMPLETED"], ["paused", "PAUSED"]]) {
      setText(`#manager-goal-${id}`, state.goals.filter((goal) => goal.state === status).length);
    }
    const goals = state.goals.filter((goal) => selectedProgressTab !== "goals" || goalMatchesFilters(goal))
      .sort((a, b) => Number(b.state === "ACTIVE") - Number(a.state === "ACTIVE")
        || String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
    const rows = goals.map((goal) => {
      const detail = element("button", "manager-text-button", "查看详情");
      detail.type = "button";
      detail.dataset.managerGoalDetail = goal.goal_id;
      return [recordTitle(goal.title, goal.goal_id), targetNames(goal.targets),
        recordTitle(goal.period_start, `至 ${goal.period_end || "—"}`),
        goalMetricProgress(goal), stateBadge(goal.state), detail];
    });
    list.replaceChildren(rows.length
      ? observationTable(["运营目标", "机器 / 账号", "周期", "指标进展", "状态", "详情"], rows, "目标进展")
      : emptyCard(state.goals.length ? "没有符合条件的目标" : "等待经理 Agent 下发目标",
        state.goals.length ? "调整筛选条件查看其他目标。" : "目标同步后，完成情况将显示在这里。"));
    if (selectedGoalId) renderGoalDetail(selectedGoalId);
  }

  function renderGoalDetail(goalId, openDialog = false) {
    const dialog = $("#manager-goal-detail-dialog");
    const goal = state.goals.find((item) => item.goal_id === goalId);
    if (!dialog || !goal) return;
    selectedGoalId = goalId;
    setText("#manager-goal-detail-title", goal.title || goal.goal_id);
    setText("#manager-goal-detail-subtitle", `${goal.goal_id} · v${goal.version || 1} · 经理 Agent 下发`);
    const content = $("#manager-goal-detail-body");
    content.replaceChildren(stateBadge(goal.state), factList([
      ["目标账号", targetNames(goal.targets)],
      ["周期", `${goal.period_start || "—"} 至 ${goal.period_end || "—"}`],
      ["期望成果", goal.desired_outcome],
      ["内容方向", (goal.content_topics || []).join("、")],
      ["回收结果", goal.progress?.result_count ?? 0],
      ["更新时间", formatTime(goal.updated_at)],
    ]), goalMetricProgress(goal), element("h3", "", "关联执行记录"));
    const tasks = state.operationTasks.filter((task) => task.goal_id === goalId);
    tasks.forEach((task) => {
      const button = element("button", "manager-related-task", task.title || task.operation_task_id);
      button.type = "button";
      button.dataset.managerOperationDetail = task.operation_task_id;
      button.appendChild(stateBadge(task.state));
      content.appendChild(button);
    });
    if (!tasks.length) content.appendChild(emptyCard("尚未同步执行记录", "经理 Agent 下发后自动展示。"));
    if (openDialog && !dialog.open) dialog.showModal();
  }

  function renderProgress() {
    populateOperationFilters();
    renderGoals();
    renderOperationTasks();
    renderPartialErrors();
  }

  function selectProgressTab(tab) {
    selectedProgressTab = tab === "executions" ? "executions" : "goals";
    document.querySelectorAll("[data-manager-progress-tab]").forEach((button) => {
      const active = button.dataset.managerProgressTab === selectedProgressTab;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
    });
    $("#manager-goals-panel").hidden = selectedProgressTab !== "goals";
    $("#manager-executions-panel").hidden = selectedProgressTab !== "executions";
    $("#manager-operation-filter-state").value = "";
    setText("#manager-progress-date-label", selectedProgressTab === "goals" ? "目标周期内日期" : "任务创建日期");
    renderProgress();
  }

  function populateOperationFilters() {
    const records = selectedProgressTab === "goals" ? state.goals : state.operationTasks;
    const stateSelect = $("#manager-operation-filter-state");
    const machineSelect = $("#manager-operation-filter-machine");
    const accountSelect = $("#manager-operation-filter-account");
    const populate = (select, label, values, formatLabel = String) => {
      if (!select) return;
      const previous = select.value;
      const placeholder = element("option", "", label);
      placeholder.value = "";
      const options = [...new Set(values.filter(Boolean))].sort().map((value) => {
        const option = element("option", "", formatLabel(value));
        option.value = value;
        return option;
      });
      select.replaceChildren(placeholder, ...options);
      if (options.some((option) => option.value === previous)) select.value = previous;
    };
    populate(
      stateSelect,
      "全部状态",
      records.map((task) => String(task.state || "UNKNOWN").toUpperCase()),
      labelFor,
    );
    populate(
      machineSelect,
      "全部员工机",
      records.flatMap((task) =>
        (task.targets || []).map((target) => String(target.machine_id || ""))),
    );
    populate(
      accountSelect,
      "全部账号",
      records.flatMap((task) =>
        (task.targets || []).map((target) => String(target.account_slot || ""))),
    );
  }

  function operationTaskMatchesFilters(task) {
    const selectedState = $("#manager-operation-filter-state")?.value || "";
    const machineId = $("#manager-operation-filter-machine")?.value || "";
    const accountSlot = $("#manager-operation-filter-account")?.value || "";
    const selectedDate = $("#manager-operation-filter-date")?.value || "";
    const keyword = ($("#manager-operation-filter-keyword")?.value || "").trim().toLowerCase();
    const targets = task.targets || [];
    const createdAt = new Date(task.created_at);
    const createdDate = Number.isNaN(createdAt.getTime()) ? "" : [
      createdAt.getFullYear(),
      String(createdAt.getMonth() + 1).padStart(2, "0"),
      String(createdAt.getDate()).padStart(2, "0"),
    ].join("-");
    const haystack = [
      task.operation_task_id,
      task.title,
      task.instructions,
      task.manager_agent_id,
      task.goal_snapshot?.title,
      ...targets.flatMap((target) => [target.machine_id, target.account_slot]),
    ].filter(Boolean).join(" ").toLowerCase();
    return (!selectedState || task.state === selectedState)
      && ((!machineId && !accountSlot) || targets.some((target) =>
        (!machineId || target.machine_id === machineId)
        && (!accountSlot || target.account_slot === accountSlot)))
      && (!selectedDate || createdDate === selectedDate)
      && (!keyword || haystack.includes(keyword));
  }

  function operationTaskPriority(task) {
    const priorities = {
      RECONCILIATION_REQUIRED: 0,
      BLOCKED: 0,
      RUNNING: 1,
      DISPATCHING: 1,
      PLANNING: 1,
      WAITING_AGENT: 2,
      COMPLETED: 3,
    };
    return priorities[task.state] ?? 2;
  }

  function renderOperationTasks() {
    const list = $("#manager-operation-list");
    if (!list) return;
    setText("#manager-operation-total", state.operationTasks.length);
    setText("#manager-operation-waiting", state.operationTasks.filter((task) => task.state === "WAITING_AGENT").length);
    setText("#manager-operation-running", state.operationTasks.filter((task) =>
      ["PLANNING", "DISPATCHING", "RUNNING"].includes(task.state)).length);
    setText("#manager-operation-reconcile", state.operationTasks.filter((task) =>
      task.state === "RECONCILIATION_REQUIRED").length);
    const tasks = state.operationTasks
      .filter((task) => selectedProgressTab !== "executions" || operationTaskMatchesFilters(task))
      .sort((a, b) => operationTaskPriority(a) - operationTaskPriority(b)
        || String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
    const rows = tasks.map((task) => {
      const detail = element("button", "manager-text-button", "查看详情");
      detail.type = "button";
      detail.dataset.managerOperationDetail = task.operation_task_id;
      return [recordTitle(task.title, task.goal_snapshot?.title || task.goal_id),
        targetNames(task.targets), stateBadge(task.state),
        recordTitle(task.result_summary || dailySessionLabel(task) || task.plan?.summary || "等待 Agent 回传进展",
          `已回收 ${task.progress?.result_count || 0} 条结果`),
        formatTime(task.updated_at), detail];
    });
    list.replaceChildren(rows.length
      ? observationTable(["执行任务 / 关联目标", "机器 / 账号", "阶段", "结果摘要", "更新时间", "详情"], rows, "执行记录")
      : emptyCard(state.operationTasks.length ? "没有符合条件的执行记录" : "等待经理 Agent 下发任务",
        state.operationTasks.length ? "调整筛选条件查看其他记录。" : "执行计划和结果回收后自动展示。"));
    if (selectedOperationTaskId) renderOperationTaskDetail(selectedOperationTaskId);
  }

  function operationTaskMessages(operationTaskId) {
    return state.agentMessages
      .filter((message) => message.operation_task_id === operationTaskId)
      .sort((left, right) => String(left.created_at || "").localeCompare(String(right.created_at || "")));
  }

  function operationMessageRole(message) {
    const sender = String(message.from_agent || "").toUpperCase();
    if (sender.includes("MANAGER")) return "manager";
    if (sender.includes("WEBUI") || sender.includes("USER")) return "user";
    return "worker";
  }

  function operationMessageStatus(message) {
    return message.payload?.stage === "outbox_written" && message.status === "PENDING"
      ? "PENDING_PULL" : message.status || message.message_type;
  }

  function operationTimelineItem(message) {
    const item = element("article", "manager-agent-timeline-item");
    item.dataset.role = operationMessageRole(message);
    const marker = element("span", "manager-agent-timeline-marker");
    const content = element("div", "manager-agent-timeline-content");
    const head = element("div", "manager-card-head");
    const copy = element("div", "manager-record-copy");
    append(
      copy,
      element(
        "strong",
        "",
        messageTypeLabels[message.message_type] || message.message_type || "Agent 消息",
      ),
      element(
        "small",
        "",
        `${message.from_agent || "未知发送方"} → ${message.to_agent || "未知接收方"}`,
      ),
    );
    append(head, copy, stateBadge(operationMessageStatus(message)));
    append(content, head);
    if (message.summary) content.appendChild(element("p", "manager-inline-message", message.summary));
    const payload = message.payload && typeof message.payload === "object" ? message.payload : {};
    const stage = String(payload.stage || "");
    const metrics = payload.metrics && typeof payload.metrics === "object" ? payload.metrics : {};
    if (stage || Object.keys(metrics).length) {
      const progress = element("div", "manager-timeline-progress");
      if (stage) {
        progress.appendChild(
          element("span", "manager-timeline-stage", progressStageLabels[stage] || stage),
        );
      }
      Object.entries(metrics).forEach(([name, value]) => {
        const metric = value && typeof value === "object" ? value : {};
        progress.appendChild(
          element(
            "span",
            "manager-timeline-metric",
            `${metricLabels[name] || name} ${metric.actual ?? 0}/${metric.target ?? "—"}`,
          ),
        );
      });
      content.appendChild(progress);
    }
    if (stage === "comment_generated" && payload.content) {
      content.appendChild(element("blockquote", "manager-timeline-comment", payload.content));
    }
    content.appendChild(element("time", "manager-compact-time", formatTime(message.created_at)));
    if (Object.keys(payload).length) {
      const details = element("details", "manager-agent-payload");
      details.appendChild(element("summary", "", "技术信息"));
      const pre = element("pre");
      pre.textContent = JSON.stringify(payload, null, 2);
      details.appendChild(pre);
      content.appendChild(details);
    }
    append(item, marker, content);
    return item;
  }

  function dailySessionLabel(task) {
    const labels = {WAITING_WINDOW: "等待执行窗口", WAITING_RECEIPT: "等待执行回执",
      WAITING_SPACING: "等待动作间隔", WAITING_ACCOUNT: "等待账号就绪",
      IDENTITY_CHANGED: "账号身份已变化", DISPATCHED: "已派发当前动作",
      WAITING_REQUIREMENTS: "等待目标、素材或剩余预算处理", WAITING_APPROVAL: "等待发布预览确认",
      WAITING_RECONCILIATION: "等待结果未知动作对账", WAITING_POLICY: "等待运营规则允许执行",
      WINDOW_ENDED: "执行窗口结束", COMPLETED: "每日硬指标已完成"};
    return labels[task.daily_session?.status] || "";
  }

  function renderOperationTaskDetail(operationTaskId, openDialog = false) {
    const dialog = $("#manager-operation-detail-dialog");
    const task = state.operationTasks.find(
      (item) => item.operation_task_id === operationTaskId,
    );
    if (!dialog || !task) {
      selectedOperationTaskId = "";
      return;
    }
    selectedOperationTaskId = operationTaskId;
    const progress = task.progress || {};
    const messages = operationTaskMessages(operationTaskId);
    const executionTasks = state.tasks.filter(
      (item) => String(item.request?.operation_task_id || "") === operationTaskId,
    );
    const results = state.results.filter(
      (item) => String(item.operation_task_id || "") === operationTaskId,
    );
    const targets = (task.targets || [])
      .map((target) => `${target.machine_id || "LOCAL"} / ${target.account_slot || "—"}`)
      .join("、");
    const personas = (task.persona_bindings || [])
      .map((persona) => `${persona.display_name || persona.persona_id} · v${persona.version || 1}`)
      .join("、");

    setText("#manager-operation-detail-title", task.title || operationTaskId);
    setText(
      "#manager-operation-detail-subtitle",
      `${operationTaskId} · ${labelFor(task.state)} · 更新 ${formatTime(task.updated_at)}`,
    );
    const summary = $("#manager-operation-detail-summary");
    summary?.replaceChildren(
      ...[
        ["当前状态", labelFor(task.state)],
        ["经理 Agent", task.manager_agent_id || "等待领取"],
        ["计划动作", progress.plan_action_count || 0],
        ["执行任务", progress.execution_task_count || 0],
        ["目标 / 实际", `${progress.target_total ?? "—"} / ${progress.actual_total ?? "—"}`],
        ["回收结果", progress.result_count || 0],
      ].map(([label, value]) => {
        const card = element("article");
        append(card, element("span", "", label), element("strong", "", value));
        return card;
      }),
    );

    const goal = $("#manager-operation-detail-goal");
    goal?.replaceChildren(
      factList([
        ["运营目标", task.goal_snapshot?.title || task.goal_id],
        ["目标账号", targets || "尚未指定"],
        ["人设快照", personas || "尚未绑定"],
        ["运营要求", task.instructions || "按运营目标执行"],
      ]),
    );
    const plan = $("#manager-operation-detail-plan");
    plan?.replaceChildren(
      factList([
        ["计划摘要", task.plan?.summary || "等待经理 Agent 生成计划"],
        ["完成标准", task.completion_criteria || "按运营目标完成并回传结果"],
        ["计划时间", formatTime(task.planned_at)],
        ["最终结论", task.result_summary || "等待结果回收与对账"],
        ...(task.daily_material ? [
          ["每日计划版本", `${task.daily_material.plan_date} · v${task.daily_material.plan_version}`],
          ["每日会话", dailySessionLabel(task) || "等待经理领取"],
          ["执行窗口", `${formatTime(task.daily_material.time_window?.start_at)} — ${formatTime(task.daily_material.time_window?.end_at)}`],
          ["剩余预算", Object.entries(task.daily_session?.remaining || {}).map(([name, count]) => `${labelFor(name)} ${count}`).join("、") || "等待核销"],
          ["结果未知预留", Object.entries(task.daily_session?.reserved_unknown || {}).filter(([, count]) => count > 0).map(([name, count]) => `${labelFor(name)} ${count}`).join("、") || "0"],
          ["输入待办", (task.daily_session?.input_messages || []).join("；") ||
            (task.daily_session?.pending_requirements || task.daily_material.pending_requirements || []).map((item) => item.reason).join("；") || "已具备执行条件"],
        ] : []),
      ]),
    );

    const executionList = $("#manager-operation-detail-executions");
    executionList?.replaceChildren(
      ...(executionTasks.length
        ? executionTasks.map(compactTaskCard)
        : [emptyCard("尚未生成员工机任务", "经理 Agent 完成规划并派发后会显示在这里。")]),
    );
    const resultList = $("#manager-operation-detail-results");
    resultList?.replaceChildren(
      ...(results.length
        ? results.map(operationTaskResultCard)
        : [emptyCard("尚未回收执行结果", "员工机写入 outbox 并被经理机回收后会显示在这里。")]),
    );
    setText("#manager-operation-timeline-total", `${messages.length} 条`);
    const timeline = $("#manager-operation-timeline");
    timeline?.replaceChildren(
      ...(messages.length
        ? messages.map(operationTimelineItem)
        : [emptyCard("尚未产生 Agent 交接记录", "下一步等待经理 Agent 领取运营任务。")]),
    );
    const technical = $("#manager-operation-technical-json");
    if (technical) {
      technical.textContent = JSON.stringify(
        { operation_task: task, execution_tasks: executionTasks, results },
        null,
        2,
      );
    }
    if (openDialog && !dialog.open) {
      dialog.showModal();
      scheduleRefresh();
    }
  }

  function closeOperationTaskDetail() {
    const dialog = $("#manager-operation-detail-dialog");
    selectedOperationTaskId = "";
    if (dialog?.open) dialog.close();
    scheduleRefresh();
  }

  function selectAccountNode(nodeId) {
    selectedAccountNode = String(nodeId || "all");
    localStorage.setItem("auto-xhs:manager-account-node", selectedAccountNode);
    renderAccountWorkspace();
  }

  async function refreshRemoteAccounts(event) {
    const button = event.currentTarget;
    const targets = selectedAccountNode === "all"
      ? state.machines
      : state.machines.filter((machine) => machine.machine_id === selectedAccountNode);
    if (!targets.length) {
      setMessage("#manager-account-message", "等待员工机登记后同步账号。", "success");
      return;
    }
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    setMessage("#manager-account-message", `正在通过 SSH 同步 ${targets.length} 个员工机…`);
    try {
      const results = await Promise.allSettled(
        targets.map((machine) =>
          fetchJson(`${managerApi}/machines/${encodeURIComponent(machine.machine_id)}/accounts`),
        ),
      );
      const [accountsPayload, alertsPayload] = await Promise.all([
        fetchJson(`${managerApi}/accounts`),
        fetchJson(`${managerApi}/alerts`),
      ]);
      state.accounts = accountsPayload.accounts || [];
      state.alerts = alertsPayload.alerts || [];
      renderAll();
      const failed = results.filter(
        (result) => result.status === "rejected" || result.value?.success === false,
      ).length;
      setMessage(
        "#manager-account-message",
        failed
          ? `账号缓存已更新，${failed} 个员工机当前无法读取。`
          : `已同步 ${targets.length} 个员工机的账号状态。`,
        failed ? "warning" : "success",
      );
    } catch (error) {
      setMessage("#manager-account-message", error.message, "error");
    } finally {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }

  function renderMachines() {
    const list = $("#manager-machine-list");
    if (!list) return;
    setText("#node-total", state.machines.length);
    populateMachineSelects();
    renderMachineNavigation();
    const attention = state.machines.filter((machine) => machine.status !== "READY").length;
    setText("#manager-attention-badge", state.machines.length
      ? (attention ? `${attention} 台机器需要关注` : "员工机状态正常") : "等待员工机同步");
    const badge = $("#manager-attention-badge");
    if (badge) badge.dataset.tone = attention || !state.machines.length ? "warning" : "success";
    const cards = state.accounts.map(accountThumbnailCard);
    list.replaceChildren(...(cards.length ? cards
      : [emptyCard("等待账号同步", "员工机上报的每个账号会独立显示在这里。")]));
    renderMachineStage();
  }

  function accountThumbnailCard(account) {
    const machine = state.machines.find((item) => item.machine_id === account.machine_id);
    const persona = accountPersona(account);
    const name = account.nickname || account.account_slot;
    const platform = account.platform === "rednote" ? "RedNote" : "小红书";
    const status = account.health_state || "UNKNOWN";
    const card = element("button", "room-node-card room-account-card");
    card.type = "button";
    card.dataset.employeeAccountSelect = employeeAccountKey(account);
    card.dataset.tone = toneFor(status);
    card.setAttribute("aria-label", `查看 ${name} 账号详情 · ${account.machine_id}`);
    const head = append(element("div", "room-node-head"),
      append(element("div"), element("small", "", `${platform} · ${account.account_slot}`),
        element("h3", "", name)), stateBadge(status));
    const facts = element("dl");
    [["所属机器", account.machine_id],
      ["账号人设", persona?.display_name || persona?.persona_id || "待配置"],
      ["设备连接", labelFor(machine?.ssh_state || "UNKNOWN")],
      ["当前任务", account.current_task_title || "暂无任务"]]
      .forEach(([label, value]) => facts.appendChild(append(element("div"),
        element("dt", "", label), element("dd", "", value))));
    append(card, head, facts,
      append(element("div", "room-node-account"), element("span", "", "账号 UID"),
        element("strong", "", account.current_uid || account.uid || "身份待同步")),
      element("small", "room-node-updated", `最近更新 ${formatTime(account.updated_at)}`));
    return card;
  }

  function machineThumbnailCard(machine) {
    const accounts = state.accounts.filter((account) => account.machine_id === machine.machine_id);
    const card = element("button", "room-node-card");
    card.type = "button";
    card.dataset.managerMachineSelect = machine.machine_id;
    card.dataset.tone = toneFor(machine.status);
    card.setAttribute("aria-label", `查看 ${machine.host_alias || machine.machine_id} 员工机详情`);
    const head = append(element("div", "room-node-head"),
      append(element("div"), element("small", "", `员工机 · ${machine.machine_id}`),
        element("h3", "", machine.host_alias || machine.machine_id)), stateBadge(machine.status));
    const personas = [...new Set(accounts.map((account) => {
      const persona = accountPersona(account);
      return persona?.display_name || persona?.persona_id;
    }).filter(Boolean))];
    const facts = element("dl");
    [["物理资产", machine.machine_id], ["执行单元", `${accounts.length} 个账号`],
      ["账号人设", personas.join("、") || "待配置"],
      ["连接状态", labelFor(machine.ssh_state || "UNKNOWN")],
      ["运行状态", labelFor(machine.status)], ["项目版本", machine.installed_version || "待同步"]]
      .forEach(([name, value]) => facts.appendChild(append(element("div"),
        element("dt", "", name), element("dd", "", value))));
    append(card, head, facts);
    accounts.forEach((account) => card.appendChild(append(element("div", "room-node-account"),
      element("span", "", account.nickname || account.account_slot),
      element("strong", "", `${account.account_slot} · ${account.current_uid || account.uid || "身份待同步"}`))));
    if (!accounts.length) card.appendChild(element("p", "room-footnote", "等待账号绑定回执"));
    card.appendChild(element("small", "room-node-updated",
      `最近更新 ${formatTime(machine.outbox_collection?.observed_at || machine.updated_at)}`));
    return card;
  }

  function renderOverviewSummary() {
    const accounts = state.accounts;
    setText("#manager-online-total", state.machines.filter((machine) => machine.ssh_state === "ONLINE").length);
    setText("#manager-machine-caption", `${state.machines.filter((machine) => machine.ssh_state === "ONLINE").length} 台连接正常`);
    setText("#manager-healthy-total", accounts.filter((account) => account.health_state === "READY").length);
    setText("#manager-account-caption", `共 ${accounts.length} 个账号`);
    setText("#manager-completed-goal-total", state.goals.filter((goal) => goal.state === "COMPLETED").length);
    setText("#manager-overview-result-total", state.results.length);
    const exceptions = operationalExceptions();
    setText("#manager-overview-exception-total", exceptions.length);
    renderControlRoom(exceptions);
    const results = [...state.results].sort((a, b) => activityTime(b) - activityTime(a)).slice(0, 4);
    $("#manager-overview-results")?.replaceChildren(...(results.length
      ? results.map(compactTodayResultCard)
      : [emptyCard("等待运营成果回收", "经理 Agent 回收执行结果后自动更新。")]));
    const alerts = exceptions.slice(0, 4).map((item) => append(
      element("article", "manager-overview-alert"),
      append(element("div", "manager-card-head"), recordTitle(item.title, item.object),
        stateBadge(item.state === "SUCCESS" ? "WARNING" : item.state)),
      element("p", "", item.message), element("small", "", formatTime(item.time))));
    $("#manager-overview-alerts")?.replaceChildren(...(alerts.length
      ? alerts : [emptyCard("当前没有待关注异常", "最近同步的机器、账号与结果状态正常。")]));
  }

  function controlRoomDayMetrics(results) {
    return ["browse", "like", "comment", "reply", "publish"].map((kind) => ({
      kind,
      actual: actionTargetActualText(results, kind).split(" / ")[1],
    }));
  }

  function renderControlRoom(exceptions) {
    const online = state.machines.filter((machine) => machine.ssh_state === "ONLINE").length;
    const attention = state.machines.filter((machine) => machine.status !== "READY");
    const today = localDateValue(new Date());
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    const yesterdayDate = localDateValue(yesterday);
    const todayResults = state.results.filter((result) => resultDate(result) === today);
    const yesterdayResults = state.results.filter((result) => resultDate(result) === yesterdayDate);
    const actuals = todayResults.map(actualResultCount).filter((value) => value !== null);
    setText("#room-machine-total", state.machines.length);
    setText("#room-node-count", `${state.accounts.length} 个账号 · 所属 ${state.machines.length} 台机器`);
    setText("#room-unbound-total", state.machines.filter((machine) =>
      !state.accounts.some((account) => account.machine_id === machine.machine_id)).length);
    setText("#room-today-total", actuals.length ? actuals.reduce((sum, value) => sum + value, 0) : "—");
    setText("#room-command-title", !state.machines.length ? "等待员工机接入"
      : attention.length ? `${attention.length} 台员工机需要关注` : "员工机集群运行正常");
    setText("#room-command-description", `${online} 台员工机在线，${state.accounts.length} 个账号已登记。`
      + (exceptions.length ? `当前有 ${exceptions.length} 项异常与提醒，可在下方查看详情。`
        : "执行进度与实际回执随同步更新。"));
    $("#room-command").dataset.tone = attention.length || exceptions.length ? "warning" : "success";
    setText("#room-report-date", yesterdayDate);
    const labels = { browse: "浏览", like: "点赞", comment: "评论", reply: "回复", publish: "发布" };
    [["#room-yesterday-metrics", yesterdayResults], ["#room-today-metrics", todayResults]]
      .forEach(([selector, results]) => $(selector).replaceChildren(
        ...controlRoomDayMetrics(results).map(({ kind, actual }) => append(element("div"),
          element("span", "", labels[kind]), element("strong", "", actual))),
      ));
    setText("#room-yesterday-note", yesterdayResults.length
      ? `已回收 ${yesterdayResults.length} 条结果；— 表示该项实际数量尚未回传。`
      : "暂无昨日回执，员工机回收结果后更新日报。");
  }

  function taskPrimaryState(task) {
    return task.business_state || task.state || task.dispatch_state || "PENDING_DISPATCH";
  }

  function taskParameterSummary(parameters) {
    if (!parameters || typeof parameters !== "object" || Array.isArray(parameters)) return "—";
    const entries = Object.entries(parameters).slice(0, 4);
    if (!entries.length) return "—";
    return entries.map(([key, value]) => `${key}=${String(value)}`).join(" · ");
  }

  function taskNeedsReconciliation(task) {
    return Boolean(task.reconciliation_required) || reconciliationStates.has(taskPrimaryState(task));
  }

  function taskIsActive(task) {
    return !terminalTaskStates.has(taskPrimaryState(task));
  }

  function taskIsExecuting(task) {
    return ["ACCEPTED", "ACKNOWLEDGED", "RUNNING", "PAUSE_REQUESTED"]
      .includes(taskPrimaryState(task));
  }

  function taskActions(task) {
    const actions = element("div", "manager-card-actions");
    const dispatchState = String(task.dispatch_state || "").toUpperCase();
    if (taskNeedsReconciliation(task)) {
      actions.appendChild(actionButton("读取并对账", "task-reconcile", task.task_id, "primary"));
      return actions;
    }
    if (["ACCEPTED", "RUNNING", "PAUSE_REQUESTED"].includes(dispatchState)) {
      actions.appendChild(actionButton("读取状态", "task-reconcile", task.task_id));
    }
    return actions;
  }

  function renderSchedules() {
    const list = $("#manager-schedule-list");
    if (!list) return;
    const active = state.schedules.filter((item) => item.state === "ACTIVE").length;
    const paused = state.schedules.filter((item) => item.state === "PAUSED").length;
    setText("#manager-schedule-active", active);
    setText("#manager-schedule-paused", paused);
    if (!state.schedules.length) {
      list.replaceChildren(
        emptyCard("暂无持续运营计划", "Agent 登记计划后会显示在这里。"),
      );
      return;
    }
    const cards = state.schedules.map((schedule) => {
      const card = element("article", "manager-task-card");
      const head = element("div", "manager-card-head");
      const copy = element("div", "manager-record-copy");
      append(
        copy,
        element("strong", "", schedule.request_summary || schedule.schedule_id),
        element("small", "", `${schedule.schedule_id} · ${schedule.machine_id || "—"}`),
      );
      append(head, copy, stateBadge(schedule.state));
      append(
        card,
        head,
        factList([
          ["账号", schedule.account_slot],
          ["能力", schedule.capability],
          ["运营任务", schedule.operation_task_id],
          ["运营目标", schedule.goal_snapshot?.title || schedule.goal_id],
          [
            "人设快照",
            schedule.persona_snapshot?.persona_id
              ? schedule.persona_snapshot.display_name || schedule.persona_snapshot.persona_id
              : "",
          ],
          ["规则", taskParameterSummary(schedule.rule)],
          ["时间窗口", taskParameterSummary(schedule.time_window)],
          ["配额", taskParameterSummary(schedule.quota)],
          ["下次运行", formatTime(schedule.next_run_at)],
          ["最近结果", labelFor(schedule.last_result_state || "UNKNOWN")],
          ["最近运行", formatTime(schedule.last_run_at)],
        ]),
      );
      return card;
    });
    list.replaceChildren(...cards);
  }

  function transferState(transfer) {
    return transfer.state || transfer.status || "PREPARING";
  }

  function renderTransfers() {
    const list = $("#manager-transfer-list");
    if (!list) return;
    if (!state.transfers.length) {
      list.replaceChildren(emptyCard("暂无传输记录", "功能包传输完成后会保留版本和目标路径。"));
      return;
    }
    const cards = state.transfers.map((transfer) => {
      const request = transfer.request || {};
      const card = element("article", "manager-transfer-card");
      const head = element("div", "manager-card-head");
      const copy = element("div", "manager-record-copy");
      append(
        copy,
        element("strong", "", transfer.transfer_id || "未命名传输"),
        element("small", "", `${transfer.machine_id || "—"} · ${request.transfer_type || transfer.transfer_type || "—"}`),
      );
      append(head, copy, stateBadge(transferState(transfer)));
      append(
        card,
        head,
        factList([
          ["功能", transfer.function_id],
          ["实际版本", transfer.actual_version],
          ["目标路径", transfer.installed_path || transfer.remote_path],
          ["更新时间", formatTime(transfer.updated_at)],
        ]),
      );
      if (transfer.message) card.appendChild(element("p", "manager-inline-message", transfer.message));
      return card;
    });
    list.replaceChildren(...cards);
  }

  function taskForResult(result) {
    return state.tasks.find((task) => task.task_id === result.task_id) || {};
  }

  function resultRequest(result) {
    const task = taskForResult(result);
    if (result.task_request && typeof result.task_request === "object") {
      return result.task_request;
    }
    return task.request && typeof task.request === "object" ? task.request : {};
  }

  function resultAccount(result) {
    return String(result.account_slot || resultRequest(result).account_slot || "");
  }

  function resultCapability(result) {
    return String(result.capability || resultRequest(result).capability || "").toLowerCase();
  }

  function resultActionKind(result) {
    const capability = resultCapability(result);
    if (capability === "follow-user") return "friend_request";
    if (capability.includes("reply") || capability.includes("private-message")) return "reply";
    if (capability.includes("comment")) return "comment";
    if (capability.includes("like")) return "like";
    if (capability.includes("publish") || capability.includes("article")) return "publish";
    if (
      capability.includes("feed")
      || capability.includes("search")
      || capability.includes("browse")
    ) return "browse";
    return "other";
  }

  function numericCount(...values) {
    const match = values.find((value) => value !== "" && value !== null && value !== undefined);
    if (match === undefined) return null;
    const count = Number(match);
    return Number.isFinite(count) && count >= 0 ? count : null;
  }

  function requestedResultCount(result) {
    const request = resultRequest(result);
    const parameters = request.parameters && typeof request.parameters === "object"
      ? request.parameters
      : {};
    if (resultCapability(result) === "home-engagement"
      && ("browse_count" in parameters || "like_count" in parameters)) {
      return (numericCount(parameters.browse_count) || 0)
        + (numericCount(parameters.like_count) || 0);
    }
    const explicit = numericCount(
      result.target_count,
      result.expected_count,
      request.target_count,
      parameters.target_count,
      parameters.expected_count,
      parameters.count,
      parameters.limit,
    );
    if (explicit !== null) return explicit;
    if (Array.isArray(parameters.target_ids)) return parameters.target_ids.length;
    if (request.target_id) return 1;
    return ["like", "comment", "reply", "publish"].includes(resultActionKind(result)) ? 1 : null;
  }

  function actualResultCount(result) {
    const explicit = numericCount(result.actual_count, result.completed_count);
    if (explicit !== null) return explicit;
    const itemStatus = result.item_results_status
      || ("item_results" in result ? "observed" : "missing");
    if (itemStatus === "observed" && Array.isArray(result.item_results)) {
      if (resultCapability(result) === "home-engagement") {
        return result.item_results.reduce((total, item) => {
          const browsed = String(item?.browse?.status || "").toUpperCase() === "SUCCESS";
          const liked = item?.like?.success === true
            || String(item?.like?.status || "").toUpperCase() === "SUCCESS";
          return total + Number(browsed) + Number(liked);
        }, 0);
      }
      if (result.item_results.some((item) => !item?.state && !item?.status)) return null;
      return result.item_results.filter((item) =>
        ["SUCCESS", "COMPLETED"].includes(
          String(item?.state || item?.status || "").toUpperCase(),
        )
      ).length;
    }
    if (String(result.state || result.status || "").toUpperCase() === "SUCCESS") {
      return requestedResultCount(result) === 1 ? 1 : null;
    }
    return null;
  }

  function localDateValue(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  function resultDate(result) {
    return localDateValue(
      result.recorded_at || result.collected_at || result.updated_at || result.created_at,
    );
  }

  function resultMatchesFilters(result) {
    const machineId = $("#manager-result-machine")?.value || "";
    const account = $("#manager-result-account")?.value || "";
    const start = $("#manager-result-start")?.value || "";
    const end = $("#manager-result-end")?.value || "";
    const date = resultDate(result);
    return (!machineId || result.machine_id === machineId)
      && (!account || resultAccount(result) === account)
      && (!start || (date && date >= start))
      && (!end || (date && date <= end));
  }

  function populateResultAccounts() {
    const select = $("#manager-result-account");
    if (!select) return;
    const previous = select.value;
    const accounts = new Set(
      state.results.map(resultAccount).filter(Boolean),
    );
    const placeholder = element("option", "", "全部账号");
    placeholder.value = "";
    const options = [...accounts].sort().map((account) => {
      const option = element("option", "", account);
      option.value = account;
      return option;
    });
    select.replaceChildren(placeholder, ...options);
    if (accounts.has(previous)) select.value = previous;
  }

  function filteredResults() {
    return state.results.filter(resultMatchesFilters);
  }

  function filteredResultHistory() {
    return state.resultHistory.filter(resultMatchesFilters);
  }

  function filteredIncrementalRecords() {
    return state.incrementalRecords.filter((record) => {
      const result = {
        ...record,
        task_id: record.source_task_id,
        recorded_at: record.created_at || record.collected_at,
      };
      return resultMatchesFilters(result);
    });
  }

  function targetActualText(values) {
    const targets = values.map(requestedResultCount).filter((value) => value !== null);
    const actuals = values.map(actualResultCount).filter((value) => value !== null);
    const target = targets.length ? targets.reduce((total, value) => total + value, 0) : "—";
    const actual = actuals.length ? actuals.reduce((total, value) => total + value, 0) : "—";
    return `${target} / ${actual}`;
  }

  function actionTargetActualText(results, kind) {
    const values = results.flatMap((result) => {
      if (resultCapability(result) !== "home-engagement") {
        return resultActionKind(result) === kind ? [result] : [];
      }
      if (!["browse", "like"].includes(kind)) return [];
      const target = numericCount(resultRequest(result).parameters?.[`${kind}_count`]);
      const observed = resultDataStatus(result, "item_results") === "observed"
        && Array.isArray(result.item_results);
      const actual = observed ? result.item_results.filter((item) =>
        item?.[kind]?.success === true
        || String(item?.[kind]?.status || "").toUpperCase() === "SUCCESS"
      ).length : null;
      return [{capability: "metric", target_count: target, actual_count: actual,
        item_results_status: "missing"}];
    });
    return targetActualText(values);
  }

  function resultDuration(result) {
    const seconds = numericCount(result.duration_seconds, result.elapsed_seconds);
    if (seconds === null) return "未回传";
    if (seconds < 60) return `${seconds} 秒`;
    return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
  }

  function resultDataStatus(result, field) {
    return result[`${field}_status`] || (field in result ? "observed" : "missing");
  }

  function renderBusinessResult(result) {
    const status = resultDataStatus(result, "business_result");
    const business = result.business_result;
    if (status !== "observed" || !business || typeof business !== "object") {
      const reason = result.business_result_missing_reason;
      return reason
        ? element("p", "manager-inline-message", `业务数据：${reason}`)
        : null;
    }

    const feeds = Array.isArray(business.feeds) ? business.feeds : [];
    const count = numericCount(business.count)
      ?? (Array.isArray(business.feeds) ? feeds.length : null);
    const panel = element("details", "manager-business-result");
    panel.appendChild(element("summary", "", count === null
      ? "业务数据 · 数量未回传" : `业务数据 · ${count} 条`));

    if (!feeds.length) {
      panel.appendChild(element("p", "manager-inline-message", "业务结果已回收。"));
      return panel;
    }

    const list = element("div", "manager-business-list");
    feeds.forEach((feed, index) => {
      const card = element("article", "manager-business-item");
      const title = feed.displayTitle || feed.title || `内容 ${index + 1}`;
      const author = feed.user?.nickname || feed.author?.nickname || "作者未记录";
      const identity = [author, feed.type, feed.id].filter(Boolean).join(" · ");
      append(card, element("strong", "", title), element("small", "", identity));

      const interaction = feed.interactInfo || feed.interact_info;
      if (interaction && typeof interaction === "object") {
        const metrics = [
          ["赞", interaction.likedCount ?? interaction.liked_count],
          ["藏", interaction.collectedCount ?? interaction.collected_count],
          ["评", interaction.commentCount ?? interaction.comment_count],
        ]
          .filter(([, value]) => value !== undefined && value !== null && value !== "")
          .map(([label, value]) => `${label} ${value}`)
          .join(" · ");
        if (metrics) card.appendChild(element("span", "", metrics));
      }
      list.appendChild(card);
    });
    panel.appendChild(list);
    return panel;
  }

  function resultCompleteness(result) {
    const itemStatus = result.item_results_status
      || ("item_results" in result ? "observed" : "missing");
    const incrementalStatus = result.incremental_data_status
      || ("incremental_data" in result ? "observed" : "missing");
    const businessStatus = resultDataStatus(result, "business_result");
    return `业务 ${labelFor(businessStatus)} · 逐项 ${labelFor(itemStatus)} · 增量 ${labelFor(incrementalStatus)}`;
  }

  function resultItemValue(value) {
    if (value === true) return "是";
    if (value === false) return "否";
    return value;
  }

  function resultItemDetails(result) {
    const items = Array.isArray(result.item_results) ? result.item_results : [];
    if (!items.length) return null;
    const details = element("details", "manager-result-items");
    details.appendChild(element("summary", "", `查看逐项结果（${items.length}）`));
    const list = element("div", "manager-result-item-list");
    items.forEach((item, index) => {
      const browse = item.browse && typeof item.browse === "object" ? item.browse : {};
      const like = item.like && typeof item.like === "object" ? item.like : {};
      const card = element("article", "manager-result-item");
      append(
        card,
        element(
          "strong",
          "",
          item.title || item.note_title || item.feed_title || `第 ${index + 1} 项`,
        ),
        factList([
          ["作者", item.author_name || item.author || "未回传"],
          ["Feed ID", item.feed_id || item.note_id || item.id],
          ["打开详情", resultItemValue(browse.opened ?? item.opened ?? item.open_detail
            ?? (browse.status ? labelFor(String(browse.status).toUpperCase()) : undefined))],
          ["阅读时长", resultItemValue(browse.duration_seconds ?? item.duration_seconds
            ?? item.read_seconds)],
          ["点赞", resultItemValue(like.success ?? like.status ?? item.liked)],
          ["评论", item.comment_text || item.comment || ""],
          ["结果", item.state || item.status || ""],
          ["错误", item.error || item.message || ""],
        ]),
      );
      list.appendChild(card);
    });
    details.appendChild(list);
    return details;
  }

  function operationTaskResultCard(result) {
    const card = compactTodayResultCard(result);
    const details = resultItemDetails(result);
    if (details) card.appendChild(details);
    return card;
  }

  function resultAuditCard(result) {
    const card = element("article", "manager-result-card manager-result-audit-card");
    const head = element("div", "manager-card-head");
    const copy = element("div", "manager-record-copy");
    append(
      copy,
      element("strong", "", result.task_id || result.remote_execution_id || "远程回执"),
      element(
        "small",
        "",
        `${result.machine_id || "—"} · 修订 ${result.result_revision || "初始"}`,
      ),
    );
    append(
      head,
      copy,
      stateBadge(result.is_current_revision ? "CURRENT" : result.state || result.status),
    );
    append(
      card,
      head,
      factList([
        ["原始状态", result.state || result.status],
        ["回传序号", result.sequence],
        ["Dispatch ID", result.dispatch_id],
        ["执行编号", result.remote_execution_id],
        ["修订来源", result.revision_source || result.source_type || "员工机 outbox"],
        ["产生时间", formatTime(result.recorded_at || result.created_at)],
        ["回收时间", formatTime(result.collected_at)],
        ["原始错误", result.error_code || result.error || ""],
      ]),
    );
    return card;
  }

  function operationalExceptions() {
    const exceptions = [];
    state.machines
      .filter((machine) => !["READY", "PAUSED"].includes(String(machine.status || "").toUpperCase()))
      .forEach((machine) => exceptions.push({
        title: `${machine.machine_id} · 员工机状态异常`,
        object: machine.host_alias || machine.machine_id,
        state: machine.status || "UNKNOWN",
        time: machine.last_check_at,
        message: machine.last_error || `SSH ${labelFor(machine.ssh_state)}`,
        recommendation: "检查 SSH 连接与员工机执行服务。",
      }));
    state.alerts.forEach((alert) => exceptions.push({
      title: `${alert.machine_id || "—"} / ${alert.account_slot || "—"} · 账号异常`,
      object: alert.reason || "账号状态待处理",
      state: alert.severity || "WARNING",
      time: alert.last_seen_at,
      message: alert.reason || "账号状态待确认",
      recommendation: alert.recommended_action || "进入员工机账号详情处理。",
    }));
    state.tasks
      .filter((task) => taskNeedsReconciliation(task)
        || ["FAILED", "BLOCKED"].includes(String(task.business_state || "").toUpperCase()))
      .forEach((task) => exceptions.push({
        title: `${task.task_id} · 任务需要处理`,
        object: task.machine_id || "未指定员工机",
        state: taskPrimaryState(task),
        time: task.updated_at,
        message: task.message || task.result_summary || "任务状态需要核对",
        recommendation: task.recommended_action || "读取员工机状态并完成对账。",
        operationTaskId: task.request?.operation_task_id,
    }));
    state.results.forEach((result) => {
      const resultState = String(result.state || result.status || "").toUpperCase();
      const itemStatus = String(
        result.item_results_status || ("item_results" in result ? "observed" : "missing"),
      ).toUpperCase();
      const incrementalStatus = String(
        result.incremental_data_status
          || ("incremental_data" in result ? "observed" : "missing"),
      ).toUpperCase();
      const businessStatus = resultDataStatus(result, "business_result");
      const incomplete = itemStatus !== "OBSERVED" || incrementalStatus !== "OBSERVED"
        || businessStatus !== "observed";
      if (!["FAILED", "BLOCKED", "RESULT_UNKNOWN", "PARTIAL_SUCCESS"].includes(resultState)
        && !result.manual_action_required && !incomplete) return;
      exceptions.push({
        title: `${result.task_id || result.remote_execution_id} · 结果异常`,
        object: `${result.machine_id || "—"} / ${resultAccount(result) || "—"}`,
        state: resultState || "WARNING",
        time: result.collected_at || result.recorded_at,
        message: result.result_summary || result.error || result.error_code || resultCompleteness(result),
        recommendation: result.recommended_action || "查看逐项结果和回收审计。",
        operationTaskId: result.operation_task_id,
      });
    });
    const resultSync = state.sync.results || {};
    if (!["", "SUCCESS", "IDLE", "NEVER"].includes(String(resultSync.status || "").toUpperCase())) {
      exceptions.push({
        title: "员工机 outbox 回收异常",
        object: "经理机结果回收",
        state: resultSync.status,
        time: resultSync.updated_at || resultSync.completed_at,
        message: resultSync.message || resultSync.error || "最近一次结果回收未正常完成",
        recommendation: "检查员工机 outbox、SSH 与 SFTP 回收状态。",
      });
    }
    return exceptions.sort((left, right) => {
      const leftTime = new Date(left.time || "").getTime() || 0;
      const rightTime = new Date(right.time || "").getTime() || 0;
      return rightTime - leftTime;
    });
  }

  function renderExceptions() {
    const list = $("#manager-exception-list");
    if (!list) return;
    const exceptions = operationalExceptions();
    setText("#manager-exception-total", exceptions.length);
    const cards = exceptions.map((exception) => {
      const card = element("article", "manager-alert-card manager-exception-card");
      const head = element("div", "manager-card-head");
      const copy = element("div", "manager-record-copy");
      append(
        copy,
        element("strong", "", exception.title),
        element("small", "", exception.object),
      );
      append(head, copy, stateBadge(exception.state));
      append(
        card,
        head,
        factList([
          ["发生时间", formatTime(exception.time)],
          ["关联运营任务", exception.operationTaskId],
          ["当前情况", exception.message],
          ["建议动作", exception.recommendation],
        ]),
      );
      return card;
    });
    list.replaceChildren(
      ...(cards.length
        ? cards
        : [emptyCard("当前没有待处理异常", "员工机、账号、任务和回收链路状态正常。")]),
    );
  }

  function collectionStatus(machine) {
    const status = machine.result_sync_status || "NEVER";
    const observedAt = Date.parse(machine.outbox_collection?.observed_at || "");
    const interval = Number(state.sync.results?.interval_seconds || 60);
    const stale = !Number.isFinite(observedAt) || Date.now() - observedAt > interval * 3000;
    if (["OFFLINE", "BLOCKED", "FAILED", "RESULT_UNKNOWN"].includes(status)) {
      return { status, cached: true };
    }
    return { status: stale ? "UNKNOWN" : status, cached: stale || status === "PARTIAL_SUCCESS" };
  }

  function renderOutboxCollection() {
    const list = $("#manager-collection-list");
    if (!list) return;
    const sync = state.sync.results;
    setText("#manager-collection-schedule", sync
      ? `每 ${sync.interval_seconds || 60} 秒 · 下次 ${formatTime(sync.next_run_at)}`
      : "等待后台首次收集");
    const cards = state.machines.map((machine) => {
      const card = element("article", "manager-result-card");
      const head = element("div", "manager-card-head");
      const current = collectionStatus(machine);
      const receipt = machine.outbox_collection || {};
      const observed = Number.isFinite(Date.parse(receipt.observed_at || ""));
      const badge = stateBadge(current.status);
      if (["IDLE", "SUCCESS"].includes(current.status) && receipt.pending_records === 0) {
        badge.textContent = "已收齐";
      }
      append(head, element("strong", "", machine.machine_id), badge);
      append(card, head, factList([
        ["最近尝试", formatTime(machine.last_result_sync_at)],
        ["最近成功", formatTime(machine.last_result_success_at)],
        [current.cached ? "上次已知待收" : "待收回执", observed ? String(receipt.pending_records) : "未知"],
        ["已导入 / 回执总数", observed ? `${receipt.imported_records} / ${receipt.total_records}` : "未知"],
        ["本轮导入", observed ? `${receipt.batch_records} 份（结果 ${receipt.batch_results}、进度 ${receipt.batch_events}、接收确认 ${receipt.batch_acknowledgements}）` : "尚未收集"],
      ]));
      if (current.cached) card.appendChild(element("p", "manager-inline-message", observed
        ? `以上计数为最近一次读取记录：${formatTime(receipt.observed_at)}。`
        : "等待首次有效的回执索引。"));
      if (machine.result_sync_error) {
        card.appendChild(element("p", "manager-inline-message", current.status === "OFFLINE"
          ? "暂时无法连接员工机，下一轮将自动重试。" : machine.result_sync_error));
        if (current.status === "OFFLINE") {
          const details = element("details", "manager-operation-technical");
          append(details, element("summary", "", "连接错误详情"),
            element("p", "manager-inline-message", machine.result_sync_error));
          card.appendChild(details);
        }
      }
      if (receipt.archive_root) {
        const details = element("details", "manager-operation-technical");
        append(details, element("summary", "", "原始回执归档位置"),
          element("p", "manager-inline-message", receipt.archive_root));
        card.appendChild(details);
      }
      return card;
    });
    list.replaceChildren(...(cards.length ? cards : [emptyCard("尚未登记员工机", "登记员工机后自动开始收集回执。")]));
  }

  function renderResults() {
    renderOutboxCollection();
    populateResultAccounts();
    const results = filteredResults();
    setText("#manager-result-total", results.length);
    const terminal = results.filter((item) =>
      terminalTaskStates.has(String(item.business_state || item.state || item.status || "").toUpperCase()));
    const successful = terminal.filter((item) =>
      ["SUCCESS", "COMPLETED"].includes(String(item.business_state || item.state || item.status || "").toUpperCase()));
    setText("#manager-success-rate", terminal.length
      ? `${Math.round(successful.length / terminal.length * 100)}%` : "—");
    ["browse", "like", "comment", "reply", "publish"].forEach((kind) => {
      setText(`#manager-action-${kind}`, actionTargetActualText(results, kind));
    });
    resultView?.render({results, resultHistory: filteredResultHistory(),
      incremental: filteredIncrementalRecords()});
    renderExceptions();
  }

  function resultDetailCard(result) {
    const card = element("article", "manager-result-card");
    const head = element("div", "manager-card-head");
    const copy = element("div", "manager-record-copy");
    append(
      copy,
      element("strong", "", result.result_summary || result.task_id || "远程结果"),
      element("small", "", `${result.machine_id || "—"} · ${result.task_id || result.remote_execution_id || "—"}`),
    );
    append(head, copy, stateBadge(result.state || result.status));
    append(
      card,
      head,
      factList([
        ["账号", resultAccount(result) || "未回传"],
        ["员工机", result.machine_id],
        ["实际 / 目标", `${actualResultCount(result) ?? "—"} / ${requestedResultCount(result) ?? "—"}`],
        ["执行耗时", resultDuration(result)],
        ["产生时间", formatTime(result.recorded_at || result.created_at)],
        ["回收时间", formatTime(result.collected_at)],
      ]),
    );
    if (result.recommended_action) {
      card.appendChild(
        element("p", "manager-inline-message", `建议动作：${result.recommended_action}`),
      );
    }
    const links = resultLinks(result);
    if (links.length) {
      const row = element("div", "manager-result-links");
      links.forEach((item) => {
        const link = element("a", "", item.label);
        link.href = item.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        row.appendChild(link);
      });
      card.appendChild(row);
    }
    const businessResult = renderBusinessResult(result);
    if (businessResult) card.appendChild(businessResult);
    const itemDetails = resultItemDetails(result);
    if (itemDetails) card.appendChild(itemDetails);
    const technical = element("details", "recovery-detail-technical");
    append(technical, element("summary", "", "任务信息与数据完整性"), factList([
      ["动作接口", resultCapability(result) || result.function_id],
      ["运营任务", result.operation_task_id], ["运营目标", result.goal_id],
      ["人设快照", result.persona_id], ["功能", result.function_id],
      ["版本", result.actual_version || result.project_version],
      ["数据完整性", resultCompleteness(result)],
    ]));
    card.appendChild(technical);
    return card;
  }

  function operationReportCard(report) {
    const definition = report.definition || {};
    const content = report.content || {};
    const card = element("article", "manager-result-card");
    const head = element("div", "manager-card-head");
    const copy = element("div", "manager-record-copy");
    append(
      copy,
      element(
        "strong",
        "",
        `${definition.report_type === "weekly" ? "周报" : "日报"} · v${report.revision_number || 1}`,
      ),
      element("small", "", report.report_id),
    );
    append(head, copy, stateBadge(report.data_status));
    append(
      card,
      head,
      factList([
        ["执行机", definition.machine_id || "全部"],
        ["账号", definition.account_slot || "全部"],
        ["周期", `${formatTime(definition.start_at)} 至 ${formatTime(definition.end_at)}`],
        ["目标 / 实际", `${content.target_total ?? "—"} / ${content.actual_total ?? "—"}`],
        ["成功率", content.success_rate == null ? "—" : `${Math.round(content.success_rate * 100)}%`],
        ["数据缺口", content.data_gap_count ?? "—"],
        ["生成时间", formatTime(report.created_at)],
      ]),
    );
    if (report.missing_reason) {
      card.appendChild(element("p", "manager-inline-message", report.missing_reason));
    }
    if (report.revision_reason) {
      card.appendChild(
        element("p", "manager-inline-message", `修订原因：${report.revision_reason}`),
      );
    }
    return card;
  }

  function renderVersions() {
    const list = $("#manager-version-list");
    if (!list) return;
    if (!state.versions.length) {
      list.replaceChildren(emptyCard("暂无功能版本记录", "经理 Agent 同步版本后自动展示。"));
      return;
    }
    const cards = state.versions.map((version) => {
      const card = element("article", "manager-version-card");
      const head = element("div", "manager-card-head");
      const copy = element("div", "manager-record-copy");
      append(
        copy,
        element("strong", "", `${version.function_id || "未标识功能"} ${version.version || ""}`.trim()),
        element("small", "", version.machine_id || "—"),
      );
      append(head, copy, stateBadge(version.status || version.version_status));
      append(
        card,
        head,
        factList([
          ["稳定版本", version.stable ? "是" : version.stable_version || "否"],
          ["安装路径", version.installed_path],
        ]),
      );
      return card;
    });
    list.replaceChildren(...cards);
  }

  function renderUnavailable(error) {
    state.available = false;
    selectedMachineId = "";
    const message = error?.code === "MANAGER_SERVICE_UNAVAILABLE"
      ? "当前 WebUI 未启用集群管理服务。"
      : `暂时无法读取集群状态：${error?.message || "请求失败"}`;
    const machineList = $("#manager-machine-list");
    const machineNav = $("#manager-machine-nav");
    if (machineList) machineList.replaceChildren(errorCard(message));
    if (machineNav) machineNav.replaceChildren();
    setText("#manager-last-read", "读取失败");
    setText("#room-live-state", "连接异常");
    setText("#room-command-title", "集群状态暂时不可用");
    setText("#room-command-description", message);
    $("#room-live-dot").dataset.tone = "danger";
    $("#room-command").dataset.tone = "danger";
    setText("#manager-health-sync", "状态不可用");
    setText("#manager-result-sync", "状态不可用");
    setText("#manager-attention-badge", "状态不可用");
    const attentionBadge = $("#manager-attention-badge");
    if (attentionBadge) attentionBadge.dataset.tone = "danger";
    renderMachineNavigation();
    renderMachineStage();
    setMessage("#manager-cluster-message", message, "error");
  }

  function renderAll() {
    planning?.render();
    employeePanel?.render(selectedEmployeeAccount);
    renderMachines();
    renderPublishReviews();
    renderAccountWorkspace();
    populateOperationFilters();
    renderGoals();
    renderOperationTasks();
    renderOverviewSummary();
    renderSchedules();
    renderTransfers();
    renderResults();
    renderVersions();
    renderSyncStatus();
    renderSystemSettings();
  }

  function renderSystemSettings() {
    const system = state.errors.system ? {} : state.system;
    const limits = system.l1_limits || {};
    setText("#manager-setting-concurrency", system.global_concurrency);
    setText("#manager-setting-hourly", limits.hourly);
    setText("#manager-setting-daily", limits.daily);
    setText("#manager-setting-dedup", limits.dedup_minutes);
    setText("#manager-setting-failures", limits.failure_threshold);
    setText("#manager-runtime-version", `版本 ${system.product_version || "—"}`);
    setText("#manager-system-pause-state", state.errors.system ? "读取失败"
      : system.global_paused ? "全局暂停" : "正常运行");
    setText("#manager-system-storage", system.storage_root || "—");
    setText("#manager-system-task-total", system.summary?.tasks_total);
    setText("#manager-system-failure-total", system.summary?.recent_failures);
    $("#manager-settings-machines").replaceChildren(...(state.machines.length
      ? state.machines.map((machine) => append(element("article", "manager-settings-machine"),
        append(element("div", "manager-card-head"),
          recordTitle(machine.host_alias || machine.machine_id, machine.machine_id),
          stateBadge(machine.status)),
        factList([
          ["经理机", machine.manager_id],
          ["SSH 地址", `${machine.ssh_user || "—"}@${machine.ssh_host || "—"}:${machine.ssh_port ?? 22}`],
          ["连接状态", labelFor(machine.ssh_state || "UNKNOWN")],
          ["项目版本", machine.installed_version],
          ["项目目录", machine.working_directory],
          ["Python", machine.python_executable],
          ["允许版本", (machine.allowed_versions || []).join("、")],
          ["最近更新", formatTime(machine.updated_at)],
        ])))
      : [emptyCard("等待员工机配置", "经理 Agent 登记员工机后自动展示连接信息。")]));
  }

  function syncStatusText(record) {
    if (!record) return "等待首次同步";
    const status = labelFor(record.status || "UNKNOWN");
    const completedAt = formatTime(record.completed_at || record.updated_at);
    return `${status} · ${completedAt}`;
  }

  function renderSyncStatus() {
    setText("#manager-health-sync", syncStatusText(state.sync.health_accounts));
    setText("#manager-result-sync", syncStatusText(state.sync.results));
    const staleMachines = state.machines.filter((machine) => machine.stale).length;
    const staleAccounts = state.accounts.filter((account) => account.stale).length;
    setText("#manager-stale-total", `${staleMachines + staleAccounts} 项`);
    const records = [state.sync.health_accounts, state.sync.results].filter(Boolean);
    setText("#room-collection-state", !records.length ? "等待首次同步"
      : records.some((record) => record.status !== "SUCCESS") ? "同步状态需要关注" : "自动同步正常");
  }

  function selectManagerView(viewId, updateHash = true) {
    const allowed = new Set([
      "overview",
      "employee-units",
      "planning",
      "workers",
      "accounts",
      "results",
      "exceptions",
      "settings",
    ]);
    const next = allowed.has(viewId) ? viewId : "overview";
    viewScrollPositions.set(selectedView, window.scrollY);
    selectedView = next;
    $(".control-room").dataset.currentView = next;
    renderMachineNavigation();
    if (next === "workers") renderMachineStage();
    if (next === "employee-units") employeePanel?.render(selectedEmployeeAccount);
    localStorage.setItem(VIEW_STORAGE_KEY, next);
    document.querySelectorAll("[data-manager-view]").forEach((view) => {
      const active = view.dataset.managerView === next;
      view.hidden = !active;
      view.classList.toggle("active", active);
    });
    document.querySelectorAll("[data-manager-view-link]").forEach((link) => {
      const active = link.dataset.managerViewLink === next;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    if ((updateHash || viewId === "operations") && window.location.hash !== `#${next}`) {
      window.history.replaceState(null, "", `#${next}`);
    }
    window.requestAnimationFrame(() => window.scrollTo(0, viewScrollPositions.get(next) || 0));
  }

  function renderPartialErrors() {
    const failed = Object.keys(state.errors);
    if (!failed.length) return;
    const labels = {
      machines: "机器",
      tasks: "任务",
      transfers: "传输",
      results: "结果",
      accounts: "账号",
      alerts: "告警",
      sync: "同步状态",
      schedules: "调度计划",
      operationReports: "运营报告",
      personas: "账号人设卡",
      goals: "运营目标",
      operationTasks: "执行记录",
    };
    const summary = failed.map((key) => labels[key] || key).join("、");
    setMessage(
      "#manager-cluster-message",
      `${summary}缓存暂时读取失败，其余区域已更新。`,
      "warning",
    );
    if ((state.errors.accounts || state.errors.alerts) && !state.accounts.length) {
      const grid = $("#manager-remote-account-grid");
      if (grid) grid.replaceChildren(errorCard("员工机账号缓存暂时不可用。"));
    }
    if (state.errors.results && !state.results.length) {
      const list = $("#manager-result-list");
      if (list) list.replaceChildren(errorCard("执行结果缓存暂时不可用。"));
    }
    if (state.errors.machines && !state.machines.length) {
      const list = $("#manager-machine-list");
      if (list) list.replaceChildren(errorCard("机器缓存暂时不可用。"));
    }
    if (state.errors.accounts || state.errors.alerts) {
      setMessage("#manager-account-message", "账号或告警缓存暂时不可用。", "warning");
    }
    if (state.errors.tasks) {
      setMessage("#manager-task-message", "Agent 任务缓存暂时不可用。", "warning");
    }
    if (state.errors.schedules && !state.schedules.length) {
      const list = $("#manager-schedule-list");
      if (list) list.replaceChildren(errorCard("Agent 调度计划缓存暂时不可用。"));
    }
    if (state.errors.operationReports && !state.operationReports.length) {
      const list = $("#manager-operation-report-list");
      if (list) list.replaceChildren(errorCard("持续运营报告缓存暂时不可用。"));
    }
    if (state.errors.goals && !state.goals.length) {
      const list = $("#manager-goal-list");
      if (list) list.replaceChildren(errorCard("运营目标暂时不可用。"));
    }
    if (state.errors.operationTasks && !state.operationTasks.length) {
      const list = $("#manager-operation-list");
      if (list) list.replaceChildren(errorCard("执行记录暂时不可用。"));
    }
    if (state.errors.results) {
      setMessage("#manager-collect-message", "执行结果缓存暂时不可用。", "warning");
    }
    if (state.errors.transfers) {
      setMessage("#manager-transfer-message", "传输记录缓存暂时不可用。", "warning");
    }
    if (state.errors.sync) {
      setText("#manager-health-sync", "读取失败");
      setText("#manager-result-sync", "读取失败");
    }
  }

  async function performLoad() {
    state.errors = {};
    const [
      snapshotResult,
      localTasksResult,
      systemResult,
    ] = await Promise.allSettled([
      fetchJson(`${managerApi}/dashboard-snapshot`),
      fetchJson(`${api}/tasks`),
      fetchJson(`${api}/system/status`),
    ]);
    if (snapshotResult.status === "rejected") {
      const error = snapshotResult.reason;
      state.errors.snapshot = error;
      renderUnavailable(error);
      return false;
    }
    const snapshot = snapshotResult.value;
    if (localTasksResult.status === "fulfilled") {
      state.localTasks = localTasksResult.value.tasks || [];
    } else {
      state.errors.localTasks = localTasksResult.reason;
    }
    if (systemResult.status === "fulfilled") {
      state.system = systemResult.value;
    } else {
      state.errors.system = systemResult.reason;
    }
    state.available = true;
    state.snapshot = snapshot;
    state.machines = snapshot.machines || [];
    state.tasks = snapshot.tasks || [];
    state.schedules = snapshot.schedules || [];
    state.operationReports = snapshot.operation_reports || [];
    state.transfers = snapshot.transfers || [];
    state.versions = snapshot.versions || [];
    state.results = snapshot.results || [];
    state.resultHistory = snapshot.result_history || state.results;
    state.incrementalRecords = snapshot.incremental_records || [];
    state.resultSummary = snapshot.result_summary || {};
    state.accounts = snapshot.accounts || [];
    state.alerts = snapshot.alerts || [];
    state.sync = snapshot.collection || {};
    state.personas = snapshot.personas || [];
    state.goals = snapshot.goals || [];
    state.dailyPlans = snapshot.daily_plans || [];
    state.employeeSummary = snapshot.employee_summary || {};
    state.operationTasks = snapshot.operation_tasks || [];
    state.agentMessages = snapshot.agent_messages || [];
    renderAll();
    const readTime = new Date().toLocaleTimeString("zh-CN", { hour12: false });
    setText("#manager-last-read", readTime);
    setText("#room-live-state", "自动更新中");
    $("#room-live-dot").dataset.tone = "success";
    setMessage("#manager-cluster-message", `集群状态已更新 · ${readTime}`, "success");
    setMessage("#manager-account-message", `账号缓存已更新 · ${readTime}`, "success");
    return true;
  }

  function scheduleRefresh() {
    window.clearTimeout(refreshTimer);
    if (document.hidden) return;
    const delay = selectedOperationTaskId
      ? TASK_REFRESH_INTERVAL_MS
      : DASHBOARD_REFRESH_INTERVAL_MS;
    setText("#room-refresh-interval", `每 ${delay / 1000} 秒刷新`);
    setText("#room-collection-interval", `${delay / 1000} 秒`);
    refreshTimer = window.setTimeout(() => load(), delay);
  }

  function load() {
    if (refreshPromise) return refreshPromise;
    window.clearTimeout(refreshTimer);
    refreshPromise = performLoad().finally(() => {
      refreshPromise = null;
      scheduleRefresh();
    });
    return refreshPromise;
  }

  function businessPayload(error) {
    return error?.payload && !error.payload.error ? error.payload : null;
  }

  async function runMutation(button, path, body, messageSelector, pendingMessage) {
    const pendingKey = button.dataset.managerAccountAction
      ? `account-${button.dataset.managerAccountAction}:${button.dataset.managerMachine}:${button.dataset.managerAccount}`
      : button.dataset.managerAction
        ? `${button.dataset.managerAction}:${button.dataset.managerIdentity}`
        : "";
    if (pendingKey) pendingActions.add(pendingKey);
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    setMessage(messageSelector, pendingMessage);
    let payload;
    let requestError = null;
    try {
      payload = await mutateJson(path, "POST", body);
    } catch (error) {
      payload = businessPayload(error);
      if (!payload) {
        setMessage(messageSelector, error.message, "error");
        requestError = error;
      }
    }
    await load();
    if (pendingKey) {
      pendingActions.delete(pendingKey);
      renderAll();
    }
    if (requestError) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
      throw requestError;
    }
    const status = payload?.status ? labelFor(payload.status) : "操作已完成";
    const message = payload?.message || payload?.machine?.last_error || status;
    setMessage(messageSelector, message, payload?.success === false ? "warning" : "success");
    button.disabled = false;
    button.removeAttribute("aria-busy");
    return payload;
  }

  async function handleMachineAction(button) {
    const machineId = button.dataset.managerIdentity;
    const action = button.dataset.managerAction.replace("machine-", "");
    const labels = {
      check: "连接检查",
      "executor-start": "启动执行服务",
      "executor-restart": "重启执行服务",
      pause: "暂停调度",
      resume: "恢复调度",
      collect: "结果回收",
    };
    if (!window.confirm(`确认对 ${machineId} 执行“${labels[action]}”？`)) return;
    const path = action === "collect"
      ? `${managerApi}/machines/${encodeURIComponent(machineId)}/collect`
      : `${managerApi}/machines/${encodeURIComponent(machineId)}/${action}`;
    const body = action === "collect" ? { download_evidence: true } : {};
    await runMutation(button, path, body, "#manager-cluster-message", `正在执行${labels[action]}…`);
  }

  async function handleTaskAction(button) {
    const taskId = button.dataset.managerIdentity;
    const path = `${managerApi}/tasks/${encodeURIComponent(taskId)}/reconcile`;
    await runMutation(
      button,
      path,
      {},
      "#manager-task-message",
      "正在读取远程状态…",
    );
  }

  function openAccountManagement(machineId, accountSlot) {
    const account = state.accounts.find((item) => {
      return item.machine_id === machineId && item.account_slot === accountSlot;
    });
    if (!account) return;
    state.activeManagedAccount = account;
    setText(
      "#manager-account-management-subtitle",
      `${account.machine_id} / ${account.account_slot}`,
    );
    $("#manager-account-note").value = account.account_note || "";
    $("#manager-account-scheduling-enabled").checked = account.scheduling_enabled !== false;
    const fieldRequest = account.field_request || {};
    setText(
      "#manager-account-field-state",
      fieldRequest.request_id
        ? `${labelFor(fieldRequest.state)} · ${fieldRequest.request_id}`
        : "当前没有现场请求",
    );
    $("#manager-account-field-instructions").value = fieldRequest.instructions || "";
    setMessage("#manager-account-management-message", "");
    $("#manager-account-management-dialog").showModal();
  }

  async function submitAccountManagement(event) {
    event.preventDefault();
    const account = state.activeManagedAccount;
    if (!account) return;
    const button = event.submitter || event.currentTarget.querySelector("button[type='submit']");
    button.disabled = true;
    setMessage("#manager-account-management-message", "正在保存经理机账号配置…");
    try {
      const path = `${managerApi}/machines/${encodeURIComponent(account.machine_id)}/accounts/${encodeURIComponent(account.account_slot)}/management`;
      const payload = await mutateJson(path, "PATCH", {
        account_note: $("#manager-account-note").value.trim(),
        scheduling_enabled: $("#manager-account-scheduling-enabled").checked,
      });
      state.activeManagedAccount = payload.account;
      setMessage(
        "#manager-account-management-message",
        payload.status === "SUCCESS"
          ? "账号管理信息已保存并同步到员工机。"
          : `经理机配置已保存，员工机同步待确认：${payload.message || "等待员工机回执"}`,
        payload.status === "SUCCESS" ? "success" : "warning",
      );
      await load();
    } catch (error) {
      setMessage("#manager-account-management-message", error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function requestAccountFieldAction(requestType, button) {
    const account = state.activeManagedAccount;
    if (!account) return;
    const actionLabel = requestType === "LOGIN" ? "现场登录" : "身份核对";
    if (!window.confirm(`确认向 ${account.machine_id} / ${account.account_slot} 发起${actionLabel}？`)) {
      return;
    }
    button.disabled = true;
    setMessage("#manager-account-management-message", `正在发起${actionLabel}请求…`);
    try {
      const path = `${managerApi}/machines/${encodeURIComponent(account.machine_id)}/accounts/${encodeURIComponent(account.account_slot)}/field-request`;
      const payload = await mutateJson(path, "POST", {
        request_type: requestType,
        instructions: $("#manager-account-field-instructions").value.trim(),
      });
      state.activeManagedAccount = payload.account;
      setText(
        "#manager-account-field-state",
        `${labelFor(payload.field_request?.state)} · ${payload.field_request?.request_id || "—"}`,
      );
      setMessage(
        "#manager-account-management-message",
        `${actionLabel}请求已发送到目标员工机。`,
        "success",
      );
      await load();
    } catch (error) {
      setMessage("#manager-account-management-message", error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function handleAccountAction(button) {
    const machineId = button.dataset.managerMachine;
    const account = button.dataset.managerAccount;
    const action = button.dataset.managerAccountAction;
    if (action === "manage") {
      openAccountManagement(machineId, account);
      return;
    }
    if (action === "identity-check") {
      const target = state.accounts.find((item) => item.machine_id === machineId
        && item.account_slot === account);
      if (target) await accountTools.open("identity-check", target);
      return;
    }
    const labels = {
      doctor: "运行账号诊断",
      "profile-start": "启动账号 Profile",
      "bridge-restart": "重启账号 Bridge",
    };
    if (!window.confirm(`确认对 ${machineId} / ${account} 执行“${labels[action]}”？`)) return;
    const suffixes = {
      doctor: "doctor",
      "profile-start": "profile/start",
      "bridge-restart": "bridge/restart",
    };
    const path = `${managerApi}/machines/${encodeURIComponent(machineId)}/accounts/${encodeURIComponent(account)}/${suffixes[action]}`;
    await runMutation(
      button,
      path,
      {},
      "#manager-account-message",
      `正在${labels[action]}…`,
    );
  }

  async function collectSelectedMachine(event) {
    const button = event.currentTarget;
    const machineId = $("#manager-result-machine").value;
    if (!machineId) {
      setMessage("#manager-collect-message", "请先选择需要回收结果的执行机。", "error");
      return;
    }
    if (!window.confirm(`确认立即连接 ${machineId} 并回收结果？`)) return;
    try {
      await runMutation(
        button,
        `${managerApi}/machines/${encodeURIComponent(machineId)}/collect`,
        { download_evidence: $("#manager-collect-evidence").checked },
        "#manager-collect-message",
        "正在回收结果…",
      );
      renderResults();
    } catch (_error) {
      // runMutation has rendered the user-facing error.
    }
  }

  function setResultDateRange() {
    const end = new Date();
    const start = new Date(end);
    start.setDate(start.getDate() - 29);
    const startInput = $("#manager-result-start");
    const endInput = $("#manager-result-end");
    if (startInput) startInput.value = localDateValue(start);
    if (endInput) endInput.value = localDateValue(end);
  }

  function bindEvents() {
    planning = window.AutoXhsPlanning({state, mutateJson, managerApi, localDateValue,
      workerDayMetrics, metricLabels, formatTime});
    planning.init();
    accountTools = window.AutoXhsAccountTools({state, mutateJson, fetchJson, managerApi, load});
    employeePanel = window.AutoXhsEmployeePanel({state, workerDayMetrics, nextWorkerPlan, mutateJson, managerApi, load,
      localDateValue, resultDate, resultAccount, resultLinks, resultActionKind, resultCapability, workerTaskDate,
      accountPersona, labelFor, operationalExceptions});
    employeePanel.init();
    resultView = window.AutoXhsResultView({state, element, append, stateBadge, formatTime,
      resultAccount, resultCapability, actualResultCount, requestedResultCount,
      resultDetailCard, resultAuditCard, operationReportCard, factList, collectionStatus,
      resultMatchesFilters});
    resultView.init();
    $("#manager-account-add").addEventListener("click", () => accountTools.open("create"));
    $("#manager-persona-editor-form").addEventListener("submit", savePersonaEditor);
    $("#manager-persona-editor-close").addEventListener("click", () => {
      $("#manager-persona-editor").close();
    });
    $("#manager-persona-editor").addEventListener("close", () => { editingPersona = null; });
    document.querySelectorAll("[data-manager-progress-tab]").forEach((button) => {
      button.addEventListener("click", () => selectProgressTab(button.dataset.managerProgressTab));
      button.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const tab = event.key === "Home" ? "goals" : event.key === "End" ? "executions"
          : selectedProgressTab === "goals" ? "executions" : "goals";
        selectProgressTab(tab);
        document.querySelector(`[data-manager-progress-tab="${tab}"]`).focus();
      });
    });
    $("#manager-progress-reset")?.addEventListener("click", () => {
      document.querySelectorAll(".manager-progress-filters input, .manager-progress-filters select")
        .forEach((control) => { control.value = ""; });
      renderProgress();
    });
    $("#manager-goal-detail-close")?.addEventListener("click", () => $("#manager-goal-detail-dialog").close());
    $("#manager-goal-detail-dialog")?.addEventListener("close", () => { selectedGoalId = ""; });
    $("#manager-refresh")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      await load();
      button.disabled = false;
      button.removeAttribute("aria-busy");
    });
    $("#manager-collect-results")?.addEventListener("click", collectSelectedMachine);
    [
      "#manager-result-machine",
      "#manager-result-account",
      "#manager-result-start",
      "#manager-result-end",
    ].forEach((selector) => $(selector)?.addEventListener("change", renderResults));
    $("#manager-result-reset")?.addEventListener("click", () => {
      const machine = $("#manager-result-machine");
      const account = $("#manager-result-account");
      if (machine) machine.value = "";
      if (account) account.value = "";
      setResultDateRange();
      renderResults();
    });
    $("#manager-account-refresh")?.addEventListener("click", refreshRemoteAccounts);
    $("#manager-account-management-form")?.addEventListener(
      "submit",
      submitAccountManagement,
    );
    $("#manager-account-management-close")?.addEventListener("click", () => {
      $("#manager-account-management-dialog")?.close();
    });
    $("#manager-account-management-dialog")?.addEventListener("close", () => {
      state.activeManagedAccount = null;
    });
    $("#manager-account-request-login")?.addEventListener("click", (event) => {
      requestAccountFieldAction("LOGIN", event.currentTarget);
    });
    $("#manager-account-request-identity")?.addEventListener("click", (event) => {
      requestAccountFieldAction("IDENTITY_CHECK", event.currentTarget);
    });
    [
      "#manager-account-filter-status",
      "#manager-account-filter-persona",
      "#manager-account-filter-task",
    ].forEach((selector) => $(selector)?.addEventListener("change", renderAccountWorkspace));
    $("#manager-operation-detail-close")?.addEventListener("click", closeOperationTaskDetail);
    $("#manager-operation-detail-dialog")?.addEventListener("close", () => {
      selectedOperationTaskId = "";
      scheduleRefresh();
    });
    $("#publish-dialog-close")?.addEventListener("click", () => {
      $("#publish-confirm-dialog")?.close();
    });
    $("#publish-dialog-dismiss")?.addEventListener("click", () => {
      $("#publish-confirm-dialog")?.close();
    });
    $("#publish-confirm-dialog")?.addEventListener("close", () => {
      state.activePublishTask = null;
    });
    $("#publish-preview-confirmed")?.addEventListener("change", (event) => {
      $("#publish-confirm-submit").disabled = !event.target.checked;
    });
    $("#publish-confirm-submit")?.addEventListener("click", () => {
      executePublishConfirmation("publish-confirm");
    });
    $("#publish-save-draft")?.addEventListener("click", () => {
      executePublishConfirmation("save-draft");
    });
    document.querySelectorAll("[data-manager-view-link]").forEach((link) => {
      link.addEventListener("click", (event) => {
        event.preventDefault();
        selectManagerView(link.dataset.managerViewLink);
      });
    });
    [
      "#manager-operation-filter-state",
      "#manager-operation-filter-machine",
      "#manager-operation-filter-account",
      "#manager-operation-filter-date",
    ].forEach((selector) => $(selector)?.addEventListener("change", renderProgress));
    $("#manager-operation-filter-keyword")?.addEventListener("input", renderProgress);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        window.clearTimeout(refreshTimer);
        return;
      }
      load();
    });
    window.addEventListener("hashchange", () => {
      selectManagerView(window.location.hash.slice(1), false);
    });
    document.addEventListener("click", (event) => {
      const employeeSelection = event.target.closest("[data-employee-account-select]");
      if (employeeSelection) {
        selectEmployeeAccount(employeeSelection.dataset.employeeAccountSelect);
        return;
      }
      const publishReview = event.target.closest("[data-manager-publish-review]");
      if (publishReview) {
        openPublishDialog(publishReview.dataset.managerPublishReview);
        return;
      }
      const goalDetail = event.target.closest("[data-manager-goal-detail]");
      if (goalDetail) {
        renderGoalDetail(goalDetail.dataset.managerGoalDetail, true);
        return;
      }
      const operationDetail = event.target.closest("[data-manager-operation-detail]");
      if (operationDetail) {
        renderOperationTaskDetail(operationDetail.dataset.managerOperationDetail, true);
        return;
      }
      const accountNode = event.target.closest("[data-manager-account-node]");
      if (accountNode) {
        selectAccountNode(accountNode.dataset.managerAccountNode);
        return;
      }
      const selection = event.target.closest("[data-manager-machine-select]");
      if (selection) {
        selectMachine(selection.dataset.managerMachineSelect);
        return;
      }
      const accountButton = event.target.closest("[data-manager-account-action]");
      if (accountButton) {
        handleAccountAction(accountButton).catch((error) => {
          setMessage("#manager-account-message", error.message, "error");
        });
        return;
      }
      const button = event.target.closest("[data-manager-action]");
      if (!button) return;
      const action = button.dataset.managerAction || "";
      const operation = action.startsWith("machine-")
        ? handleMachineAction(button)
        : handleTaskAction(button);
      operation.catch((error) => {
        const selector = action.startsWith("machine-")
          ? "#manager-cluster-message"
          : "#manager-task-message";
        setMessage(selector, error.message, "error");
      });
    });
  }

  function initialize() {
    setResultDateRange();
    bindEvents();
    renderVersions();
    selectManagerView(window.location.hash.slice(1) || selectedView, false);
    load();
  }

  initialize();
  window.AutoXhsManagerConsole = { load, render: renderAll, state };
})();
