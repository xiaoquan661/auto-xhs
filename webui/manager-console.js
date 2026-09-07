(() => {
  "use strict";

  const { api, fetchJson, mutateJson } = window.AutoXhsApi;
  const managerApi = `${api}/manager`;
  const DASHBOARD_REFRESH_INTERVAL_MS = 30_000;
  const TASK_REFRESH_INTERVAL_MS = 5_000;
  const VIEW_STORAGE_KEY = "auto-xhs:manager-view";
  const state = {
    available: true,
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
    localAccounts: [],
    localStatuses: [],
    localTasks: [],
    activePublishTask: null,
    activeManagedAccount: null,
    system: {},
    replyModel: {},
    personas: [],
    goals: [],
    operationTasks: [],
    agentMessages: [],
    snapshot: {},
  };
  const pendingActions = new Set();
  let selectedMachineId = localStorage.getItem("auto-xhs:manager-machine") || "";
  let selectedAccountNode = localStorage.getItem("auto-xhs:manager-account-node") || "all";
  let selectedOperationTaskId = "";
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

  function splitList(value) {
    return String(value || "")
      .split(/[，,\n]/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function generatedId(prefix) {
    const stamp = new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14);
    return `${prefix}-${stamp}-${String(Date.now()).slice(-4)}`;
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
      ["ACTIVE", "READY", "SUCCESS", "ONLINE", "FORMAL", "CURRENT", "OBSERVED"]
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
      "#manager-transfer-machine",
      "#manager-version-machine",
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

  function strategyAccounts(machineId) {
    if (machineId === "LOCAL") {
      return state.localAccounts.map((account) => ({
        account_slot: account.name,
        nickname: account.name,
      }));
    }
    return state.accounts.filter((account) => account.machine_id === machineId);
  }

  function populateStrategyAccount(machineSelector, accountSelector, preferredValue = "") {
    const machine = $(machineSelector)?.value || "LOCAL";
    const select = $(accountSelector);
    if (!select) return;
    const previous = preferredValue || select.value;
    const placeholder = element("option", "", "请选择账号槽位");
    placeholder.value = "";
    select.replaceChildren(placeholder);
    strategyAccounts(machine).forEach((account) => {
      const option = element(
        "option",
        "",
        account.nickname && account.nickname !== account.account_slot
          ? `${account.nickname} · ${account.account_slot}`
          : account.account_slot,
      );
      option.value = account.account_slot;
      select.appendChild(option);
    });
    if ([...select.options].some((option) => option.value === previous)) {
      select.value = previous;
    }
  }

  function populateStrategySelects() {
    const selectors = ["#manager-persona-machine", "#manager-goal-machine"];
    selectors.forEach((selector) => {
      const select = $(selector);
      if (!select) return;
      const previous = select.value || "LOCAL";
      const managerOption = element("option", "", "经理机");
      managerOption.value = "LOCAL";
      select.replaceChildren(managerOption);
      state.machines.forEach((machine) => {
        const option = element("option", "", machine.host_alias || machine.machine_id);
        option.value = machine.machine_id;
        select.appendChild(option);
      });
      select.value = [...select.options].some((option) => option.value === previous)
        ? previous
        : "LOCAL";
    });
    populateStrategyAccount("#manager-persona-machine", "#manager-persona-account");
    populateStrategyAccount("#manager-goal-machine", "#manager-goal-account");
  }

  function populateOperationGoalSelect() {
    const select = $("#manager-operation-goal");
    if (!select) return;
    const previous = select.value;
    const placeholder = element("option", "", "请选择已启用的运营目标");
    placeholder.value = "";
    select.replaceChildren(placeholder);
    state.goals
      .filter((goal) => goal.state === "ACTIVE")
      .forEach((goal) => {
        const target = (goal.targets || [])[0] || {};
        const option = element(
          "option",
          "",
          `${goal.title} · ${target.machine_id || "LOCAL"}/${target.account_slot || "—"}`,
        );
        option.value = goal.goal_id;
        select.appendChild(option);
      });
    if ([...select.options].some((option) => option.value === previous)) {
      select.value = previous;
    }
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

  function machineAvatar(machine) {
    const label = String(machine.host_alias || machine.machine_id || "EX").trim();
    return label.length > 3 ? label.slice(-2).toUpperCase() : label.toUpperCase();
  }

  function machineNavButton(machine) {
    const button = element("button", "manager-machine-nav-item");
    button.type = "button";
    button.dataset.managerMachineSelect = machine.machine_id;
    button.setAttribute("aria-pressed", String(selectedMachineId === machine.machine_id));
    if (selectedMachineId === machine.machine_id) button.classList.add("active");

    const avatar = element("span", "manager-rail-avatar", machineAvatar(machine));
    avatar.dataset.tone = toneFor(machine.status);
    const copy = element("span", "manager-rail-copy");
    append(
      copy,
      element("strong", "", machine.host_alias || machine.machine_id),
      element("small", "", machine.ssh_host || machine.machine_id),
    );
    const alertState = highestAlertState(machine.machine_id);
    const tail = element("span", "manager-node-state");
    if (alertState) {
      const marker = element("i", "manager-node-alert-marker");
      marker.dataset.tone = toneFor(alertState);
      marker.title = `${alertsForMachine(machine.machine_id).length} 个账号需要处理`;
      tail.appendChild(marker);
    }
    tail.appendChild(stateBadge(machine.status));
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
    setText("#manager-rail-total", `${state.machines.length} 个`);
    setText("#manager-overview-count", state.machines.length);
    const overviewActive = !selectedMachineId;
    overviewSwitch.classList.toggle("active", overviewActive);
    overviewSwitch.setAttribute("aria-pressed", String(overviewActive));
    nav.replaceChildren(...state.machines.map(machineNavButton));
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
    const matches = [...state.accounts, ...state.localAccounts.map((account, index) =>
      normalizeLocalAccount(account, state.localStatuses[index]),
    )].filter((account) => account.account_slot === task.account_slot);
    return matches.length === 1 ? matches[0].machine_id : "";
  }

  function publishAccount(task) {
    const machineId = machineIdForPublishTask(task);
    const accounts = [
      ...state.accounts,
      ...state.localAccounts.map((account, index) =>
        normalizeLocalAccount(account, state.localStatuses[index]),
      ),
    ];
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
    }
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

  function renderMachineDetail(machine) {
    const hero = $("#manager-detail-hero");
    const accountList = $("#manager-machine-account-list");
    const taskList = $("#manager-machine-task-list");
    const agentList = $("#manager-machine-agent-list");
    const todayList = $("#manager-machine-today-list");
    const activityList = $("#manager-machine-activity-list");
    const alertList = $("#manager-machine-alert-list");
    if (!hero || !accountList || !taskList || !todayList || !activityList || !alertList) return;

    setText("#manager-machine-detail-kicker", `EXECUTOR · ${machine.machine_id}`);
    setText("#manager-machine-detail-title", machine.host_alias || machine.machine_id);
    setText(
      "#manager-machine-detail-description",
      `${machine.machine_id} 的连接、调度、任务与回读状态。`,
    );

    const head = element("div", "manager-detail-hero-head");
    const title = element("div", "manager-card-title");
    const copy = element("div");
    append(
      copy,
      element("strong", "", machine.host_alias || machine.machine_id),
      element("small", "", machine.machine_id),
    );
    append(title, element("span", "manager-machine-icon", machineAvatar(machine)), copy);
    append(head, title, stateBadge(machine.status));
    const facts = factList(
      [
        ["SSH 地址", `${machine.ssh_user || "—"}@${machine.ssh_host || "—"}:${machine.ssh_port || 22}`],
        ["连接状态", labelFor(machine.ssh_state)],
        ["项目版本", machine.installed_version || "尚未读取"],
        ["允许版本", (machine.allowed_versions || []).join("、") || "—"],
        ["工作目录", machine.working_directory],
        ["最近检查", formatTime(machine.last_check_at)],
      ],
      "manager-detail-facts",
    );
    hero.replaceChildren(head, facts);
    if (Boolean(machine.desired_paused) !== Boolean(machine.remote_paused)) {
      hero.appendChild(element("p", "manager-inline-message", "暂停状态正在等待执行机回读同步。"));
    }
    if (machine.last_error) {
      hero.appendChild(element("p", "manager-inline-message", machine.last_error));
    }
    hero.appendChild(machineActionButtons(machine));

    const tasks = recordsForMachine(state.tasks, machine.machine_id);
    const results = recordsForMachine(state.results, machine.machine_id);
    const transfers = recordsForMachine(state.transfers, machine.machine_id);
    const incremental = recordsForMachine(state.incrementalRecords, machine.machine_id);
    const accounts = recordsForMachine(state.accounts, machine.machine_id);
    const alerts = alertsForMachine(machine.machine_id);
    const todayResults = results.filter((result) =>
      sameLocalDay(result.recorded_at || result.collected_at || result.updated_at),
    );
    setText("#manager-detail-account-total", accounts.length);
    setText(
      "#manager-detail-today",
      todayResults.reduce((total, result) => total + resultOperationCount(result), 0),
    );
    setText(
      "#manager-detail-pending",
      tasks.filter((task) => String(task.dispatch_state || "").toUpperCase() === "PENDING_DISPATCH")
        .length,
    );
    setText(
      "#manager-detail-running",
      tasks.filter(taskIsExecuting).length,
    );
    setText(
      "#manager-detail-paused",
      tasks.filter((task) => String(task.dispatch_state || "").toUpperCase() === "PAUSED").length,
    );
    setText(
      "#manager-detail-success",
      results.filter((result) => String(result.state || result.status).toUpperCase() === "SUCCESS")
        .length,
    );
    setText("#manager-detail-reconcile", tasks.filter(taskNeedsReconciliation).length);
    setText("#manager-detail-incremental", incremental.length);

    const accountCards = accounts.map((account) => compactAccountCard(account));
    accountList.replaceChildren(
      ...(accountCards.length
        ? accountCards
        : [emptyCard("暂无账号缓存", "运行连接检查或在账号管理页同步这台员工机。")]),
    );

    const taskCards = tasks
      .sort((left, right) => Number(taskIsActive(right)) - Number(taskIsActive(left))
        || activityTime(right) - activityTime(left))
      .slice(0, 6)
      .map(compactTaskCard);
    taskList.replaceChildren(
      ...(taskCards.length
        ? taskCards
        : [emptyCard("暂无 Agent 任务", "Agent 向这台执行机下发后会显示在这里。")]),
    );

    const taskIds = new Set(tasks.map((task) => task.task_id));
    const agentName = `${machine.machine_id}-AGENT`;
    const messages = state.agentMessages.filter((message) =>
      message.payload?.machine_id === machine.machine_id
      || taskIds.has(message.task_id)
      || message.from_agent === agentName
      || message.to_agent === agentName,
    ).sort((left, right) => activityTime(right) - activityTime(left));
    agentList?.replaceChildren(
      ...(messages.length
        ? messages.slice(0, 12).reverse().map(operationTimelineItem)
        : [emptyCard("暂无协作记录", "这台员工机的 ACK、执行进度和结果回执会显示在这里。")]),
    );
    const latestAck = messages.find((message) => message.message_type === "TASK_ACK");
    hero.appendChild(factList([
      ["最近 ACK", formatTime(latestAck?.created_at)],
      ["最近回收", formatTime(machine.last_result_success_at)],
    ]));

    const todayCards = [...todayResults]
      .sort((left, right) => activityTime(right) - activityTime(left))
      .slice(0, 8)
      .map(compactTodayResultCard);
    todayList.replaceChildren(
      ...(todayCards.length
        ? todayCards
        : [emptyCard("今日暂无结果", "平台结果回读后会按动作显示在这里。")]),
    );

    const activity = [
      ...results.map((record) => ({ record, type: "result" })),
      ...transfers.map((record) => ({ record, type: "transfer" })),
    ]
      .sort((left, right) => activityTime(right.record) - activityTime(left.record))
      .slice(0, 6)
      .map(({ record, type }) => compactActivityCard(record, type));
    activityList.replaceChildren(
      ...(activity.length
        ? activity
        : [emptyCard("暂无结果或传输", "回收结果或传输功能包后会显示在这里。")]),
    );
    const alertCards = alerts.map(compactAlertCard);
    if (machine.status !== "READY" && machine.status !== "PAUSED") {
      alertCards.unshift(emptyCard(
        `机器${labelFor(machine.status)}`,
        machine.stale_reason || machine.last_error || "请检查这台员工机的连接和执行服务。",
      ));
    }
    alertList.replaceChildren(
      ...(alertCards.length
        ? alertCards
        : [emptyCard("当前状态正常", "当前没有需要处理的机器或账号告警。")]),
    );
  }

  function renderMachineStage() {
    const overview = $("#manager-cluster-overview-panel");
    const detail = $("#manager-machine-detail");
    if (!overview || !detail) return;
    const machine = state.machines.find((item) => item.machine_id === selectedMachineId);
    overview.hidden = Boolean(machine);
    detail.hidden = !machine;
    if (machine) renderMachineDetail(machine);
  }

  function selectMachine(machineId) {
    const nextId = String(machineId || "");
    selectedMachineId = state.machines.some((machine) => machine.machine_id === nextId)
      ? nextId
      : "";
    localStorage.setItem("auto-xhs:manager-machine", selectedMachineId);
    renderMachineNavigation();
    renderMachineStage();
    if (selectedMachineId) selectManagerView("workers");
  }

  function normalizeLocalAccount(account, status) {
    const rawStatus = String(status?.status || "UNKNOWN").toUpperCase();
    let healthState = "WARNING";
    if (rawStatus === "READY") healthState = "READY";
    else if (["LOGIN_REQUIRED", "IDENTITY_MISMATCH", "PLATFORM_MISMATCH", "BLOCKED"]
      .includes(rawStatus) || status?.server_running === false) healthState = "BLOCKED";
    else if (["UNKNOWN", "ERROR"].includes(rawStatus)) healthState = "UNKNOWN";
    const identity = status?.identity || {};
    return {
      account_key: `MANAGER:${account.name}`,
      machine_id: "MANAGER",
      account_slot: account.name,
      name: account.name,
      platform: account.platform || "xiaohongshu",
      health_state: healthState,
      bridge_status: status?.server_running ? "READY" : "BLOCKED",
      extension_status: status?.extension_connected ? "READY" : "WARNING",
      profile_status: status?.profile_verified ? "READY" : "BLOCKED",
      identity,
      uid: identity.live_user_id || identity.user_id || "",
      nickname: identity.live_nickname || identity.nickname || "",
      agent_task_status: "IDLE",
      last_check_at: identity.observed_at || "",
      abnormal_reason: healthState === "READY" ? "" : status?.next_action || "账号状态待确认",
      recommended_action: status?.next_action || "在经理机账号卡片中处理",
      source_label: "经理机本机环境",
      updated_at: identity.observed_at || "",
    };
  }

  function accountsForSelectedNode() {
    const local = state.localAccounts.map((account, index) =>
      normalizeLocalAccount(account, state.localStatuses[index]),
    );
    if (selectedAccountNode === "manager") return local;
    if (selectedAccountNode === "all") return [...local, ...state.accounts];
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
        persona.status !== "DISABLED"
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
      !["all", "manager"].includes(selectedAccountNode)
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
    setText("#manager-account-node-manager-count", state.localAccounts.length);
    setText("#manager-account-node-all-count", state.localAccounts.length + state.accounts.length);
  }

  function renderAccountWorkspace() {
    const remoteSection = $("#manager-remote-account-section");
    const localSection = $("#manager-local-account-section");
    const grid = $("#manager-remote-account-grid");
    const localGrid = $("#manager-local-account-grid");
    if (!remoteSection || !localSection || !grid) return;
    renderAccountNavigation();
    const accounts = visibleAccounts();
    const showLocal = ["all", "manager"].includes(selectedAccountNode);
    const remoteAccounts = accounts.filter((account) => account.machine_id !== "MANAGER");
    const localAccounts = accounts.filter((account) => account.machine_id === "MANAGER");
    localSection.hidden = !showLocal;
    remoteSection.hidden = selectedAccountNode === "manager";
    const title = selectedAccountNode === "all"
      ? "全部节点账号"
      : selectedAccountNode === "manager"
        ? "经理机账号"
        : `${selectedAccountNode} 账号`;
    setText("#manager-account-view-title", title);
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
    setText(
      "#manager-account-running",
      accounts.filter(accountHasActiveTask).length,
    );
    const localAttention = state.localStatuses.filter(
      (status) => String(status?.status || "UNKNOWN").toUpperCase() !== "READY",
    ).length;
    setText(
      "#blocked-total",
      localAttention + state.alerts.filter((alert) => alert.active !== false).length,
    );
    const cards = remoteAccounts.map((account) => compactAccountCard(account));
    grid.replaceChildren(
      ...(cards.length
        ? cards
        : [emptyCard("暂无员工机账号", "选择员工机并同步账号状态后会显示在这里。")]),
    );
    if (localGrid) {
      localGrid.replaceChildren(
        ...(localAccounts.length
          ? localAccounts.map((account) => compactAccountCard(account, false))
          : [emptyCard("暂无本机环境账号", "经理机本地账号会在此独立展示。")]),
      );
    }
  }

  function renderPersonas() {
    const list = $("#manager-persona-list");
    if (!list) return;
    populateStrategySelects();
    const cards = state.personas.map((persona) => {
      const card = element("article", "manager-task-card manager-strategy-card");
      const head = element("div", "manager-card-head");
      const copy = element("div", "manager-record-copy");
      append(
        copy,
        element("strong", "", persona.display_name || persona.account_slot),
        element(
          "small",
          "",
          `${persona.machine_id || "LOCAL"} · ${persona.account_slot}`,
        ),
      );
      append(head, copy, stateBadge(persona.status));
      append(
        card,
        head,
        factList([
          ["角色定位", persona.role],
          ["目标受众", persona.audience],
          ["内容主题", (persona.content_topics || []).join("、")],
          ["表达语气", persona.tone],
          ["互动方式", persona.interaction_style],
          ["更新时间", formatTime(persona.updated_at)],
        ]),
      );
      const actions = element("div", "manager-card-actions");
      actions.appendChild(actionButton("编辑人设卡", "persona-edit", persona.persona_id));
      card.appendChild(actions);
      return card;
    });
    list.replaceChildren(
      ...(cards.length
        ? cards
        : [emptyCard("暂无账号人设卡", "选择节点和账号后保存人设卡。")]),
    );
  }

  function metricSummary(metrics, emptyLabel = "以期望成果为准") {
    const labels = { browse: "浏览", like: "点赞", comment: "评论", reply: "回复", publish: "发布" };
    return Object.entries(metrics || {})
      .filter(([, value]) => Number(value) > 0)
      .map(([key, value]) => `${labels[key] || key} ${value}`)
      .join(" · ") || emptyLabel;
  }

  function goalActionButtons(goal) {
    const actions = element("div", "manager-card-actions");
    if (["DRAFT", "PAUSED"].includes(goal.state)) {
      actions.appendChild(actionButton("编辑目标", "goal-edit", goal.goal_id));
      actions.appendChild(actionButton("启用目标", "goal-activate", goal.goal_id, "primary"));
    }
    if (goal.state === "ACTIVE") {
      actions.appendChild(actionButton("暂停目标", "goal-pause", goal.goal_id, "warning"));
    }
    if (["ACTIVE", "PAUSED"].includes(goal.state)) {
      actions.appendChild(actionButton("标记完成", "goal-complete", goal.goal_id));
    }
    return actions;
  }

  function renderGoals() {
    const list = $("#manager-goal-list");
    if (!list) return;
    setText("#manager-goal-total", state.goals.length);
    setText("#manager-goal-draft", state.goals.filter((goal) => goal.state === "DRAFT").length);
    setText("#manager-goal-active", state.goals.filter((goal) => goal.state === "ACTIVE").length);
    setText("#manager-goal-paused", state.goals.filter((goal) => goal.state === "PAUSED").length);
    const cards = state.goals.map((goal) => {
      const target = (goal.targets || [])[0] || {};
      const progress = goal.progress || {};
      const card = element("article", "manager-task-card manager-strategy-card");
      const head = element("div", "manager-card-head");
      const copy = element("div", "manager-record-copy");
      append(
        copy,
        element("strong", "", goal.title || goal.goal_id),
        element("small", "", `${goal.goal_id} · v${goal.version || 1}`),
      );
      append(head, copy, stateBadge(goal.state));
      append(
        card,
        head,
        factList([
          ["账号", `${target.machine_id || "LOCAL"} · ${target.account_slot || "—"}`],
          ["周期", `${goal.period_start || "—"} 至 ${goal.period_end || "—"}`],
          ["目标指标", metricSummary(goal.target_metrics)],
          ["实际指标", metricSummary(progress.actual_metrics, "等待结果回收")],
          ["目标 / 实际", `${progress.target_total ?? "—"} / ${progress.actual_total ?? "—"}`],
          ["回收结果", String(progress.result_count || 0)],
          ["内容方向", (goal.content_topics || []).join("、")],
        ]),
      );
      if (goal.desired_outcome) {
        card.appendChild(element("p", "manager-inline-message", goal.desired_outcome));
      }
      const actions = goalActionButtons(goal);
      if (actions.childElementCount) card.appendChild(actions);
      return card;
    });
    list.replaceChildren(
      ...(cards.length
        ? cards
        : [emptyCard("暂无运营目标", "保存目标后由 Agent 读取并制定计划。")]),
    );
  }

  function populateOperationFilters() {
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
      state.operationTasks.map((task) => String(task.state || "WAITING_AGENT").toUpperCase()),
      labelFor,
    );
    populate(
      machineSelect,
      "全部员工机",
      state.operationTasks.flatMap((task) =>
        (task.targets || []).map((target) => String(target.machine_id || ""))),
    );
    populate(
      accountSelect,
      "全部账号",
      state.operationTasks.flatMap((task) =>
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
      && (!machineId || targets.some((target) => target.machine_id === machineId))
      && (!accountSlot || targets.some((target) => target.account_slot === accountSlot))
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
    populateOperationGoalSelect();
    populateOperationFilters();
    const runningStates = new Set(["PLANNING", "DISPATCHING", "RUNNING"]);
    setText("#manager-operation-total", state.operationTasks.length);
    setText(
      "#manager-operation-waiting",
      state.operationTasks.filter((task) => task.state === "WAITING_AGENT").length,
    );
    setText(
      "#manager-operation-running",
      state.operationTasks.filter((task) => runningStates.has(task.state)).length,
    );
    setText(
      "#manager-operation-reconcile",
      state.operationTasks.filter((task) => task.state === "RECONCILIATION_REQUIRED").length,
    );
    const visibleTasks = state.operationTasks
      .filter(operationTaskMatchesFilters)
      .sort((left, right) => operationTaskPriority(left) - operationTaskPriority(right)
        || String(right.updated_at || "").localeCompare(String(left.updated_at || "")));
    const cards = visibleTasks.map((task) => {
      const progress = task.progress || {};
      const targets = (task.targets || [])
        .map((target) => `${target.machine_id || "LOCAL"}/${target.account_slot || "—"}`)
        .join("、");
      const personaSnapshots = (task.persona_bindings || [])
        .map((persona) => persona.display_name || persona.persona_id)
        .join("、");
      const card = element("article", "manager-task-card manager-strategy-card");
      const head = element("div", "manager-card-head");
      const copy = element("div", "manager-record-copy");
      append(
        copy,
        element("strong", "", task.title || task.operation_task_id),
        element("small", "", task.operation_task_id),
      );
      append(head, copy, stateBadge(task.state));
      append(
        card,
        head,
        factList([
          ["运营目标", task.goal_snapshot?.title || task.goal_id],
          ["目标账号", targets],
          ["经理 Agent", task.manager_agent_id || "等待领取"],
          ["人设快照", personaSnapshots],
          [
            "计划动作 / 执行任务",
            `${progress.plan_action_count || 0} / ${progress.execution_task_count || 0}`,
          ],
          ["目标 / 实际", `${progress.target_total ?? "—"} / ${progress.actual_total ?? "—"}`],
          ["回收结果", String(progress.result_count || 0)],
          ["更新时间", formatTime(task.updated_at)],
        ]),
      );
      if (task.instructions) {
        card.appendChild(element("p", "manager-inline-message", task.instructions));
      }
      if (task.plan?.summary) {
        card.appendChild(
          element("p", "manager-inline-message", `经理 Agent 计划：${task.plan.summary}`),
        );
      }
      if (task.result_summary) {
        card.appendChild(
          element("p", "manager-inline-message", `任务结论：${task.result_summary}`),
        );
      }
      const exchanges = state.agentMessages
        .filter((message) => message.operation_task_id === task.operation_task_id)
        .slice(0, 4);
      if (exchanges.length) {
        const exchangeList = element("div", "manager-agent-exchanges");
        exchangeList.appendChild(element("strong", "", "Agent 交接记录"));
        exchanges.forEach((message) => {
          exchangeList.appendChild(
            element(
              "p",
              "manager-inline-message",
              `${message.from_agent} → ${message.to_agent} · `
                + `${labelFor(operationMessageStatus(message))} · ${message.summary}`,
            ),
          );
        });
        card.appendChild(exchangeList);
      }
      const actions = element("div", "manager-card-actions");
      const detailButton = element("button", "secondary-button", "查看任务详情");
      detailButton.type = "button";
      detailButton.dataset.managerOperationDetail = task.operation_task_id;
      actions.appendChild(detailButton);
      card.appendChild(actions);
      return card;
    });
    list.replaceChildren(
      ...(cards.length
        ? cards
        : [emptyCard(
            state.operationTasks.length ? "没有符合条件的运营任务" : "暂无运营任务",
            state.operationTasks.length
              ? "调整状态、员工机、账号、日期或关键词筛选。"
              : "先启用运营目标，再提交给经理 Agent。",
          )]),
    );
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
    const targets = selectedAccountNode === "manager"
      ? []
      : selectedAccountNode === "all"
        ? state.machines
        : state.machines.filter((machine) => machine.machine_id === selectedAccountNode);
    if (!targets.length) {
      setMessage("#manager-account-message", "经理机账号由本地账号服务持续更新。", "success");
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
    setText("#manager-machine-total", state.machines.length);
    setText(
      "#manager-ready-total",
      state.machines.filter((machine) => machine.status === "READY").length,
    );
    setText("#node-total", state.machines.length);
    populateMachineSelects();
    renderMachineNavigation();
    const machineAttention = state.machines.filter((machine) => machine.status !== "READY").length;
    const accountAttention = state.alerts.filter((alert) => alert.active !== false).length;
    const attention = machineAttention + accountAttention;
    setText(
      "#manager-attention-badge",
      attention
        ? `${machineAttention} 台机器 · ${accountAttention} 个账号需要处理`
        : "全部可调度",
    );
    const attentionBadge = $("#manager-attention-badge");
    if (attentionBadge) attentionBadge.dataset.tone = attention ? "warning" : "success";
    if (!state.machines.length) {
      list.replaceChildren(
        emptyCard("尚未登记执行机", "前往系统设置填写 SSH 地址、项目目录和允许版本。"),
      );
      renderMachineStage();
      return;
    }
    const cards = state.machines.map((machine) => {
      const card = element("article", "manager-machine-card");
      const head = element("div", "manager-card-head");
      const title = element("div", "manager-card-title");
      const icon = element("span", "manager-machine-icon", "EX");
      const copy = element("div");
      append(
        copy,
        element("strong", "", machine.host_alias || machine.machine_id),
        element("small", "", machine.machine_id),
      );
      append(title, icon, copy);
      append(head, title, stateBadge(machine.status));
      const facts = factList(
        [
          ["SSH", `${machine.ssh_user || "—"}@${machine.ssh_host || "—"}:${machine.ssh_port || 22}`],
          ["连接", labelFor(machine.ssh_state)],
          ["项目版本", machine.installed_version || "尚未读取"],
          ["最近检查", formatTime(machine.last_check_at)],
        ],
        "manager-machine-facts",
      );
      append(card, head, facts);
      if (machine.stale_reason) {
        card.appendChild(element("p", "manager-inline-message", machine.stale_reason));
      }
      if (Boolean(machine.desired_paused) !== Boolean(machine.remote_paused)) {
        card.appendChild(
          element("p", "manager-inline-message", "暂停状态正在等待执行机回读同步。"),
        );
      }
      if (machine.last_error) {
        card.appendChild(element("p", "manager-inline-message", machine.last_error));
      }
      const actions = machineActionButtons(machine);
      const detailButton = element("button", "manager-action-button", "查看详情");
      detailButton.type = "button";
      detailButton.dataset.managerMachineSelect = machine.machine_id;
      actions.prepend(detailButton);
      card.appendChild(actions);
      return card;
    });
    list.replaceChildren(...cards);
    renderMachineStage();
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
    if (dispatchState === "PENDING_DISPATCH") {
      actions.appendChild(actionButton("暂停任务", "task-pause", task.task_id, "warning"));
    } else if (["ACCEPTED", "RUNNING", "PAUSE_REQUESTED"].includes(dispatchState)) {
      actions.appendChild(actionButton("读取状态", "task-reconcile", task.task_id));
      actions.appendChild(actionButton("暂停任务", "task-pause", task.task_id, "warning"));
    } else if (dispatchState === "PAUSED") {
      actions.appendChild(actionButton("恢复任务", "task-resume", task.task_id, "primary"));
    }
    return actions;
  }

  function renderTasks() {
    const list = $("#manager-task-list");
    if (!list) return;
    const executingTasks = state.tasks.filter(taskIsExecuting);
    const active = executingTasks.length;
    const reconcile = state.tasks.filter(taskNeedsReconciliation).length;
    setText("#manager-active-task-total", active);
    setText("#manager-reconcile-total", reconcile);
    if (!executingTasks.length) {
      list.replaceChildren(
        emptyCard("暂无执行中任务", "员工机接收任务并执行后，状态会显示在这里。"),
      );
      return;
    }
    const cards = executingTasks.map((task) => {
      const request = task.request || {};
      const card = element("article", "manager-task-card");
      const head = element("div", "manager-card-head");
      const copy = element("div", "manager-record-copy");
      append(
        copy,
        element("strong", "", request.request_summary || task.task_id),
        element("small", "", `${task.task_id} · ${task.machine_id || "—"}`),
      );
      append(head, copy, stateBadge(taskPrimaryState(task)));
      const facts = factList([
        ["来源", task.source_type || request.source_type || "Agent"],
        ["账号", request.account_slot],
        ["能力", request.capability],
        ["运营任务", request.operation_task_id],
        ["运营目标", request.goal_snapshot?.title || request.goal_id],
        [
          "人设快照",
          request.persona_snapshot?.persona_id
            ? request.persona_snapshot.display_name || request.persona_snapshot.persona_id
            : "",
        ],
        ["目标", [request.target_type, request.target_id].filter(Boolean).join(" · ")],
        ["参数", taskParameterSummary(request.parameters)],
        ["派发", labelFor(task.dispatch_state)],
        ["连接", labelFor(task.connectivity)],
        ["功能版本", [task.function_id, task.actual_version].filter(Boolean).join(" ")],
        ["更新时间", formatTime(task.updated_at)],
      ]);
      append(card, head, facts);
      const detail = task.result_summary || task.message || task.recommended_action;
      if (detail) card.appendChild(element("p", "manager-inline-message", detail));
      const actions = taskActions(task);
      if (actions.childElementCount) card.appendChild(actions);
      return card;
    });
    list.replaceChildren(...cards);
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
      const actions = element("div", "manager-card-actions");
      if (schedule.state === "PAUSED") {
        actions.appendChild(
          actionButton("恢复计划", "schedule-resume", schedule.schedule_id, "primary"),
        );
      } else {
        actions.appendChild(
          actionButton("暂停计划", "schedule-pause", schedule.schedule_id, "warning"),
        );
      }
      card.appendChild(actions);
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
    const resultList = $("#manager-result-list");
    const incrementalList = $("#manager-incremental-list");
    const auditList = $("#manager-result-audit-list");
    if (!resultList || !incrementalList || !auditList) return;
    populateResultAccounts();
    const results = filteredResults();
    const resultHistory = filteredResultHistory();
    const incremental = filteredIncrementalRecords();
    const manualActions = results.filter((item) => item.manual_action_required).length;
    const gaps = results.reduce((count, item) => {
      const itemStatus =
        item.item_results_status || ("item_results" in item ? "observed" : "missing");
      const incrementalStatus =
        item.incremental_data_status || ("incremental_data" in item ? "observed" : "missing");
      return count
        + Number(itemStatus !== "observed")
        + Number(resultDataStatus(item, "business_result") !== "observed")
        + Number(incrementalStatus !== "observed")
        + Number(requestedResultCount(item) === null)
        + Number(actualResultCount(item) === null);
    }, 0);
    setText("#manager-result-total", results.length);
    setText("#manager-incremental-total", incremental.length);
    setText("#manager-manual-total", manualActions);
    setText("#manager-data-gap-total", gaps);
    const targets = results.map(requestedResultCount).filter((value) => value !== null);
    const actuals = results.map(actualResultCount).filter((value) => value !== null);
    setText(
      "#manager-target-total",
      targets.length ? targets.reduce((total, value) => total + value, 0) : "—",
    );
    setText(
      "#manager-actual-total",
      actuals.length ? actuals.reduce((total, value) => total + value, 0) : "—",
    );
    const terminal = results.filter((item) =>
      terminalTaskStates.has(String(item.state || item.status || "").toUpperCase())
    );
    const successful = terminal.filter(
      (item) => String(item.state || item.status || "").toUpperCase() === "SUCCESS",
    ).length;
    setText(
      "#manager-success-rate",
      terminal.length ? `${Math.round((successful / terminal.length) * 100)}%` : "—",
    );
    setText(
      "#manager-exception-rate",
      terminal.length ? `${Math.round(((terminal.length - successful) / terminal.length) * 100)}%` : "—",
    );
    ["browse", "like", "comment", "reply", "publish"].forEach((kind) => {
      setText(
        `#manager-action-${kind}`,
        actionTargetActualText(results, kind),
      );
    });
    renderExceptions();

    const resultCards = results.map((result) => {
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
          ["动作", resultCapability(result) || result.function_id],
          ["运营任务", result.operation_task_id],
          ["运营目标", result.goal_id],
          ["人设快照", result.persona_id || ""],
          ["目标 / 实际", targetActualText([result])],
          ["功能", result.function_id],
          ["版本", result.actual_version || result.project_version],
          ["执行耗时", resultDuration(result)],
          ["产生时间", formatTime(result.recorded_at || result.created_at)],
          ["回收时间", formatTime(result.collected_at)],
          ["数据完整性", resultCompleteness(result)],
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
      return card;
    });
    resultList.replaceChildren(
      ...(resultCards.length
        ? resultCards
        : [emptyCard("暂无远程结果", "经理机正在定时收集员工机回执，导入后会自动显示。")]),
    );

    const incrementalCards = incremental.map((record) => {
      const card = element("article", "manager-incremental-card");
      const title =
        record.record_type || record.event_type || record.metric_type || record.record_id || "增量记录";
      append(
        card,
        element("strong", "", title),
        element(
          "small",
          "manager-inline-message",
          `${record.machine_id || "—"} · ${formatTime(record.created_at || record.collected_at)}`,
        ),
      );
      if (record.operation_task_id || record.goal_id || record.persona_id) {
        card.appendChild(
          element(
            "small",
            "manager-inline-message",
            [
              record.operation_task_id ? `运营任务 ${record.operation_task_id}` : "",
              record.goal_id ? `目标 ${record.goal_id}` : "",
              record.persona_id
                ? `人设 ${record.persona_id}`
                : "",
            ].filter(Boolean).join(" · "),
          ),
        );
      }
      return card;
    });
    incrementalList.replaceChildren(
      ...(incrementalCards.length
        ? incrementalCards
        : [emptyCard("暂无增量数据", "采集到事件、指标或诊断后会显示在这里。")]),
    );
    const auditCards = resultHistory.map(resultAuditCard);
    auditList.replaceChildren(
      ...(auditCards.length
        ? auditCards
        : [emptyCard("暂无回收审计记录", "收到员工机回执后会在这里保留修订与技术编号。")]),
    );
  }

  function renderOperationReports() {
    const list = $("#manager-operation-report-list");
    if (!list) return;
    if (!state.operationReports.length) {
      list.replaceChildren(
        emptyCard("暂无持续运营报告", "Agent 基于回收结果生成报告后会显示在这里。"),
      );
      return;
    }
    const cards = state.operationReports.map((report) => {
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
    });
    list.replaceChildren(...cards);
  }

  function renderVersions() {
    const list = $("#manager-version-list");
    if (!list) return;
    if (!state.versions.length) {
      list.replaceChildren(emptyCard("尚未读取执行机版本", "选择执行机后点击“读取执行机版本”。"));
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
    const taskList = $("#manager-task-list");
    const machineNav = $("#manager-machine-nav");
    if (machineList) machineList.replaceChildren(errorCard(message));
    if (taskList) taskList.replaceChildren(errorCard(message));
    if (machineNav) machineNav.replaceChildren();
    setText("#manager-last-read", "读取失败");
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
    renderMachines();
    renderPublishReviews();
    renderAccountWorkspace();
    renderPersonas();
    renderGoals();
    renderOperationTasks();
    renderTasks();
    renderSchedules();
    renderTransfers();
    renderResults();
    renderOperationReports();
    renderVersions();
    renderSyncStatus();
    renderSystemSettings();
  }

  function renderSystemSettings() {
    const system = state.system || {};
    const limits = system.l1_limits || {};
    const runtimeForm = $("#manager-runtime-settings-form");
    if (runtimeForm && !runtimeForm.contains(document.activeElement)) {
      $("#manager-setting-concurrency").value = String(system.global_concurrency || 3);
      $("#manager-setting-hourly").value = limits.hourly || 20;
      $("#manager-setting-daily").value = limits.daily || 100;
      $("#manager-setting-dedup").value = limits.dedup_minutes || 10;
      $("#manager-setting-failures").value = limits.failure_threshold || 3;
    }
    setText("#manager-runtime-version", `版本 ${system.product_version || "—"}`);
    setText("#manager-system-pause-state", system.global_paused ? "全局暂停" : "正常运行");
    setText("#manager-system-storage", system.storage_root || "—");
    setText("#manager-system-task-total", system.summary?.tasks_total ?? "—");
    setText("#manager-system-failure-total", system.summary?.recent_failures ?? "—");

    const model = state.replyModel || {};
    const modelForm = $("#manager-reply-model-form");
    if (modelForm && !modelForm.contains(document.activeElement)) {
      $("#manager-reply-model-base-url").value = model.base_url || "https://api.deepseek.com";
      $("#manager-reply-model-name").value = model.model || "";
      $("#manager-reply-model-timeout").value = model.timeout_seconds || 60;
      $("#manager-reply-model-api-key").value = "";
    }
    setText("#manager-reply-model-state", model.configured ? "已配置" : "尚未配置");
    if (!$("#manager-reply-model-message")?.textContent) {
      setMessage(
        "#manager-reply-model-message",
        model.api_key_saved ? "Key 已保存在本机，页面不回显。" : "首次保存需要填写 API Key。",
      );
    }
  }

  async function submitRuntimeSettings(event) {
    event.preventDefault();
    if (!window.confirm("确认更新经理机并发、配额和失败阈值？")) return;
    const button = event.submitter || event.currentTarget.querySelector("button[type='submit']");
    button.disabled = true;
    setMessage("#manager-runtime-message", "正在保存运行参数…");
    try {
      state.system = await mutateJson(`${api}/system/settings`, "POST", {
        confirmed: true,
        global_concurrency: Number($("#manager-setting-concurrency").value),
        l1_limits: {
          hourly: Number($("#manager-setting-hourly").value),
          daily: Number($("#manager-setting-daily").value),
          dedup_minutes: Number($("#manager-setting-dedup").value),
          failure_threshold: Number($("#manager-setting-failures").value),
        },
      });
      renderSystemSettings();
      setMessage("#manager-runtime-message", "运行参数已保存。", "success");
    } catch (error) {
      setMessage("#manager-runtime-message", error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function submitReplyModelSettings(event) {
    event.preventDefault();
    const button = event.submitter || event.currentTarget.querySelector("button[type='submit']");
    button.disabled = true;
    setMessage("#manager-reply-model-message", "正在保存模型配置…");
    try {
      state.replyModel = await mutateJson(`${api}/reply-intelligence/settings`, "POST", {
        confirmed: true,
        api_key: $("#manager-reply-model-api-key").value,
        base_url: $("#manager-reply-model-base-url").value,
        model: $("#manager-reply-model-name").value,
        timeout_seconds: Number($("#manager-reply-model-timeout").value),
      });
      renderSystemSettings();
      setMessage("#manager-reply-model-message", "模型配置已保存。", "success");
    } catch (error) {
      setMessage("#manager-reply-model-message", error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function testReplyModelConnection(button) {
    button.disabled = true;
    setMessage("#manager-reply-model-message", "正在测试模型连接…");
    try {
      const payload = await mutateJson(`${api}/reply-intelligence/test`, "POST", {});
      setMessage(
        "#manager-reply-model-message",
        `${payload.message || "模型连接成功"} · ${payload.model || ""}`,
        "success",
      );
    } catch (error) {
      setMessage("#manager-reply-model-message", error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function exportDiagnostics(button) {
    button.disabled = true;
    setMessage("#manager-diagnostic-message", "正在生成诊断报告…");
    try {
      const payload = await mutateJson(`${api}/diagnostics/export`, "POST", {});
      setMessage(
        "#manager-diagnostic-message",
        payload.path || payload.message || "诊断报告已生成。",
        "success",
      );
    } catch (error) {
      setMessage("#manager-diagnostic-message", error.message, "error");
    } finally {
      button.disabled = false;
    }
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
  }

  function selectManagerView(viewId, updateHash = true) {
    const allowed = new Set([
      "overview",
      "operations",
      "workers",
      "accounts",
      "results",
      "exceptions",
      "settings",
    ]);
    const next = allowed.has(viewId) ? viewId : "overview";
    viewScrollPositions.set(selectedView, window.scrollY);
    selectedView = next;
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
    if (updateHash && window.location.hash !== `#${next}`) {
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
      operationTasks: "运营任务收件箱",
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
    if (state.errors.tasks && !state.tasks.length) {
      const list = $("#manager-task-list");
      if (list) list.replaceChildren(errorCard("Agent 任务缓存暂时不可用。"));
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
    if (state.errors.personas && !state.personas.length) {
      const list = $("#manager-persona-list");
      if (list) list.replaceChildren(errorCard("账号人设卡暂时不可用。"));
    }
    if (state.errors.goals && !state.goals.length) {
      const list = $("#manager-goal-list");
      if (list) list.replaceChildren(errorCard("运营目标暂时不可用。"));
    }
    if (state.errors.operationTasks && !state.operationTasks.length) {
      const list = $("#manager-operation-list");
      if (list) list.replaceChildren(errorCard("运营任务收件箱暂时不可用。"));
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
      localAccountsResult,
      localTasksResult,
      systemResult,
      replyModelResult,
    ] = await Promise.allSettled([
      fetchJson(`${managerApi}/dashboard-snapshot`),
      fetchJson(`${api}/accounts`),
      fetchJson(`${api}/tasks`),
      fetchJson(`${api}/system/status`),
      fetchJson(`${api}/reply-intelligence/status`),
    ]);
    if (snapshotResult.status === "rejected") {
      const error = snapshotResult.reason;
      state.errors.snapshot = error;
      renderUnavailable(error);
      return false;
    }
    const snapshot = snapshotResult.value;
    if (localAccountsResult.status === "fulfilled") {
      state.localAccounts = localAccountsResult.value.accounts || [];
      const statusResults = await Promise.allSettled(
        state.localAccounts.map((account) =>
          fetchJson(`${api}/accounts/${encodeURIComponent(account.name)}/status`),
        ),
      );
      state.localStatuses = statusResults.map((result) =>
        result.status === "fulfilled" ? result.value : { status: "UNKNOWN" },
      );
    } else {
      state.errors.localAccounts = localAccountsResult.reason;
    }
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
    if (replyModelResult.status === "fulfilled") {
      state.replyModel = replyModelResult.value;
    } else {
      state.errors.replyModel = replyModelResult.reason;
    }
    state.available = true;
    state.snapshot = snapshot;
    state.machines = snapshot.machines || [];
    state.tasks = snapshot.tasks || [];
    state.schedules = snapshot.schedules || [];
    state.operationReports = snapshot.operation_reports || [];
    state.transfers = snapshot.transfers || [];
    state.results = snapshot.results || [];
    state.resultHistory = snapshot.result_history || state.results;
    state.incrementalRecords = snapshot.incremental_records || [];
    state.resultSummary = snapshot.result_summary || {};
    state.accounts = snapshot.accounts || [];
    state.alerts = snapshot.alerts || [];
    state.sync = snapshot.collection || {};
    state.personas = snapshot.personas || [];
    state.goals = snapshot.goals || [];
    state.operationTasks = snapshot.operation_tasks || [];
    state.agentMessages = snapshot.agent_messages || [];
    renderAll();
    const readTime = new Date().toLocaleTimeString("zh-CN", { hour12: false });
    setText("#manager-last-read", readTime);
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
    const action = button.dataset.managerAction.replace("task-", "");
    const labels = {
      pause: "暂停任务",
      resume: "恢复任务",
      reconcile: "读取远程状态",
    };
    if (!window.confirm(`确认对任务 ${taskId} 执行“${labels[action]}”？`)) return;
    const path = `${managerApi}/tasks/${encodeURIComponent(taskId)}/${action}`;
    await runMutation(
      button,
      path,
      {},
      "#manager-task-message",
      `正在${labels[action]}…`,
    );
  }

  async function handleScheduleAction(button) {
    const scheduleId = button.dataset.managerIdentity;
    const action = button.dataset.managerAction.replace("schedule-", "");
    const labels = { pause: "暂停计划", resume: "恢复计划" };
    if (!window.confirm(`确认对计划 ${scheduleId} 执行“${labels[action]}”？`)) return;
    const path = `${managerApi}/schedules/${encodeURIComponent(scheduleId)}/${action}`;
    await runMutation(
      button,
      path,
      {},
      "#manager-schedule-message",
      `正在${labels[action]}…`,
    );
  }

  function editPersona(personaId) {
    const persona = state.personas.find((item) => item.persona_id === personaId);
    if (!persona) return;
    $("#manager-persona-id").value = persona.persona_id;
    $("#manager-persona-machine").value = persona.machine_id || "LOCAL";
    populateStrategyAccount(
      "#manager-persona-machine",
      "#manager-persona-account",
      persona.account_slot,
    );
    $("#manager-persona-name").value = persona.display_name || "";
    $("#manager-persona-role").value = persona.role || "";
    $("#manager-persona-audience").value = persona.audience || "";
    $("#manager-persona-language").value = persona.language || "中文";
    $("#manager-persona-topics").value = (persona.content_topics || []).join("，");
    $("#manager-persona-tone").value = persona.tone || "";
    $("#manager-persona-interaction").value = persona.interaction_style || "";
    $("#manager-persona-facts").value = persona.fact_boundaries || "";
    $("#manager-persona-knowledge").value = persona.knowledge || "";
    setMessage("#manager-persona-message", `正在编辑 ${persona.display_name}。`);
    $("#manager-persona-form").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function editGoal(goalId) {
    const goal = state.goals.find((item) => item.goal_id === goalId);
    if (!goal) return;
    const target = (goal.targets || [])[0] || {};
    $("#manager-goal-id").value = goal.goal_id;
    $("#manager-goal-name").value = goal.title || "";
    $("#manager-goal-machine").value = target.machine_id || "LOCAL";
    populateStrategyAccount(
      "#manager-goal-machine",
      "#manager-goal-account",
      target.account_slot,
    );
    $("#manager-goal-start").value = goal.period_start || "";
    $("#manager-goal-end").value = goal.period_end || "";
    $("#manager-goal-outcome").value = goal.desired_outcome || "";
    $("#manager-goal-topics").value = (goal.content_topics || []).join("，");
    ["browse", "like", "comment", "reply", "publish"].forEach((name) => {
      $(`#manager-goal-${name}`).value = Number(goal.target_metrics?.[name] || 0);
    });
    setMessage("#manager-goal-message", `正在编辑 ${goal.title} v${goal.version}。`);
    $("#manager-goal-form").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function handleGoalAction(button) {
    const goalId = button.dataset.managerIdentity;
    const action = button.dataset.managerAction.replace("goal-", "");
    const labels = { activate: "启用目标", pause: "暂停目标", complete: "标记完成" };
    if (!window.confirm(`确认对目标 ${goalId} 执行“${labels[action]}”？`)) return;
    await runMutation(
      button,
      `${managerApi}/goals/${encodeURIComponent(goalId)}/${action}`,
      {},
      "#manager-goal-message",
      `正在${labels[action]}…`,
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
    const labels = {
      doctor: "运行账号诊断",
      "profile-start": "启动账号 Profile",
      "bridge-restart": "重启账号 Bridge",
      "identity-check": "检查当前身份",
    };
    if (!window.confirm(`确认对 ${machineId} / ${account} 执行“${labels[action]}”？`)) return;
    const suffixes = {
      doctor: "doctor",
      "profile-start": "profile/start",
      "bridge-restart": "bridge/restart",
      "identity-check": "identity/check",
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

  async function submitPersona(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = event.submitter || form.querySelector("button[type='submit']");
    const body = {
      persona_id: $("#manager-persona-id").value.trim(),
      machine_id: $("#manager-persona-machine").value,
      account_slot: $("#manager-persona-account").value,
      display_name: $("#manager-persona-name").value.trim(),
      role: $("#manager-persona-role").value.trim(),
      audience: $("#manager-persona-audience").value.trim(),
      language: $("#manager-persona-language").value.trim() || "中文",
      content_topics: splitList($("#manager-persona-topics").value),
      tone: $("#manager-persona-tone").value.trim(),
      interaction_style: $("#manager-persona-interaction").value.trim(),
      fact_boundaries: $("#manager-persona-facts").value.trim(),
      knowledge: $("#manager-persona-knowledge").value.trim(),
      status: "ACTIVE",
    };
    button.disabled = true;
    setMessage("#manager-persona-message", "正在保存人设卡…");
    try {
      const payload = await mutateJson(`${managerApi}/personas`, "POST", body);
      form.reset();
      $("#manager-persona-id").value = "";
      await load();
      setMessage(
        "#manager-persona-message",
        `${payload.persona.display_name} 已保存并启用。`,
        "success",
      );
    } catch (error) {
      setMessage("#manager-persona-message", error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function submitGoal(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = event.submitter || form.querySelector("button[type='submit']");
    const targetMetrics = {};
    ["browse", "like", "comment", "reply", "publish"].forEach((name) => {
      const value = Number($(`#manager-goal-${name}`).value || 0);
      if (value > 0) targetMetrics[name] = value;
    });
    const body = {
      goal_id: $("#manager-goal-id").value.trim(),
      title: $("#manager-goal-name").value.trim(),
      targets: [{
        machine_id: $("#manager-goal-machine").value,
        account_slot: $("#manager-goal-account").value,
      }],
      period_start: $("#manager-goal-start").value,
      period_end: $("#manager-goal-end").value,
      desired_outcome: $("#manager-goal-outcome").value.trim(),
      content_topics: splitList($("#manager-goal-topics").value),
      target_metrics: targetMetrics,
      priority: "NORMAL",
    };
    button.disabled = true;
    setMessage("#manager-goal-message", "正在保存运营目标…");
    try {
      const payload = await mutateJson(`${managerApi}/goals`, "POST", body);
      form.reset();
      $("#manager-goal-id").value = "";
      setGoalDateRange();
      await load();
      setMessage(
        "#manager-goal-message",
        `${payload.goal.title} v${payload.goal.version} 已保存，等待启用。`,
        "success",
      );
    } catch (error) {
      setMessage("#manager-goal-message", error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function submitOperationTask(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = event.submitter || form.querySelector("button[type='submit']");
    const body = {
      goal_id: $("#manager-operation-goal").value,
      title: $("#manager-operation-name").value.trim(),
      instructions: $("#manager-operation-instructions").value.trim(),
      completion_criteria: $("#manager-operation-criteria").value.trim(),
      requested_by: "WebUI用户",
    };
    button.disabled = true;
    setMessage("#manager-operation-message", "正在提交经理 Agent 任务收件箱…");
    try {
      const payload = await mutateJson(`${managerApi}/operation-tasks`, "POST", body);
      form.reset();
      await load();
      setMessage(
        "#manager-operation-message",
        `${payload.operation_task.operation_task_id} 已提交，等待经理 Agent 领取。`,
        "success",
      );
    } catch (error) {
      setMessage("#manager-operation-message", error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function submitMachine(event) {
    event.preventDefault();
    const button = event.submitter || event.currentTarget.querySelector("button[type='submit']");
    const versions = splitList($("#manager-machine-versions").value);
    if (!versions.length) {
      setMessage("#manager-machine-message", "请至少填写一个允许项目版本。", "error");
      return;
    }
    const body = {
      manager_id: $("#manager-machine-manager-id").value.trim() || "MILA-MANAGER",
      machine_id: $("#manager-machine-id").value.trim(),
      host_alias: $("#manager-machine-alias").value.trim(),
      ssh_host: $("#manager-machine-host").value.trim(),
      ssh_port: Number($("#manager-machine-port").value || 22),
      ssh_user: $("#manager-machine-user").value.trim(),
      working_directory: $("#manager-machine-workdir").value.trim(),
      python_executable: $("#manager-machine-python").value.trim(),
      identity_file: $("#manager-machine-identity").value.trim(),
      allowed_versions: versions,
      expected_role: "executor",
      connect_timeout_seconds: Number($("#manager-machine-connect-timeout").value || 10),
    };
    try {
      const payload = await runMutation(
        button,
        `${managerApi}/machines`,
        body,
        "#manager-machine-message",
        "正在保存执行机…",
      );
      setMessage(
        "#manager-machine-message",
        `${payload.machine?.machine_id || body.machine_id} 已保存，可前往工作台执行连接检查。`,
        "success",
      );
    } catch (_error) {
      // runMutation has rendered the user-facing error.
    }
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

  async function submitTransfer(event) {
    event.preventDefault();
    const button = event.submitter || event.currentTarget.querySelector("button[type='submit']");
    const machineId = $("#manager-transfer-machine").value;
    const body = {
      transfer_id: $("#manager-transfer-id").value.trim(),
      machine_id: machineId,
      transfer_type: $("#manager-transfer-type").value,
      source_path: $("#manager-transfer-source").value.trim(),
      allowed_versions: splitList($("#manager-transfer-versions").value),
      timeout_seconds: Number($("#manager-transfer-timeout").value || 600),
    };
    const existing = state.transfers.find((item) => item.transfer_id === body.transfer_id);
    if (
      existing &&
      ["PREPARING", "UPLOADING", "RESULT_UNKNOWN"].includes(transferState(existing))
    ) {
      setMessage(
        "#manager-transfer-message",
        transferState(existing) === "RESULT_UNKNOWN"
          ? "该传输结果仍待核对，当前页面不会用新编号重复传输。"
          : "该传输仍在进行，请等待状态回读。",
        "warning",
      );
      return;
    }
    if (!window.confirm(`确认把这个包传输到 ${machineId}？`)) return;
    try {
      const payload = await runMutation(
        button,
        `${managerApi}/transfers`,
        body,
        "#manager-transfer-message",
        "正在准备并传输功能包…",
      );
      if (payload.success === true) $("#manager-transfer-id").value = generatedId("TRANSFER");
      setMessage(
        "#manager-transfer-message",
        `${body.transfer_id}：${labelFor(payload.status || payload.transfer?.state)}`,
        payload.success === false ? "warning" : "success",
      );
    } catch (_error) {
      // runMutation has rendered the user-facing error.
    }
  }

  async function submitVersionPolicy(event) {
    event.preventDefault();
    const button = event.submitter || event.currentTarget.querySelector("button[type='submit']");
    const machineId = $("#manager-version-machine").value;
    const status = $("#manager-version-status").value;
    const machine = state.machines.find((item) => item.machine_id === machineId);
    const accounts = splitList($("#manager-version-accounts").value);
    const actions = splitList($("#manager-version-actions").value);
    if (status !== "DISABLED" && (!accounts.length || !actions.length)) {
      setMessage("#manager-version-message", "正式版和试用版必须填写账号范围与允许动作。", "error");
      return;
    }
    if (!window.confirm(`确认在 ${machineId} 保存这条功能版本策略？`)) return;
    const body = {
      machine_id: machineId,
      function_id: $("#manager-version-function").value.trim(),
      version: $("#manager-version-value").value.trim(),
      status,
      scope: status === "DISABLED"
        ? null
        : {
            manager_ids: [machine?.manager_id || "MILA-MANAGER"],
            machine_ids: [machineId],
            account_slots: accounts,
            allowed_actions: actions,
          },
      set_as_stable: status === "FORMAL" && $("#manager-version-stable").checked,
    };
    try {
      const payload = await runMutation(
        button,
        `${managerApi}/version-policies`,
        body,
        "#manager-version-message",
        "正在保存版本策略…",
      );
      if (payload.version) {
        state.versions = [
          payload.version,
          ...state.versions.filter(
            (version) =>
              version.function_id !== payload.version.function_id ||
              version.version !== payload.version.version,
          ),
        ];
        renderVersions();
      }
    } catch (_error) {
      // runMutation has rendered the user-facing error.
    }
  }

  async function loadVersions(event) {
    const button = event.currentTarget;
    const machineId = $("#manager-version-machine").value;
    if (!machineId) {
      setMessage("#manager-version-message", "请先选择执行机。", "error");
      return;
    }
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    setMessage("#manager-version-message", "正在连接执行机读取版本…");
    try {
      const functionId = $("#manager-version-function").value.trim();
      const suffix = functionId ? `/${encodeURIComponent(functionId)}` : "";
      const payload = await fetchJson(
        `${managerApi}/machines/${encodeURIComponent(machineId)}/versions${suffix}`,
      );
      state.versions = payload.versions || [];
      renderVersions();
      setMessage(
        "#manager-version-message",
        `已读取 ${state.versions.length} 条版本记录。`,
        "success",
      );
    } catch (error) {
      const payload = businessPayload(error);
      if (payload?.versions) {
        state.versions = payload.versions;
        renderVersions();
      }
      setMessage(
        "#manager-version-message",
        payload?.message || error.message,
        payload ? "warning" : "error",
      );
    } finally {
      button.disabled = false;
      button.removeAttribute("aria-busy");
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

  function setGoalDateRange() {
    const start = new Date();
    const end = new Date(start);
    end.setDate(end.getDate() + 29);
    const startInput = $("#manager-goal-start");
    const endInput = $("#manager-goal-end");
    if (startInput) startInput.value = localDateValue(start);
    if (endInput) endInput.value = localDateValue(end);
  }

  function bindEvents() {
    $("#manager-refresh")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      await load();
      button.disabled = false;
      button.removeAttribute("aria-busy");
    });
    $("#manager-machine-form")?.addEventListener("submit", submitMachine);
    $("#manager-runtime-settings-form")?.addEventListener("submit", submitRuntimeSettings);
    $("#manager-reply-model-form")?.addEventListener("submit", submitReplyModelSettings);
    $("#manager-reply-model-test")?.addEventListener("click", (event) => {
      testReplyModelConnection(event.currentTarget);
    });
    $("#manager-export-diagnostics")?.addEventListener("click", (event) => {
      exportDiagnostics(event.currentTarget);
    });
    $("#manager-persona-form")?.addEventListener("submit", submitPersona);
    $("#manager-goal-form")?.addEventListener("submit", submitGoal);
    $("#manager-operation-form")?.addEventListener("submit", submitOperationTask);
    $("#manager-persona-machine")?.addEventListener("change", () => {
      populateStrategyAccount("#manager-persona-machine", "#manager-persona-account");
    });
    $("#manager-goal-machine")?.addEventListener("change", () => {
      populateStrategyAccount("#manager-goal-machine", "#manager-goal-account");
    });
    $("#manager-collect-results")?.addEventListener("click", collectSelectedMachine);
    $("#manager-transfer-form")?.addEventListener("submit", submitTransfer);
    $("#manager-version-form")?.addEventListener("submit", submitVersionPolicy);
    $("#manager-load-versions")?.addEventListener("click", loadVersions);
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
    $("#manager-version-status")?.addEventListener("change", updateVersionStableControl);
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
    ].forEach((selector) => $(selector)?.addEventListener("change", renderOperationTasks));
    $("#manager-operation-filter-keyword")?.addEventListener("input", renderOperationTasks);
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
    window.addEventListener("auto-xhs:local-accounts", (event) => {
      state.localAccounts = event.detail?.accounts || [];
      state.localStatuses = event.detail?.statuses || [];
      renderAccountWorkspace();
      populateStrategySelects();
    });
    document.addEventListener("click", (event) => {
      const publishReview = event.target.closest("[data-manager-publish-review]");
      if (publishReview) {
        openPublishDialog(publishReview.dataset.managerPublishReview);
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
      if (action === "persona-edit") {
        editPersona(button.dataset.managerIdentity);
        return;
      }
      if (action === "goal-edit") {
        editGoal(button.dataset.managerIdentity);
        return;
      }
      const operation = action.startsWith("machine-")
        ? handleMachineAction(button)
        : action.startsWith("schedule-")
          ? handleScheduleAction(button)
          : action.startsWith("goal-")
            ? handleGoalAction(button)
        : handleTaskAction(button);
      operation.catch((error) => {
        const selector = action.startsWith("machine-")
          ? "#manager-cluster-message"
          : action.startsWith("schedule-")
            ? "#manager-schedule-message"
            : action.startsWith("goal-")
              ? "#manager-goal-message"
          : "#manager-task-message";
        setMessage(selector, error.message, "error");
      });
    });
  }

  function initialize() {
    const transferId = $("#manager-transfer-id");
    if (transferId && !transferId.value) transferId.value = generatedId("TRANSFER");
    setResultDateRange();
    setGoalDateRange();
    updateVersionStableControl();
    bindEvents();
    renderVersions();
    selectManagerView(window.location.hash.slice(1) || selectedView, false);
    load();
  }

  function updateVersionStableControl() {
    const status = $("#manager-version-status")?.value || "FORMAL";
    const stable = $("#manager-version-stable");
    if (!stable) return;
    stable.disabled = status !== "FORMAL";
    stable.checked = status === "FORMAL";
  }

  initialize();
  window.AutoXhsManagerConsole = { load, render: renderAll, state };
})();
