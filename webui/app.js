const { api, fetchJson, mutateJson } = window.AutoXhsApi;
let accountStatusByName = new Map();
let activeAccountSwitch = null;
let activeIdentityMismatch = null;
let activeAccountRemoval = null;
let activePublishTask = null;
let accountTaskActivityByName = new Map();
const pendingSubmissionByAccount = new Map();
const taskMessageByAccount = new Map();
const commentCollectionMessageByAccount = new Map();
let taskActivityPollTimer = null;
let taskSnapshotKey = "";
let accountStatusPollTimer = null;
let accountSnapshotKey = "";
let currentAccountData = { accounts: [] };
let currentAccountStatuses = [];
let activeAccountSetup = null;
let accountSetupPollTimer = null;
let accountSetupPrimaryAction = null;
document.documentElement.classList.add("js-ready");

const {
  taskCapabilities,
  publishMonitorCapabilities,
  agentMonitorCapabilities,
  taskTemplates,
  draftModes,
  capabilityTemplate,
} = window.AutoXhsTaskCatalog;

let commentSuggestionIndex = 0;

const $ = (selector) => document.querySelector(selector);
const text = (value) => String(value ?? "");

function accountConfig(accountOrName) {
  if (typeof accountOrName === "object" && accountOrName) return accountOrName;
  return currentAccountData.accounts.find((item) => item.name === accountOrName) || { name: text(accountOrName) };
}

function profileDisplayName(account) {
  return account?.profile_display_name || account?.chrome_profile_directory || account?.name || "未命名 Profile";
}

function bindingRow(label, name, detail, tone = "") {
  const row = document.createElement("div");
  row.className = `account-binding-row ${tone}`.trim();
  const labelNode = document.createElement("span");
  labelNode.textContent = label;
  const copy = document.createElement("div");
  const strong = document.createElement("strong");
  strong.textContent = name;
  const small = document.createElement("small");
  small.textContent = detail;
  copy.append(strong, small);
  row.append(labelNode, copy);
  return row;
}

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
  if (["IDENTITY_REQUIRED", "IDENTITY_CHECK_REQUIRED", "SWITCH_PENDING", "QUEUED", "RUNNING", "WAITING_APPROVAL", "DRAFT", "CONFIRMED"].includes(state)) return "pending";
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
    SWITCH_PENDING: "换号进行中",
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

const taskActivityPriority = {
  RUNNING: 0,
  QUEUED: 1,
  WAITING_APPROVAL: 2,
  BLOCKED: 3,
};

function rebuildAccountTaskActivity(tasks) {
  const activities = new Map();
  tasks.forEach((task) => {
    if (!(task.state in taskActivityPriority)) return;
    const current = activities.get(task.account_slot);
    if (!current || taskActivityPriority[task.state] < taskActivityPriority[current.state]) {
      activities.set(task.account_slot, {
        ...task,
        open_count: (current?.open_count || 0) + 1,
      });
    } else {
      current.open_count += 1;
    }
  });
  accountTaskActivityByName = activities;
}

function currentAccountTaskActivity(account) {
  const pending = pendingSubmissionByAccount.get(account);
  if (pending) return { ...pending, state: "RUNNING", locally_submitting: true };
  return accountTaskActivityByName.get(account) || null;
}

function accountHasUnfinishedTask(account) {
  return Boolean(currentAccountTaskActivity(account));
}

function activityShortLabel(activity) {
  if (!activity) return "空闲";
  return {
    RUNNING: "执行中",
    QUEUED: "等待执行",
    WAITING_APPROVAL: "等待确认",
    BLOCKED: "需处理",
  }[activity.state] || stateLabel(activity.state);
}

function paintAccountActivity(node, account) {
  const activity = currentAccountTaskActivity(account);
  const dot = document.createElement("span");
  dot.className = "account-activity-dot";
  dot.setAttribute("aria-hidden", "true");
  const copy = document.createElement("div");
  const label = document.createElement("small");
  const title = document.createElement("strong");
  const detail = document.createElement("span");

  if (!activity) {
    node.dataset.state = "IDLE";
    label.textContent = "任务状态";
    title.textContent = "空闲";
    detail.textContent = "当前没有排队或执行中的任务";
  } else {
    const capabilityName = taskCapabilityLabel(activity.capability);
    node.dataset.state = activity.state;
    label.textContent = activityShortLabel(activity);
    title.textContent = `${activityShortLabel(activity)} · ${capabilityName}`;
    detail.textContent = activity.request_summary || `任务 ${String(activity.task_id || "").slice(0, 8)}`;
    if ((activity.open_count || 1) > 1) detail.textContent += ` · 共 ${activity.open_count} 个未完成任务`;
  }
  copy.append(label, title, detail);
  node.replaceChildren(dot, copy);
  if (
    activity
    && publishMonitorCapabilities.has(activity.capability)
    && activity.state === "WAITING_APPROVAL"
    && activity.parameters?.stage === "preview_ready"
  ) {
    const review = document.createElement("button");
    review.type = "button";
    review.className = "publish-review-button";
    review.textContent = "查看并确认";
    review.addEventListener("click", () => openPublishConfirmation(activity));
    node.append(review);
  }
}

function syncAccountActivityUI() {
  document.querySelectorAll(".account-card[data-account]").forEach((card) => {
    const activity = card.querySelector(".account-activity");
    if (activity) paintAccountActivity(activity, card.dataset.account);
  });
  refreshTaskAccountOptions();
  updateTaskAvailability();
  updateCommentCollectionAvailability();
}

function accountCard(account, status) {
  const card = document.createElement("article");
  card.className = "account-card";
  card.dataset.account = account.name;

  const head = document.createElement("div");
  head.className = "account-head";
  const identity = document.createElement("div");
  identity.className = "account-identity";
  const avatar = document.createElement("span");
  avatar.className = "avatar";
  const displayName = profileDisplayName(account);
  avatar.textContent = text(displayName).slice(0, 2).toUpperCase();
  const titleWrap = document.createElement("div");
  const title = document.createElement("h3");
  title.textContent = displayName;
  const profile = document.createElement("p");
  profile.className = "profile";
  profile.textContent = `固定 Profile · ${account.chrome_profile_directory || "Default"}`;
  const extensionPath = document.createElement("p");
  extensionPath.className = "profile extension-path";
  extensionPath.textContent = account.name === displayName ? "" : `槽位标识 · ${account.name}`;
  extensionPath.title = account.extension_dir || account.name;
  titleWrap.append(title, profile, extensionPath);
  identity.append(avatar, titleWrap);

  const badge = document.createElement("span");
  const state = status?.status || "ERROR";
  card.dataset.state = state;
  badge.className = `badge ${stateStyle(state)}`;
  badge.textContent = stateLabel(state);
  const headActions = document.createElement("div");
  headActions.className = "account-head-actions";
  const more = document.createElement("details");
  more.className = "account-more";
  const moreLabel = document.createElement("summary");
  moreLabel.textContent = "更多";
  moreLabel.setAttribute("aria-label", `${account.name} 更多操作`);
  const morePanel = document.createElement("div");
  morePanel.className = "account-more-panel";
  more.append(moreLabel, morePanel);
  headActions.append(badge, more);
  head.append(identity, headActions);

  const signals = document.createElement("div");
  signals.className = "signals";
  signals.append(
    signal("BRIDGE", status?.server_running ? "在线" : "离线"),
    signal("扩展", status?.extension_connected ? "已连接" : "未连接"),
    signal("PROFILE", status?.profile_verified ? "已核验" : "待核验")
  );

  const binding = document.createElement("div");
  binding.className = "account-binding";
  const statusIdentity = status?.identity || {};
  binding.append(bindingRow("固定 Profile", displayName, account.chrome_profile_directory || "Default"));
  if (state === "IDENTITY_MISMATCH") {
    binding.append(
      bindingRow("槽位原账号", statusIdentity.nickname || "未命名账号", `UID ${statusIdentity.user_id || "—"}`, "recorded"),
      bindingRow("当前登录账号", statusIdentity.live_nickname || "未命名账号", `UID ${statusIdentity.live_user_id || "—"}`, "mismatch")
    );
  } else {
    const currentName = statusIdentity.live_nickname || statusIdentity.nickname || statusIdentity.live_user_id || statusIdentity.user_id || "尚未登录或核验";
    const currentUid = statusIdentity.live_user_id || statusIdentity.user_id;
    binding.append(bindingRow("当前小红书账号", currentName, currentUid ? `UID ${currentUid}` : "完成身份核验后显示昵称与 UID"));
  }

  const taskActivity = document.createElement("div");
  taskActivity.className = "account-activity";
  paintAccountActivity(taskActivity, account.name);
  card.append(head, signals, binding, taskActivity);
  const next = document.createElement("p");
  next.className = "next-action";
  const identityName = status?.identity?.nickname || status?.identity?.user_id;
  let guidedNextAction = "";
  if (!account.extension_instance_enrolled) guidedNextAction = "点击“继续配置”，然后在目标 Profile 的扩展中确认配对";
  else if (!status?.connection_ready) guidedNextAction = "点击“恢复连接”，系统会复用或按需打开绑定的 Profile";
  else if (["IDENTITY_REQUIRED", "IDENTITY_CHECK_REQUIRED"].includes(state)) guidedNextAction = "点击“完成身份核验”，系统会自动读取当前 UID";
  else if (state === "IDENTITY_MISMATCH") guidedNextAction = "确认是登错账号，还是让当前账号接管这个固定 Profile";
  next.textContent = guidedNextAction ? `下一步：${guidedNextAction}` : `当前身份：${text(identityName || "已核验")}`;
  card.append(next);
  const actions = document.createElement("div");
  actions.className = "account-actions";
  const primaryButton = document.createElement("button");
  primaryButton.type = "button";
  primaryButton.className = "primary-button account-primary-action";
  if (status?.identity?.switch_pending) {
    primaryButton.textContent = "继续切换账号";
    primaryButton.addEventListener("click", () => openAccountSwitch(account, status));
  } else if (state === "IDENTITY_MISMATCH") {
    primaryButton.textContent = "处理账号不一致";
    primaryButton.addEventListener("click", () => openIdentityMismatch(account, status));
  } else if (state === "READY") {
    primaryButton.textContent = "已就绪";
    primaryButton.dataset.ready = "true";
    primaryButton.disabled = true;
  } else {
    primaryButton.textContent = !account.extension_instance_enrolled
      ? "继续配置"
      : status?.connection_ready ? "完成身份核验" : "恢复连接";
    primaryButton.addEventListener("click", () => beginGuidedAccountSetup(account, status));
  }
  actions.append(primaryButton);

  const addMoreGroup = (label) => {
    const heading = document.createElement("span");
    heading.className = "account-more-group";
    heading.textContent = label;
    morePanel.append(heading);
  };
  const addMoreAction = (label, handler, { className = "", disabled = false, title = "" } = {}) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `account-more-action ${className}`.trim();
    button.textContent = label;
    button.disabled = disabled;
    button.title = title;
    button.addEventListener("click", () => {
      more.removeAttribute("open");
      handler(button);
    });
    morePanel.append(button);
    return button;
  };
  addMoreGroup("连接调试");
  addMoreAction("启动 Bridge", (button) => updateBridge(account.name, "start-only", next, button), {
    disabled: Boolean(status?.server_running),
    title: status?.server_running ? "Bridge 当前已经在线" : "只启动本地 Bridge，不打开 Chrome",
  });
  addMoreAction("停止 Bridge", (button) => updateBridge(account.name, "stop", next, button), {
    disabled: !status?.server_running,
    title: status?.server_running ? "停止本槽位 Bridge，不关闭 Chrome" : "Bridge 当前未运行",
  });
  addMoreAction("重启账号连接", (button) => updateBridge(account.name, "restart", next, button));
  addMoreAction("重新发起配对", () => beginPairing(account.name, next));
  addMoreAction("单独核验当前 UID", () => verifyIdentity(account.name, status?.identity?.recorded, next));
  addMoreGroup("账号管理");
  addMoreAction(status?.identity?.switch_pending ? "继续切换账号" : "切换账号", () => openAccountSwitch(account, status));
  const logoutAction = addMoreAction("退出当前账号", (button) => logoutAccount(account.name, next, button));
  logoutAction.title = "结束当前小红书登录会话，并回读页面确认已退出";
  addMoreAction("账号自启动设置", () => updateAutostart(account.name, next));
  const removeButton = document.createElement("button");
  removeButton.type = "button";
  removeButton.className = "account-remove-button";
  removeButton.textContent = "删除槽位";
  removeButton.addEventListener("click", () => {
    more.removeAttribute("open");
    openAccountRemoval(account);
  });
  morePanel.append(removeButton);
  card.append(actions);
  return card;
}

const setupSteps = ["bridge", "pairing", "identity", "ready"];

function paintSetupProgress(activeStep, completed = []) {
  document.querySelectorAll("[data-setup-step]").forEach((node) => {
    const step = node.dataset.setupStep;
    node.classList.toggle("active", step === activeStep);
    node.classList.toggle("done", completed.includes(step));
  });
}

function setAccountSetupGuidance(title, body, pending = true) {
  const guidance = $("#account-setup-guidance");
  const heading = document.createElement("strong");
  heading.textContent = title;
  const copy = document.createElement("p");
  copy.textContent = body;
  guidance.replaceChildren(heading, copy);
  guidance.classList.toggle("pending", pending);
}

function scheduleAccountSetupPoll(callback, delay = 1000) {
  if (accountSetupPollTimer) clearTimeout(accountSetupPollTimer);
  accountSetupPollTimer = setTimeout(callback, delay);
}

function setAccountSetupPrimary(label, action = null, { hidden = false, disabled = false } = {}) {
  const button = $("#account-setup-primary");
  accountSetupPrimaryAction = action;
  button.textContent = label;
  button.hidden = hidden;
  button.disabled = disabled;
  button.removeAttribute("aria-busy");
}

async function runAccountSetupPrimaryAction() {
  if (!accountSetupPrimaryAction) return;
  if (accountSetupPollTimer) clearTimeout(accountSetupPollTimer);
  accountSetupPollTimer = null;
  const action = accountSetupPrimaryAction;
  const button = $("#account-setup-primary");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try {
    await action();
  } finally {
    if (accountSetupPrimaryAction === action && !button.hidden) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }
}

function closeGuidedAccountSetup() {
  if (accountSetupPollTimer) clearTimeout(accountSetupPollTimer);
  accountSetupPollTimer = null;
  activeAccountSetup = null;
  accountSetupPrimaryAction = null;
  const dialog = $("#account-setup-dialog");
  if (dialog.open) dialog.close();
  scheduleAccountStatusPoll();
}

async function copyGuidedPairingBundle(showResult = true) {
  if (!activeAccountSetup?.pairingBundle) return;
  const message = $("#account-setup-message");
  try {
    await navigator.clipboard.writeText(activeAccountSetup.pairingBundle);
    if (showResult) message.textContent = "配对信息已重新复制。";
  } catch (_error) {
    message.textContent = "浏览器未允许自动复制，请重新点击“复制配对信息”。";
  }
}

async function beginGuidedAccountSetup(account, status = {}) {
  if (accountSetupPollTimer) clearTimeout(accountSetupPollTimer);
  if (accountStatusPollTimer) clearTimeout(accountStatusPollTimer);
  accountStatusPollTimer = null;
  activeAccountSetup = {
    account,
    status,
    recorded: Boolean(status?.identity?.recorded),
    recordedUid: status?.identity?.user_id || "",
    pairingBundle: "",
    phase: "STARTING",
  };
  $("#account-setup-name").textContent = account.name;
  $("#account-setup-avatar").textContent = text(account.name).slice(0, 2).toUpperCase();
  $("#account-setup-profile").textContent = `Chrome · ${account.chrome_profile_directory || "Default"}`;
  $("#account-setup-stage").textContent = "正在准备";
  $("#account-setup-message").textContent = "正在启动本地 Bridge…";
  $("#account-setup-copy").hidden = true;
  setAccountSetupPrimary("正在准备…", null, { disabled: true });
  $("#account-setup-identity").hidden = true;
  paintSetupProgress("bridge");
  setAccountSetupGuidance("正在准备账号连接", "系统会复用现有连接；未配对的已有 Profile 不会被重复打开。", true);
  const dialog = $("#account-setup-dialog");
  if (!dialog.open) dialog.showModal();

  try {
    const payload = await mutateJson(`${api}/accounts/${encodeURIComponent(account.name)}/setup/begin`, "POST", { confirmed: true });
    const setup = payload.setup;
    if (!activeAccountSetup || activeAccountSetup.account.name !== account.name) return;
    activeAccountSetup.phase = setup.phase;
    if (setup.phase === "WAITING_PAIRING") {
      activeAccountSetup.pairingBundle = setup.pairing.pairing_bundle;
      $("#account-setup-copy").hidden = false;
      $("#account-setup-stage").textContent = "等待扩展确认";
      $("#account-setup-message").textContent = setup.message || "配对信息已准备。";
      paintSetupProgress("pairing", ["bridge"]);
      setAccountSetupGuidance(
        "请在目标 Profile 的扩展中确认",
        "配对信息已复制。打开 XHS Bridge 扩展，粘贴并确认当前 Profile；本页面会自动继续。",
        true
      );
      setAccountSetupPrimary("我已在扩展确认，立即检测", pollGuidedAccountSetup);
      await copyGuidedPairingBundle(false);
      scheduleAccountSetupPoll(pollGuidedAccountSetup);
      return;
    }
    $("#account-setup-stage").textContent = setup.lifecycle?.ready ? "正在核验身份" : "等待扩展连接";
    $("#account-setup-message").textContent = setup.lifecycle?.message || "正在等待扩展连接…";
    paintSetupProgress("identity", ["bridge", "pairing"]);
    setAccountSetupGuidance("连接已启动", "扩展连接后会自动读取并核对当前小红书 UID。", true);
    setAccountSetupPrimary("立即检测连接", pollGuidedAccountSetup);
    scheduleAccountSetupPoll(pollGuidedAccountSetup, setup.lifecycle?.ready ? 100 : 1000);
  } catch (error) {
    $("#account-setup-stage").textContent = "配置受阻";
    $("#account-setup-message").textContent = error.code === "NOT_FOUND"
      ? "本地服务仍是旧版本，请重启 WebUI 后重试。"
      : error.message;
    setAccountSetupGuidance("暂时无法开始配置", "无需关闭此面板；处理上方问题后可在这里重新尝试。", false);
    setAccountSetupPrimary("重新尝试", () => beginGuidedAccountSetup(account, activeAccountSetup?.status || status));
  }
}

async function pollGuidedAccountSetup() {
  if (!activeAccountSetup) return;
  const current = activeAccountSetup;
  try {
    if (current.phase === "WAITING_PAIRING") {
      const payload = await fetchJson(`${api}/accounts/${encodeURIComponent(current.account.name)}/pairing`);
      if (!payload.pairing.paired) {
        $("#account-setup-message").textContent = payload.pairing.pairing_pending
          ? "等待你在扩展中确认当前 Profile…"
          : "配对信息已过期，请重新生成。";
        if (payload.pairing.pairing_pending) {
          setAccountSetupPrimary("我已在扩展确认，立即检测", pollGuidedAccountSetup);
          scheduleAccountSetupPoll(pollGuidedAccountSetup);
        } else {
          setAccountSetupGuidance("本次配对信息已失效", "点击下方按钮即可重新生成，不需要退出配置流程。", false);
          setAccountSetupPrimary("重新生成配对信息", () => beginGuidedAccountSetup(current.account, current.status));
        }
        return;
      }
      current.phase = "WAITING_EXTENSION";
      current.pairingBundle = "";
      $("#account-setup-copy").hidden = true;
      $("#account-setup-stage").textContent = "等待扩展连接";
      $("#account-setup-message").textContent = "扩展配对成功，正在建立 Bridge 连接…";
      paintSetupProgress("identity", ["bridge", "pairing"]);
      setAccountSetupPrimary("立即检测连接", pollGuidedAccountSetup);
      scheduleAccountSetupPoll(pollGuidedAccountSetup, 250);
      return;
    }

    const status = await fetchJson(`${api}/accounts/${encodeURIComponent(current.account.name)}/status`);
    current.status = status;
    current.recorded = Boolean(status.identity?.recorded);
    current.recordedUid = status.identity?.user_id || current.recordedUid;
    if (!status.extension_connected) {
      $("#account-setup-stage").textContent = "等待扩展连接";
      $("#account-setup-message").textContent = "Bridge 已在线，正在等待目标 Profile 的扩展连接…";
      setAccountSetupPrimary("立即检测连接", pollGuidedAccountSetup);
      scheduleAccountSetupPoll(pollGuidedAccountSetup);
      return;
    }
    if (!status.profile_verified) {
      $("#account-setup-stage").textContent = "Profile 不一致";
      $("#account-setup-message").textContent = status.next_action || "连接的扩展不属于当前槽位绑定的 Profile。";
      setAccountSetupGuidance("请检查打开的 Chrome Profile", "系统不会把错误 Profile 记录到当前槽位。", false);
      setAccountSetupPrimary("重新检测 Profile", pollGuidedAccountSetup);
      return;
    }
    await inspectGuidedIdentity();
  } catch (error) {
    $("#account-setup-message").textContent = error.message;
    scheduleAccountSetupPoll(pollGuidedAccountSetup, 1500);
  }
}

async function inspectGuidedIdentity() {
  if (!activeAccountSetup) return;
  const current = activeAccountSetup;
  $("#account-setup-stage").textContent = "正在核验身份";
  $("#account-setup-message").textContent = "正在读取当前小红书登录身份…";
  setAccountSetupPrimary("正在检测身份…", null, { disabled: true });
  try {
    const checked = await mutateJson(`${api}/accounts/${encodeURIComponent(current.account.name)}/identity/check`, "POST", {});
    const identity = checked.identity;
    if (!activeAccountSetup || activeAccountSetup.account.name !== current.account.name) return;
    current.identity = identity;
    $("#account-setup-nickname").textContent = identity.nickname || "未命名账号";
    $("#account-setup-uid").textContent = `UID ${identity.user_id}`;
    $("#account-setup-identity").hidden = false;
    if (!current.recorded) {
      current.phase = "WAITING_IDENTITY_CONFIRMATION";
      $("#account-setup-stage").textContent = "等待身份确认";
      $("#account-setup-message").textContent = "请确认把这个小红书账号绑定到当前槽位。";
      setAccountSetupPrimary("确认绑定此账号", confirmGuidedIdentity);
      paintSetupProgress("identity", ["bridge", "pairing"]);
      setAccountSetupGuidance("首次绑定需要你确认一次", "确认后系统会记录 UID；以后恢复连接时将自动核对，不再要求重复点击。", false);
      return;
    }
    if (current.recordedUid !== identity.user_id) {
      $("#account-setup-stage").textContent = "身份不一致";
      $("#account-setup-message").textContent = `槽位记录 UID ${current.recordedUid}，当前登录 UID ${identity.user_id}。`;
      setAccountSetupGuidance("当前登录账号与槽位记录不一致", "系统已停止进入 READY；请确认是登错账号，还是让当前账号接管这个固定 Profile。", false);
      setAccountSetupPrimary("处理账号不一致", () => {
        const { account, status } = current;
        closeGuidedAccountSetup();
        openIdentityMismatch(account, {
          ...status,
          status: "IDENTITY_MISMATCH",
          identity: {
            ...(status.identity || {}),
            recorded: true,
            user_id: current.recordedUid,
            live_user_id: identity.user_id,
            live_nickname: identity.nickname,
            matches_record: false,
          },
        });
      });
      return;
    }
    await finishGuidedAccountSetup(identity);
  } catch (error) {
    if (error.code === "LOGIN_REQUIRED") {
      if (accountSetupPollTimer) clearTimeout(accountSetupPollTimer);
      accountSetupPollTimer = null;
      current.phase = "WAITING_LOGIN";
      $("#account-setup-stage").textContent = "等待登录";
      $("#account-setup-message").textContent = `${error.message}，已停止自动核验。`;
      setAccountSetupGuidance(
        "请在当前 Profile 中登录小红书",
        "系统已停止自动检测；登录完成并停留在小红书页面后，点击“我已登录，立即检测”。",
        false
      );
      setAccountSetupPrimary("我已登录，立即检测", inspectGuidedIdentity);
      return;
    }
    if (error.code === "IDENTITY_UID_UNAVAILABLE") {
      current.phase = "WAITING_UID";
      $("#account-setup-stage").textContent = "等待 UID 可读";
      $("#account-setup-message").textContent = error.message;
      setAccountSetupGuidance("已检测到登录", "请停留在小红书首页，系统会继续尝试读取当前 UID。", true);
      setAccountSetupPrimary("立即重新检测 UID", inspectGuidedIdentity);
      scheduleAccountSetupPoll(inspectGuidedIdentity, 2000);
      return;
    }
    $("#account-setup-stage").textContent = "身份检测失败";
    $("#account-setup-message").textContent = error.message;
    setAccountSetupGuidance("暂时无法读取 UID", "请刷新当前 Profile 的小红书页面，系统会继续自动检测。", false);
    setAccountSetupPrimary("重新检测 UID", inspectGuidedIdentity);
    scheduleAccountSetupPoll(inspectGuidedIdentity, 2000);
  }
}

async function confirmGuidedIdentity() {
  if (!activeAccountSetup?.identity) return;
  try {
    await mutateJson(`${api}/accounts/${encodeURIComponent(activeAccountSetup.account.name)}/identity/record`, "POST", { confirmed: true });
    await finishGuidedAccountSetup(activeAccountSetup.identity);
  } catch (error) {
    $("#account-setup-message").textContent = error.message;
    setAccountSetupPrimary("重新确认绑定", confirmGuidedIdentity);
  }
}

async function finishGuidedAccountSetup(identity) {
  if (!activeAccountSetup) return;
  const accountName = activeAccountSetup.account.name;
  activeAccountSetup.phase = "READY";
  $("#account-setup-stage").textContent = "已就绪";
  $("#account-setup-message").textContent = `${identity.nickname || identity.user_id} 已完成连接与身份核验。`;
  setAccountSetupPrimary("配置完成", null, { disabled: true });
  paintSetupProgress("ready", setupSteps);
  setAccountSetupGuidance("账号已经 READY", "后续断线时点击“恢复连接”即可，UID 将自动核对。", true);
  await refreshAccountRoster();
  setTimeout(() => {
    if (activeAccountSetup?.account.name === accountName && activeAccountSetup.phase === "READY") closeGuidedAccountSetup();
  }, 900);
}

async function logoutAccount(account, messageNode, button) {
  if (!window.confirm(`确认退出槽位 ${account} 当前登录的小红书账号？\n\nChrome Profile、扩展和 Bridge 会保留。`)) return;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  messageNode.textContent = "正在结束小红书登录会话并回读登录状态…";
  try {
    const result = await mutateJson(`${api}/accounts/${encodeURIComponent(account)}/auth/logout`, "POST", {
      confirmed: true,
    });
    messageNode.textContent = result.message || "退出操作已完成。";
    await loadDashboard();
  } catch (error) {
    messageNode.textContent = `退出失败：${error.message}`;
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

function openAccountRemoval(account) {
  activeAccountRemoval = account;
  $("#remove-account-name").textContent = account.name;
  $("#remove-account-profile").textContent = `Chrome · ${account.chrome_profile_directory || "Default"}`;
  $("#remove-confirm-name").value = "";
  $("#remove-message").textContent = "";
  $("#remove-account-submit").disabled = true;
  $("#account-remove-dialog").showModal();
  $("#remove-confirm-name").focus();
}

function closeAccountRemoval() {
  $("#account-remove-dialog").close();
  activeAccountRemoval = null;
}

async function removeAccountSlot() {
  if (!activeAccountRemoval) return;
  const account = activeAccountRemoval.name;
  const confirmationName = $("#remove-confirm-name").value.trim();
  const button = $("#remove-account-submit");
  const message = $("#remove-message");
  if (confirmationName !== account) {
    message.textContent = `请输入完整槽位名称 ${account}`;
    return;
  }
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  message.textContent = "正在停止该槽位服务并保存本机归档…";
  try {
    const result = await mutateJson(`${api}/accounts/${encodeURIComponent(account)}`, "DELETE", {
      confirmed: true,
      confirmation_name: confirmationName,
    });
    closeAccountRemoval();
    await loadDashboard();
    $("#setup-message").textContent = `${result.message}；Chrome Profile、登录数据和共享扩展均未删除。`;
  } catch (error) {
    message.textContent = `删除失败：${error.message}`;
  } finally {
    button.disabled = $("#remove-confirm-name").value.trim() !== account;
    button.removeAttribute("aria-busy");
  }
}

async function updateBridge(account, action, messageNode, button) {
  const operation = {
    "start-only": {
      label: "启动 Bridge",
      confirmation: `只启动槽位 ${account} 的本地 Bridge？\n\n不会打开或关闭 Chrome。`,
    },
    stop: {
      label: "停止 Bridge",
      confirmation: `停止槽位 ${account} 的本地 Bridge？\n\n该槽位会暂时断开，但不会关闭 Chrome。`,
    },
    restart: {
      label: "重启账号连接",
      confirmation: `重启槽位 ${account} 的账号连接？\n\n系统会重启 Bridge；扩展未连接时会按需打开绑定的 Chrome Profile，但不会关闭 Chrome。`,
    },
  }[action];
  if (!operation || !window.confirm(operation.confirmation)) return;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  messageNode.textContent = `正在${operation.label}…`;
  try {
    const result = await mutateJson(`${api}/accounts/${encodeURIComponent(account)}/bridge/${action}`, "POST", {});
    const lifecycle = result.lifecycle || {};
    if (action === "start-only") {
      messageNode.textContent = lifecycle.bridge_running ? "Bridge 已启动；可继续检查扩展连接。" : "Bridge 未能进入运行状态。";
    } else if (action === "stop") {
      messageNode.textContent = lifecycle.bridge_running ? "Bridge 仍在运行，请稍后重试。" : "Bridge 已停止；Chrome 保持打开。";
    } else {
      messageNode.textContent = lifecycle.ready
        ? lifecycle.message || "账号连接已恢复，正在刷新状态。"
        : `账号尚未就绪：${lifecycle.message || "请检查 Bridge、扩展和 Profile 状态"}`;
    }
    await loadDashboard();
  } catch (error) {
    messageNode.textContent = error.message;
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

async function updateAutostart(account, messageNode) {
  try {
    const current = await fetchJson(`${api}/accounts/${encodeURIComponent(account)}/autostart`);
    const enabled = !current.autostart.enabled;
    if (!window.confirm(`${enabled ? "启用" : "关闭"} ${account} 的 Windows 登录账号自启动？\n\n启用后会在登录 Windows 时启动 Bridge，并按需打开该槽位绑定的 Chrome Profile。`)) return;
    await mutateJson(`${api}/accounts/${encodeURIComponent(account)}/autostart/update`, "POST", { enabled, confirmed: true });
    messageNode.textContent = `账号自启动已${enabled ? "启用" : "关闭"}。`;
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

function setSwitchGuidance(title, body, pending = false) {
  const guidance = $("#switch-guidance");
  const heading = document.createElement("strong");
  heading.textContent = title;
  const copy = document.createElement("p");
  copy.textContent = body;
  guidance.replaceChildren(heading, copy);
  guidance.classList.toggle("pending", pending);
}

function renderAccountSwitch(status) {
  const account = activeAccountSwitch?.account || "";
  const config = activeAccountSwitch?.config || accountConfig(account);
  const identity = status?.identity || {};
  const pending = identity.switch || null;
  const isPending = Boolean(identity.switch_pending || pending);
  activeAccountSwitch.status = status;

  const displayName = profileDisplayName(config);
  $("#switch-account-avatar").textContent = displayName.slice(0, 2).toUpperCase();
  $("#switch-account-name").textContent = displayName;
  $("#switch-profile-name").textContent = displayName;
  $("#switch-profile-directory").textContent = config.chrome_profile_directory || "Default";
  $("#switch-current-name").textContent = identity.nickname || identity.user_id || "尚未记录";
  $("#switch-current-uid").textContent = `UID ${identity.user_id || "—"}`;
  $("#switch-stage").textContent = isPending ? "等待新登录" : "准备换号";
  $("#switch-fields").hidden = isPending;
  $("#switch-cancel").hidden = !isPending;
  $("#switch-dismiss").textContent = isPending ? "关闭面板" : "暂不切换";
  $("#switch-primary").textContent = isPending ? "核验并完成切换" : "自动退出并开始换号";
  $("#switch-primary").dataset.action = isPending ? "complete" : "begin";
  $("#switch-message").textContent = "";

  document.querySelectorAll("[data-switch-step]").forEach((step) => {
    const name = step.dataset.switchStep;
    step.classList.toggle("active", isPending ? name === "login" : name === "begin");
    step.classList.toggle("done", isPending && name === "begin");
  });
  if (isPending) {
    $("#switch-label").value = pending?.target_label || "";
    $("#switch-target-uid").value = pending?.target_user_id || "";
    setSwitchGuidance(
      "请先在对应 Chrome Profile 登录新账号",
      "登录完成后回到这里点击“核验并完成切换”。系统会确认新 UID 与旧 UID 不同，再恢复该槽位的业务任务。",
      true
    );
  } else {
    $("#switch-label").value = "";
    $("#switch-target-uid").value = "";
    setSwitchGuidance(
      "开始后会退出当前小红书登录",
      "系统负责自动退出当前账号；槽位、Chrome Profile、扩展和 Bridge 保持不变。你只需在这个 Profile 中手动登录新账号，再回到这里完成核验。"
    );
  }
}

function openAccountSwitch(accountOrName, status) {
  const config = accountConfig(accountOrName);
  activeAccountSwitch = { account: config.name, config, status };
  renderAccountSwitch(status);
  $("#account-switch-dialog").showModal();
}

function closeAccountSwitch() {
  $("#account-switch-dialog").close();
  activeAccountSwitch = null;
}

async function runAccountSwitchAction(action) {
  if (!activeAccountSwitch) return;
  const account = activeAccountSwitch.account;
  const primary = $("#switch-primary");
  const cancel = $("#switch-cancel");
  const message = $("#switch-message");
  primary.disabled = true;
  cancel.disabled = true;
  primary.setAttribute("aria-busy", "true");
  message.textContent = action === "begin" ? "正在退出旧账号并锁定业务任务…" : "正在读取新账号 UID 并完成核验…";
  try {
    if (action === "begin") {
      const result = await mutateJson(`${api}/accounts/${encodeURIComponent(account)}/switch/begin`, "POST", {
        confirmed: true,
        label: $("#switch-label").value.trim(),
        target_user_id: $("#switch-target-uid").value.trim(),
      });
      renderAccountSwitch({
        ...activeAccountSwitch.status,
        status: "SWITCH_PENDING",
        identity: {
          ...activeAccountSwitch.status.identity,
          switch_pending: true,
          switch: result.switch,
        },
      });
      message.textContent = result.logged_out
        ? "旧账号已退出。请在对应 Chrome Profile 登录新账号。"
        : "换号流程已开始。请确认当前页面已退出，再登录新账号。";
      await loadDashboard();
    } else {
      const pending = activeAccountSwitch.status?.identity?.switch || {};
      await mutateJson(`${api}/accounts/${encodeURIComponent(account)}/switch/complete`, "POST", {
        confirmed: true,
        expected_user_id: pending.target_user_id || "",
        label: pending.target_label || "",
      });
      closeAccountSwitch();
      await loadDashboard();
    }
  } catch (error) {
    message.textContent = error.message;
  } finally {
    primary.disabled = false;
    cancel.disabled = false;
    primary.removeAttribute("aria-busy");
  }
}

async function cancelAccountSwitch() {
  if (!activeAccountSwitch) return;
  const account = activeAccountSwitch.account;
  const button = $("#switch-cancel");
  const message = $("#switch-message");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  message.textContent = "正在取消换号流程…";
  try {
    await mutateJson(`${api}/accounts/${encodeURIComponent(account)}/switch/cancel`, "POST", { confirmed: true });
    closeAccountSwitch();
    await loadDashboard();
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

function setMismatchGuidance(title, body, pending = false) {
  const guidance = $("#mismatch-guidance");
  const heading = document.createElement("strong");
  heading.textContent = title;
  const copy = document.createElement("p");
  copy.textContent = body;
  guidance.replaceChildren(heading, copy);
  guidance.classList.toggle("pending", pending);
}

function renderIdentityMismatch() {
  if (!activeIdentityMismatch) return;
  const { account, status, phase } = activeIdentityMismatch;
  const identity = status.identity || {};
  const displayName = profileDisplayName(account);
  $("#mismatch-avatar").textContent = displayName.slice(0, 2).toUpperCase();
  $("#mismatch-profile-name").textContent = displayName;
  $("#mismatch-profile-directory").textContent = account.chrome_profile_directory || "Default";
  $("#mismatch-recorded-name").textContent = identity.nickname || "未命名账号";
  $("#mismatch-recorded-uid").textContent = `UID ${identity.user_id || "—"}`;
  $("#mismatch-live-name").textContent = identity.live_nickname || "未命名账号";
  $("#mismatch-live-uid").textContent = `UID ${identity.live_user_id || "—"}`;
  $("#mismatch-stage").textContent = phase === "restore" ? "等待原账号登录" : phase === "replace" ? "等待覆盖确认" : "等待选择";
  $("#mismatch-message").textContent = "";
  $("#mismatch-restore").hidden = phase !== "choose";
  $("#mismatch-use-current").hidden = phase !== "choose";
  $("#mismatch-confirm-replace").hidden = phase !== "replace";
  $("#mismatch-recheck").hidden = phase === "replace";

  if (phase === "restore") {
    setMismatchGuidance(
      "请在这个固定 Profile 中登录原账号",
      `当前错误账号已经退出。请登录槽位原记录 UID ${identity.user_id || "—"}，然后点击“重新核验”。Profile 与槽位不会变化。`,
      true
    );
  } else if (phase === "replace") {
    setMismatchGuidance(
      "这会替换槽位保存的账号身份",
      `确认后，槽位记录将从 UID ${identity.user_id || "—"} 改为 UID ${identity.live_user_id || "—"}；固定 Profile、槽位标识、扩展和 Bridge 都保持不变。`
    );
  } else {
    setMismatchGuidance(
      "请选择这次登录是否正确",
      "如果登错了，退出当前账号并登录原账号；如果以后就使用当前账号，可以明确确认覆盖槽位记录的 UID。"
    );
  }
}

function openIdentityMismatch(accountOrName, status) {
  const account = accountConfig(accountOrName);
  activeIdentityMismatch = { account, status, phase: "choose" };
  renderIdentityMismatch();
  $("#identity-mismatch-dialog").showModal();
}

function closeIdentityMismatch() {
  $("#identity-mismatch-dialog").close();
  activeIdentityMismatch = null;
}

async function restoreRecordedIdentity() {
  if (!activeIdentityMismatch) return;
  const { account } = activeIdentityMismatch;
  const button = $("#mismatch-restore");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  $("#mismatch-message").textContent = "正在退出当前误登录账号…";
  try {
    await mutateJson(`${api}/accounts/${encodeURIComponent(account.name)}/auth/logout`, "POST", { confirmed: true });
    activeIdentityMismatch.phase = "restore";
    renderIdentityMismatch();
    $("#mismatch-message").textContent = "当前账号已退出。请在这个 Profile 中登录原账号。";
  } catch (error) {
    $("#mismatch-message").textContent = error.message;
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

async function recheckMismatchIdentity() {
  if (!activeIdentityMismatch) return;
  const { account } = activeIdentityMismatch;
  const expectedUid = activeIdentityMismatch.status.identity?.user_id || "";
  const button = $("#mismatch-recheck");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  $("#mismatch-message").textContent = "正在重新读取当前登录账号…";
  try {
    const checked = await mutateJson(`${api}/accounts/${encodeURIComponent(account.name)}/identity/check`, "POST", {});
    if (checked.identity.user_id === expectedUid) {
      closeIdentityMismatch();
      await loadDashboard();
      return;
    }
    activeIdentityMismatch.status.identity = {
      ...activeIdentityMismatch.status.identity,
      live_user_id: checked.identity.user_id,
      live_nickname: checked.identity.nickname,
    };
    activeIdentityMismatch.phase = "choose";
    renderIdentityMismatch();
    $("#mismatch-message").textContent = `当前仍是 UID ${checked.identity.user_id}，尚未恢复为槽位原账号。`;
  } catch (error) {
    $("#mismatch-message").textContent = error.message;
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

function prepareIdentityReplacement() {
  if (!activeIdentityMismatch) return;
  activeIdentityMismatch.phase = "replace";
  renderIdentityMismatch();
}

async function confirmIdentityReplacement() {
  if (!activeIdentityMismatch) return;
  const { account, status } = activeIdentityMismatch;
  const identity = status.identity || {};
  const button = $("#mismatch-confirm-replace");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  $("#mismatch-message").textContent = "正在核对当前登录账号并更新槽位身份…";
  try {
    await mutateJson(`${api}/accounts/${encodeURIComponent(account.name)}/identity/replace`, "POST", {
      confirmed: true,
      expected_recorded_user_id: identity.user_id,
      expected_observed_user_id: identity.live_user_id,
      label: identity.live_nickname || "",
    });
    closeIdentityMismatch();
    await loadDashboard();
  } catch (error) {
    $("#mismatch-message").textContent = error.message;
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

function emptyAccounts() {
  const empty = document.createElement("article");
  empty.className = "empty-card";
  const copy = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = "还没有账号槽位";
  const body = document.createElement("span");
  body.textContent = "点击右上角“添加账号”，系统会引导你完成 Profile、配对和 UID 核验。";
  copy.append(title, body);
  empty.append(copy);
  return empty;
}

function uniqueAccountKey(seed) {
  const normalized = text(seed)
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "") || "profile";
  const used = new Set(currentAccountData.accounts.map((account) => account.name));
  let candidate = normalized.slice(0, 32);
  let suffix = 2;
  while (used.has(candidate)) {
    const ending = `-${suffix}`;
    candidate = `${normalized.slice(0, 32 - ending.length)}${ending}`;
    suffix += 1;
  }
  return candidate;
}

function syncSelectedProfileName() {
  const selected = $("#profile-select").value;
  if (!selected) {
    $("#account-name").value = "";
    $("#account-key").value = "";
    return;
  }
  const profile = JSON.parse(selected);
  $("#account-name").value = profile.display_name;
  $("#account-key").value = uniqueAccountKey(profile.profile_directory);
}

function syncAccountMode() {
  const existing = $("#account-mode").value === "existing";
  $("#profile-field").hidden = !existing;
  $("#discover-profiles").hidden = !existing;
  $("#account-name").readOnly = existing;
  $("#account-name").maxLength = existing ? 80 : 32;
  $("#account-name-label").textContent = existing ? "槽位名称（跟随 Profile）" : "槽位名称";
  $("#account-name-help").textContent = existing
    ? "选择已有 Profile 后自动使用它的名称；内部槽位标识由系统生成。"
    : "新建独立槽位时使用英文字母、数字、短横线或下划线。";
  $("#account-name").placeholder = existing ? "请先扫描并选择 Profile" : "例如 brand-a";
  if (existing) syncSelectedProfileName();
  else {
    $("#account-name").value = "";
    $("#account-key").value = "";
  }
}

async function discoverProfiles() {
  const message = $("#setup-message");
  message.textContent = "正在扫描本机 Chrome Profile…";
  try {
    const payload = await mutateJson(`${api}/accounts/discover`, "POST", {});
    const select = $("#profile-select");
    select.replaceChildren();
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "请选择一个未绑定的 Profile";
    select.append(placeholder);
    let firstAvailable = "";
    payload.profiles.forEach((profile) => {
      const option = document.createElement("option");
      option.value = JSON.stringify({
        user_data_dir: profile.profile_path.replace(/[\\/]?[^\\/]+$/, ""),
        profile_directory: profile.profile_directory,
        display_name: profile.display_name || profile.profile_directory,
      });
      option.textContent = `${profile.display_name} · ${profile.profile_directory}${profile.bound_account ? `（已绑定 ${profile.bound_account}）` : ""}`;
      option.disabled = Boolean(profile.bound_account);
      select.append(option);
      if (!option.disabled && !firstAvailable) firstAvailable = option.value;
    });
    if (firstAvailable) {
      select.value = firstAvailable;
      syncSelectedProfileName();
      message.textContent = "已选择第一个未绑定 Profile；槽位名称已自动与 Profile 保持一致。";
    } else {
      message.textContent = payload.profiles.length ? "发现的 Profile 都已绑定。" : "没有发现可用的 Chrome Profile。";
    }
  } catch (error) {
    message.textContent = error.message;
  }
}

function setAccountCreatePanel(open) {
  const panel = $("#account-create-panel");
  panel.hidden = !open;
  $("#open-account-create").setAttribute("aria-expanded", String(open));
  if (!open) return;
  window.AutoXhsWorkspace?.navigate("accounts");
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
  window.setTimeout(() => ($("#account-mode").value === "existing" ? $("#profile-select") : $("#account-name")).focus(), 180);
}

async function submitAccount(event) {
  event.preventDefault();
  const message = $("#setup-message");
  const mode = $("#account-mode").value;
  const name = mode === "existing" ? $("#account-key").value.trim() : $("#account-name").value.trim();
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
        profile_display_name: profile.display_name,
        confirmed: true,
      });
    } else {
      payload = await mutateJson(`${api}/accounts`, "POST", { name, confirmed: true });
    }
    message.textContent = `${payload.account.name} 已添加，正在打开引导配置。`;
    event.target.reset();
    syncAccountMode();
    await loadDashboard();
    setAccountCreatePanel(false);
    const addedAccount = currentAccountData.accounts.find((account) => account.name === payload.account.name) || payload.account;
    await beginGuidedAccountSetup(addedAccount, accountStatusByName.get(payload.account.name) || {});
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
  const route = name === "confirmation" ? "approvals" : "tasks";
  window.AutoXhsWorkspace?.navigate(route);
}

function setupTaskPanels() {
  window.addEventListener("auto-xhs:workspace-change", (event) => {
    if (event.detail.route === "tasks") updateTaskAvailability();
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

function updateDraftFields() {
  const action = $("#draft-action").value;
  const mode = draftModes[action] || draftModes["post-comment"];
  $("#draft-target-label").textContent = mode.targetLabel;
  $("#draft-target").placeholder = mode.targetPlaceholder;
  $("#draft-summary-label").textContent = mode.summaryLabel;
  $("#draft-summary").placeholder = mode.summaryPlaceholder;
  $("#draft-content-label").textContent = mode.contentLabel;
  $("#draft-content").placeholder = mode.contentPlaceholder;
  $("#draft-action-help").textContent = mode.help;
  $("#draft-generate").hidden = action !== "post-comment";
  $("#draft-generation-help").hidden = action !== "post-comment";
}

function resetDraftForm() {
  $("#draft-form").reset();
  // Browsers may restore the previous select value after a reload. The product
  // default is always a new comment on a note, never a reply to a comment.
  $("#draft-action").value = "post-comment";
  updateDraftFields();
}

function commentDraftFocus(value) {
  const normalized = value.replace(/\s+/g, " ").replace(/[。！？!?；;]+$/u, "").trim();
  return normalized.length > 34 ? `${normalized.slice(0, 34)}…` : normalized;
}

function generateCommentDraft() {
  const message = $("#draft-message");
  if ($("#draft-action").value !== "post-comment") {
    message.textContent = "回复需要结合对方原话手动填写，系统没有生成回复。";
    return;
  }
  const focus = commentDraftFocus($("#draft-summary").value);
  if (!focus) {
    message.textContent = "请先填写笔记内容说明，系统才能生成相关的评论草稿。";
    $("#draft-summary").focus();
    return;
  }
  const suggestions = [
    `看完很有收获，${focus}这部分讲得很清楚，感谢分享！`,
    `${focus}这个角度很有启发，想请教一下实际操作时最需要注意什么？`,
    `对${focus}有了新的理解，内容整理得很清楚，谢谢分享。`,
  ];
  $("#draft-content").value = suggestions[commentSuggestionIndex % suggestions.length];
  commentSuggestionIndex += 1;
  message.textContent = "已生成基础评论草稿，请结合原笔记核对或修改后再保存。";
  $("#draft-content").focus();
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

function appendRandomCommentResults(container, result) {
  const overview = document.createElement("div");
  overview.className = "browse-result-overview";
  addResultMetric(overview, "候选", `${result.candidate_count ?? 0} 篇`);
  addResultMetric(overview, "成功", `${result.succeeded_count ?? 0} 条`);
  addResultMetric(overview, "失败", `${result.failed_count ?? 0} 条`);
  addResultMetric(overview, "搜集用时", `${Math.round(result.collection_elapsed_seconds ?? 0)} 秒`);
  container.append(overview);

  const styleLabels = { natural: "自然互动", praise: "友好认可", question: "提问互动" };
  const style = document.createElement("p");
  style.className = "result-note";
  style.textContent = `评论风格：${styleLabels[result.style] || result.style || "自然互动"}`;
  container.append(style);

  const list = document.createElement("ol");
  list.className = "browse-result-list random-comment-result-list";
  result.items.forEach((item) => {
    const row = document.createElement("li");
    const body = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = item.title || "未命名笔记";
    const meta = document.createElement("span");
    meta.textContent = [item.author ? `作者：${item.author}` : "", item.status === "success" ? "已发送" : `失败：${item.message || "未知原因"}`].filter(Boolean).join(" · ");
    const comment = document.createElement("p");
    comment.className = "random-comment-content";
    comment.textContent = item.content || "未生成评论内容";
    const identifier = document.createElement("small");
    identifier.textContent = `笔记 ID：${item.feed_id || "—"}`;
    body.append(title, meta, comment, identifier);
    row.dataset.state = item.status || "failed";
    row.append(body);
    list.append(row);
  });
  container.append(list);
}

function appendHomeEngagementResults(container, result) {
  const overview = document.createElement("div");
  overview.className = "browse-result-overview";
  addResultMetric(overview, "浏览", `${result.counts?.browsed ?? 0} 篇`);
  addResultMetric(overview, "点赞", `${result.counts?.liked ?? 0} 篇`);
  addResultMetric(overview, "评论", `${result.counts?.commented ?? 0} 篇`);
  addResultMetric(overview, "跳过", `${result.counts?.skipped ?? 0} 篇`);
  addResultMetric(overview, "总用时", `${Math.round(result.elapsed_seconds ?? 0)} 秒`);
  container.append(overview);

  const list = document.createElement("ol");
  list.className = "browse-result-list engagement-result-list";
  result.items.forEach((item) => {
    const row = document.createElement("li");
    const body = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = item.title || "未命名笔记";
    const meta = document.createElement("span");
    const actions = [];
    if (item.like) actions.push(`点赞：${item.like.status === "success" ? "成功" : "失败"}`);
    if (item.comment) actions.push(`评论：${item.comment.status === "success" ? "成功" : "失败"}`);
    meta.textContent = [`阅读 ${Math.round(item.read_seconds || 0)} 秒`, ...actions].join(" · ");
    body.append(title, meta);
    if (item.comment?.content) {
      const comment = document.createElement("p");
      comment.className = "random-comment-content";
      comment.textContent = item.comment.content;
      body.append(comment);
    }
    const identifier = document.createElement("small");
    identifier.textContent = `笔记 ID：${item.feed_id || "—"}`;
    body.append(identifier);
    row.append(body);
    list.append(row);
  });
  container.append(list);
}

function appendPrivateMessageResults(container, result) {
  const overview = document.createElement("div");
  overview.className = "browse-result-overview";
  addResultMetric(overview, "收件人", `${result.count ?? result.items.length} 人`);
  addResultMetric(overview, "确认成功", `${result.success_count ?? 0} 条`);
  container.append(overview);

  const stateLabels = {
    SUCCESS: "已发送",
    FAILED: "失败",
    BLOCKED: "需处理",
    RESULT_UNKNOWN: "结果未知",
  };
  const list = document.createElement("ol");
  list.className = "browse-result-list private-message-result-list";
  result.items.forEach((item) => {
    const row = document.createElement("li");
    row.dataset.state = item.state || "FAILED";
    const body = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = item.nickname || item.user_id || "未知收件人";
    const meta = document.createElement("span");
    meta.textContent = [
      stateLabels[item.state] || item.state,
      item.result_summary && item.state !== "SUCCESS" ? item.result_summary : "",
    ].filter(Boolean).join(" · ");
    const message = document.createElement("p");
    message.className = "random-comment-content";
    message.textContent = item.content || "";
    body.append(title, meta, message);
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
  } else if (result.result_type === "random_comment" && Array.isArray(result.items)) {
    appendRandomCommentResults(content, result);
  } else if (result.result_type === "home_engagement" && Array.isArray(result.items)) {
    appendHomeEngagementResults(content, result);
  } else if (result.result_type === "private_message_batch" && Array.isArray(result.items)) {
    appendPrivateMessageResults(content, result);
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

function appendPublishTaskPreview(card, item) {
  if (!publishMonitorCapabilities.has(item.capability)) return;
  const parameters = item.parameters || {};
  const preview = parameters.preview || {};
  const details = document.createElement("dl");
  details.className = "result-facts publish-monitor-facts";
  const values = [
    ["发布标题", preview.title],
    ["发布类型", { image: "图文", video: "视频", long_article: "长文" }[preview.kind] || preview.kind],
    ["素材数量", preview.asset_count !== undefined ? `${preview.asset_count} 个` : ""],
    ["可见范围", preview.visibility],
    ["计划时间", preview.schedule_at || "立即发布"],
    ["当前阶段", {
      preparing: "准备浏览器预览",
      template_selection: "选择长文模板",
      next_step: "进入长文发布页",
      preview_ready: "等待 WebUI 或 Agent 确认",
    }[parameters.stage] || parameters.stage],
  ];
  values.forEach(([label, value]) => {
    if (value === undefined || value === null || value === "") return;
    const term = document.createElement("dt");
    term.textContent = label;
    const description = document.createElement("dd");
    description.textContent = String(value);
    details.append(term, description);
  });
  if (details.childElementCount) card.append(details);
}

function publishTaskReadyForConfirmation(item) {
  return Boolean(
    item
    && publishMonitorCapabilities.has(item.capability)
    && item.state === "WAITING_APPROVAL"
    && item.parameters?.stage === "preview_ready"
  );
}

function publishKindLabel(kind) {
  return { image: "图文", video: "视频", long_article: "长文" }[kind] || kind || "发布内容";
}

function publishAssetName(asset) {
  const parts = text(asset).split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || text(asset);
}

function closePublishConfirmation() {
  activePublishTask = null;
  const dialog = $("#publish-confirm-dialog");
  if (dialog.open) dialog.close();
}

function openPublishConfirmation(task) {
  if (!publishTaskReadyForConfirmation(task)) {
    window.alert("这条发布任务尚未进入最终预览确认阶段，请刷新后重试。");
    return;
  }
  activePublishTask = task;
  const status = accountStatusByName.get(task.account_slot) || {};
  const account = accountConfig(task.account_slot);
  const identity = status.identity || {};
  const preview = task.parameters?.preview || {};
  const assets = Array.isArray(preview.assets) ? preview.assets : [];

  $("#publish-confirm-avatar").textContent = text(profileDisplayName(account)).slice(0, 2).toUpperCase();
  $("#publish-confirm-account").textContent = profileDisplayName(account);
  $("#publish-confirm-profile").textContent = `槽位 ${task.account_slot} · ${account.chrome_profile_directory || "Default"}`;
  $("#publish-confirm-kind").textContent = publishKindLabel(preview.kind);
  $("#publish-confirm-title").textContent = preview.title || "（无标题）";
  $("#publish-confirm-content").textContent = preview.description || preview.content || "（无正文）";
  $("#publish-confirm-tags").textContent = (preview.tags || []).length ? preview.tags.map((tag) => `#${tag}`).join(" ") : "无标签";
  $("#publish-confirm-visibility").textContent = preview.visibility || "公开可见";
  $("#publish-confirm-schedule").textContent = preview.schedule_at || "立即发布";
  $("#publish-confirm-identity").textContent = identity.live_nickname || identity.nickname || "未核验账号";
  $("#publish-confirm-uid").textContent = `UID ${identity.live_user_id || identity.user_id || "—"}`;

  const assetList = $("#publish-confirm-assets");
  assetList.replaceChildren();
  if (!assets.length) {
    const empty = document.createElement("li");
    empty.textContent = "未记录素材";
    assetList.append(empty);
  } else {
    assets.forEach((asset, index) => {
      const row = document.createElement("li");
      const order = document.createElement("span");
      const name = document.createElement("strong");
      order.textContent = String(index + 1).padStart(2, "0");
      name.textContent = publishAssetName(asset);
      name.title = text(asset);
      row.append(order, name);
      assetList.append(row);
    });
  }

  const ready = Boolean(status.ready && identity.live_user_id && identity.matches_record);
  const checkbox = $("#publish-preview-confirmed");
  checkbox.checked = false;
  checkbox.disabled = !ready;
  $("#publish-confirm-submit").disabled = true;
  $("#publish-save-draft").disabled = !ready;
  $("#publish-confirm-message").textContent = ready
    ? "请先在绑定的 Chrome Profile 中核对真实预览，再勾选确认。"
    : "当前账号不是 READY 状态，请先完成连接和 UID 核验。";
  $("#publish-confirm-dialog").showModal();
}

async function executePublishConfirmation(action) {
  if (!activePublishTask) return;
  const task = activePublishTask;
  const status = accountStatusByName.get(task.account_slot) || {};
  const identity = status.identity || {};
  const publish = action === "publish-confirm";
  if (publish && !$("#publish-preview-confirmed").checked) return;
  if (!publish && !window.confirm("确认不发布，并把当前浏览器中的内容保存到小红书草稿箱？")) return;

  const primary = $("#publish-confirm-submit");
  const draft = $("#publish-save-draft");
  primary.disabled = true;
  draft.disabled = true;
  primary.setAttribute("aria-busy", "true");
  draft.setAttribute("aria-busy", "true");
  $("#publish-confirm-message").textContent = publish ? "正在确认并等待平台回读发布结果…" : "正在保存到小红书草稿箱…";
  try {
    const result = await mutateJson(`${api}/tasks/${task.task_id}/${action}`, "POST", {
      account_slot: task.account_slot,
      verified_uid: identity.live_user_id || "",
      confirmed: true,
      ...(publish ? { preview_confirmed: true } : {}),
    });
    const finalTask = result.task || {};
    $("#publish-confirm-message").textContent = finalTask.result_summary || stateLabel(finalTask.state);
    await loadDashboard();
    if (["SUCCESS", "CANCELLED"].includes(finalTask.state)) closePublishConfirmation();
  } catch (error) {
    $("#publish-confirm-message").textContent = error.message;
    primary.disabled = !$("#publish-preview-confirmed").checked;
    draft.disabled = false;
  } finally {
    primary.removeAttribute("aria-busy");
    draft.removeAttribute("aria-busy");
  }
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
    appendPublishTaskPreview(card, item);
    appendTaskResult(card, item, { interactive });
    if (interactive && publishTaskReadyForConfirmation(item)) {
      const actions = document.createElement("div");
      actions.className = "task-actions";
      const review = document.createElement("button");
      review.type = "button";
      review.className = "primary-button";
      review.textContent = "查看并确认";
      review.addEventListener("click", () => openPublishConfirmation(item));
      actions.append(review);
      card.append(actions);
    } else if (interactive && !agentMonitorCapabilities.has(item.capability) && ["BLOCKED", "QUEUED", "WAITING_APPROVAL"].includes(item.state)) {
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
    option.dataset.account = account.name;
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

function refreshTaskAccountOptions() {
  const select = $("#task-account");
  if (!select) return;
  Array.from(select.options).forEach((option) => {
    const account = option.dataset.account;
    if (!account) return;
    const status = accountStatusByName.get(account) || {};
    const activity = currentAccountTaskActivity(account);
    option.textContent = activity
      ? `${account}（${activityShortLabel(activity)} · ${taskCapabilityLabel(activity.capability)}）`
      : `${account}（${stateLabel(status.status || "ERROR")} · 空闲）`;
  });
}

function updateTaskAvailability() {
  const select = $("#task-account");
  const button = $("#task-submit");
  const account = select.value;
  const status = accountStatusByName.get(account);
  const ready = Boolean(status?.ready);
  const activity = currentAccountTaskActivity(account);
  button.disabled = !ready || Boolean(activity);
  setActionButtonLabel(
    button,
    !ready ? "账号尚未就绪" : activity ? activityShortLabel(activity) : taskSubmitLabel()
  );
  if (!ready) {
    $("#task-message").textContent = "暂无 READY 账号，请先按账号卡片提示完成连接和 UID 核验。";
  } else if (activity) {
    $("#task-message").textContent = `账号 ${account} ${activityShortLabel(activity)}：${activity.request_summary || taskCapabilityLabel(activity.capability)}。可选择其他已就绪且空闲的账号并行执行。`;
  } else {
    $("#task-message").textContent = taskMessageByAccount.get(account)
      || ($("#task-capability").value === "random-comment"
        ? `账号 ${account} 已就绪；点击后会生成并直接发送本次随机评论。`
        : `账号 ${account} 已就绪且空闲，可以创建并执行任务。`);
  }
}

function taskActivityNeedsPolling() {
  return true;
}

function taskSnapshot(tasks) {
  return JSON.stringify(tasks.map((task) => [
    task.task_id,
    task.state,
    task.result_summary,
    task.error_code,
    task.parameters?.stage || "",
  ]));
}

function scheduleTaskActivityPoll() {
  if (taskActivityPollTimer) clearTimeout(taskActivityPollTimer);
  taskActivityPollTimer = null;
  if (!taskActivityNeedsPolling()) return;
  const active = pendingSubmissionByAccount.size > 0 || Array.from(accountTaskActivityByName.values()).some(
    (activity) => ["QUEUED", "RUNNING"].includes(activity.state)
  );
  taskActivityPollTimer = setTimeout(pollTaskActivity, active ? 1000 : 3000);
}

async function pollTaskActivity() {
  taskActivityPollTimer = null;
  const hadBackendActivity = Array.from(accountTaskActivityByName.values()).some(
    (activity) => activity.state === "RUNNING"
  );
  try {
    const payload = await fetchJson(`${api}/tasks`);
    const nextSnapshotKey = taskSnapshot(payload.tasks);
    if (nextSnapshotKey !== taskSnapshotKey) {
      taskSnapshotKey = nextSnapshotKey;
      await loadWorkData(currentAccountData, currentAccountStatuses);
      return;
    }
    rebuildAccountTaskActivity(payload.tasks);
    syncAccountActivityUI();
    const hasBackendActivity = Array.from(accountTaskActivityByName.values()).some(
      (activity) => activity.state === "RUNNING"
    );
    if (hadBackendActivity && !hasBackendActivity && pendingSubmissionByAccount.size === 0) {
      await loadWorkData(currentAccountData, currentAccountStatuses);
      return;
    }
  } catch (_error) {
    // The normal dashboard error state remains authoritative; retry on the next tick.
  }
  scheduleTaskActivityPoll();
}

async function loadWorkData(accountData, statuses) {
  const [tasks, records, drafts, inboundEvents, replyIntelligence] = await Promise.all([
    fetchJson(`${api}/tasks`),
    fetchJson(`${api}/records`),
    fetchJson(`${api}/drafts`),
    fetchJson(`${api}/inbound-events`),
    fetchJson(`${api}/reply-intelligence/status`),
  ]);
  const resultByTask = new Map(records.records.filter((record) => record.result && Object.keys(record.result).length).map((record) => [record.task_id, record.result]));
  const tasksWithResults = tasks.tasks.map((task) => ({ ...task, result: resultByTask.get(task.task_id) || task.result || {} }));
  taskSnapshotKey = taskSnapshot(tasks.tasks);
  rebuildAccountTaskActivity(tasksWithResults);
  renderTaskItems(tasksWithResults, "#task-list", "暂无任务", { interactive: true });
  renderTaskItems(
    tasksWithResults.filter(publishTaskReadyForConfirmation),
    "#publish-confirmation-list",
    "暂无待确认发布",
    { interactive: true }
  );
  renderTaskItems(records.records, "#record-list", "暂无执行记录");
  accountStatusByName = new Map(accountData.accounts.map((account, index) => [account.name, statuses[index]]));
  syncAccountActivityUI();
  populateAccountSelect($("#task-account"), accountData.accounts, statuses, { readyRequired: true });
  populateAccountSelect($("#draft-account"), accountData.accounts, statuses);
  refreshTaskAccountOptions();
  updateTaskAvailability();
  scheduleTaskActivityPoll();
  const publishConfirmations = tasks.tasks.filter(publishTaskReadyForConfirmation).length;
  $("#confirmation-count").textContent = drafts.drafts.filter((draft) => ["DRAFT", "CONFIRMED"].includes(draft.status)).length + publishConfirmations;
  renderDrafts(drafts.drafts);
  renderIntelligentReplyEvents(inboundEvents.events, replyIntelligence);
  renderReplyModelSettings(replyIntelligence);
}

function renderReplyModelSettings(status) {
  const badge = $("#reply-model-badge");
  const message = $("#reply-model-message");
  badge.textContent = status.configured ? "已配置" : "未配置";
  badge.className = `diagnosis-badge ${status.configured ? "good" : "warn"}`;
  $("#reply-model-base-url").value = status.base_url || "https://api.deepseek.com";
  $("#reply-model-name").value = status.model || "deepseek-v4-flash";
  $("#reply-model-timeout").value = status.timeout_seconds || 60;
  $("#reply-model-api-key").value = "";
  message.textContent = status.configured
    ? `配置已生效 · ${status.configuration_source === "webui" ? "WebUI 本地配置" : "环境变量"} · Key 不回显`
    : "填写兼容接口配置后保存；Key 只保存在本机。";
}

function commentCollectionAccountLabel(account, status) {
  const identity = status?.identity || {};
  const nickname = identity.live_nickname || identity.nickname || "昵称待核验";
  const uid = identity.live_user_id || identity.user_id || "UID 待核验";
  return `${profileDisplayName(account)} · ${nickname} · ${uid}（${stateLabel(status?.status || "ERROR")}）`;
}

function populateCommentCollectionAccounts(accounts, statuses) {
  const select = $("#comment-collection-account");
  const previous = select.value;
  select.replaceChildren();
  accounts.forEach((account, index) => {
    const status = statuses[index] || { status: "ERROR", ready: false };
    const option = document.createElement("option");
    option.value = account.name;
    option.disabled = !status.ready;
    option.textContent = commentCollectionAccountLabel(account, status);
    option.title = `槽位标识：${account.name}`;
    select.append(option);
  });
  const ready = Array.from(select.options).filter((option) => !option.disabled);
  const retained = ready.find((option) => option.value === previous);
  if (retained) select.value = retained.value;
  else if (ready[0]) select.value = ready[0].value;
  if (!ready.length) {
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = accounts.length ? "暂无 READY 账号" : "请先添加账号";
    placeholder.selected = true;
    select.prepend(placeholder);
  }
  updateCommentCollectionAvailability();
}

function updateCommentCollectionAvailability() {
  const account = $("#comment-collection-account").value;
  const status = accountStatusByName.get(account) || {};
  const busy = account ? accountHasUnfinishedTask(account) : false;
  const button = $("#collect-comments");
  const message = $("#comment-collection-message");
  button.disabled = !account || !status.ready || busy;
  if (!account) {
    message.textContent = "没有可采集的 READY 账号，请先到账号页完成连接和 UID 核验。";
  } else if (busy) {
    message.textContent = commentCollectionMessageByAccount.get(account)
      || `${commentCollectionAccountLabel(accountConfig(account), status)}正在执行其他任务。`;
  } else {
    message.textContent = commentCollectionMessageByAccount.get(account)
      || "点击后读取该账号“评论和@”通知，只写入本地事件收件箱。";
  }
}

function commentCollectionSummary(executed) {
  const task = executed.task || {};
  const result = executed.result || {};
  const events = result.inbound_events || {};
  if (!["SUCCESS", "PARTIAL_SUCCESS"].includes(task.state)) {
    return task.result_summary || task.recommended_action || stateLabel(task.state || "FAILED");
  }
  const prefix = task.state === "PARTIAL_SUCCESS" ? "采集部分完成" : "采集完成";
  return `${prefix}：读取 ${result.count ?? events.observed_count ?? 0} 条评论通知；新增 ${events.created_count ?? 0} 条，重复 ${events.duplicate_count ?? 0} 条。`;
}

async function collectNewComments(event) {
  event.preventDefault();
  const account = $("#comment-collection-account").value;
  const maxNotes = Number($("#comment-collection-max-notes").value);
  const message = $("#comment-collection-message");
  if (!accountStatusByName.get(account)?.ready) {
    message.textContent = "目标账号尚未 READY，评论没有采集。";
    return;
  }
  if (accountHasUnfinishedTask(account)) {
    message.textContent = "该账号已有未完成任务，请等待任务结束后再采集。";
    updateCommentCollectionAvailability();
    return;
  }
  pendingSubmissionByAccount.set(account, {
    capability: "collect-note-comments",
    request_summary: `读取最新 ${maxNotes} 条评论通知`,
  });
  commentCollectionMessageByAccount.set(account, "正在打开小红书通知页并读取“评论和@”…");
  message.textContent = commentCollectionMessageByAccount.get(account);
  updateCommentCollectionAvailability();
  syncAccountActivityUI();
  scheduleTaskActivityPoll();
  try {
    const created = await mutateJson(`${api}/tasks`, "POST", {
      source: "webui",
      account_slot: account,
      capability: "collect-note-comments",
      request_summary: `读取最新 ${maxNotes} 条评论通知`,
      parameters: { max_notes: maxNotes },
    });
    const executed = await mutateJson(`${api}/tasks/${created.task.task_id}/execute`, "POST", {});
    const summary = commentCollectionSummary(executed);
    commentCollectionMessageByAccount.set(account, summary);
    taskMessageByAccount.set(account, summary);
  } catch (error) {
    const summary = `采集失败：${error.message}`;
    commentCollectionMessageByAccount.set(account, summary);
    taskMessageByAccount.set(account, summary);
  } finally {
    pendingSubmissionByAccount.delete(account);
    await loadDashboard();
    updateCommentCollectionAvailability();
    syncAccountActivityUI();
    scheduleTaskActivityPoll();
  }
}

function accountSnapshot(accounts, statuses) {
  return JSON.stringify(accounts.map((account, index) => {
    const status = statuses[index] || {};
    return [
      account.name,
      account.extension_instance_enrolled,
      status.status,
      status.server_running,
      status.extension_connected,
      status.profile_verified,
      status.next_action,
      status.identity?.user_id || "",
      status.identity?.live_user_id || "",
      status.identity?.nickname || "",
      status.identity?.switch_pending || false,
    ];
  }));
}

async function fetchAccountStatuses(accountData) {
  return Promise.all(
    accountData.accounts.map(async (account) => {
      try {
        return await fetchJson(`${api}/accounts/${encodeURIComponent(account.name)}/status`);
      } catch (error) {
        return { status: "ERROR", ready: false, next_action: error.message };
      }
    })
  );
}

function renderAccountRoster(accountData, statuses) {
  currentAccountData = accountData;
  currentAccountStatuses = statuses;
  accountStatusByName = new Map(accountData.accounts.map((account, index) => [account.name, statuses[index]]));
  accountSnapshotKey = accountSnapshot(accountData.accounts, statuses);
  const grid = $("#account-grid");
  grid.replaceChildren();
  if (accountData.accounts.length === 0) grid.append(emptyAccounts());
  accountData.accounts.forEach((account, index) => grid.append(accountCard(account, statuses[index])));
  $("#account-total").textContent = accountData.accounts.length;
  $("#connected-total").textContent = statuses.filter((item) => item.extension_connected).length;
  $("#blocked-total").textContent = statuses.filter((item) => item.status !== "READY").length;
  $("#ready-total").textContent = statuses.filter((item) => item.status === "READY").length;
  populateAccountSelect($("#task-account"), accountData.accounts, statuses, { readyRequired: true });
  populateAccountSelect($("#draft-account"), accountData.accounts, statuses);
  populateCommentCollectionAccounts(accountData.accounts, statuses);
  refreshTaskAccountOptions();
  updateTaskAvailability();
}

async function refreshAccountRoster() {
  const accountData = await fetchJson(`${api}/accounts`);
  const statuses = await fetchAccountStatuses(accountData);
  const nextSnapshot = accountSnapshot(accountData.accounts, statuses);
  if (nextSnapshot !== accountSnapshotKey) renderAccountRoster(accountData, statuses);
  scheduleAccountStatusPoll();
  return { accountData, statuses };
}

function scheduleAccountStatusPoll() {
  if (accountStatusPollTimer) clearTimeout(accountStatusPollTimer);
  accountStatusPollTimer = null;
  if (activeAccountSetup) return;
  const allReady = currentAccountStatuses.length > 0 && currentAccountStatuses.every((item) => item.status === "READY");
  accountStatusPollTimer = setTimeout(pollAccountStatuses, allReady ? 5000 : 1000);
}

async function pollAccountStatuses() {
  accountStatusPollTimer = null;
  try {
    await refreshAccountRoster();
  } catch (_error) {
    scheduleAccountStatusPoll();
  }
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
    const title = document.createElement("strong"); title.textContent = `${draft.account_slot} · ${(draftModes[draft.action_type] || {}).label || draft.action_type}`;
    const content = document.createElement("p"); content.textContent = draft.content;
    card.append(badge, title, content);
    const intelligent = draft.metadata?.intelligent_reply;
    if (intelligent) {
      const meta = document.createElement("div");
      meta.className = "task-meta";
      const confidence = Number.isFinite(Number(intelligent.confidence))
        ? `${Math.round(Number(intelligent.confidence) * 100)}%`
        : "—";
      meta.textContent = `AI 草稿 · ${intelligent.intent || "other"} · 置信度 ${confidence}${intelligent.quality_flags?.length ? ` · 需留意 ${intelligent.quality_flags.join(", ")}` : ""}`;
      card.append(meta);
    }
    if (["DRAFT", "CONFIRMED"].includes(draft.status)) {
      const confirm = document.createElement("button"); confirm.className = "primary-button"; confirm.type = "button"; confirm.textContent = draft.status === "CONFIRMED" ? "重新核对并执行" : "核对并确认";
      confirm.addEventListener("click", () => confirmDraft(draft, card, confirm));
      card.append(confirm);
    }
    container.append(card);
  });
}

function renderIntelligentReplyEvents(events, status) {
  const container = $("#intelligent-reply-list");
  const statusLine = $("#reply-intelligence-status");
  statusLine.textContent = status.configured
    ? `模型 ${status.model} 已配置；生成结果只进入人工确认。`
    : `尚未配置：${(status.missing || []).join("、")}`;
  container.replaceChildren();
  const candidates = events
    .filter((event) => event.event_type === "note_comment" && !event.created_task_id)
    .slice(0, 20);
  if (!candidates.length) {
    const empty = emptyAccounts();
    empty.querySelector("strong").textContent = "暂无待处理新评论";
    empty.querySelector("span").textContent = "采集到新评论后可在这里生成智能回复。";
    container.append(empty);
    return;
  }
  candidates.forEach((event) => {
    const payload = event.payload || {};
    const card = document.createElement("article");
    card.className = "record-card";
    const badge = document.createElement("span"); badge.className = "badge warning"; badge.textContent = "待生成";
    const title = document.createElement("strong"); title.textContent = `${event.account_slot} · ${payload.nickname || payload.user_id || "用户"}`;
    const content = document.createElement("p"); content.textContent = payload.content || "评论内容为空";
    const meta = document.createElement("div"); meta.className = "task-meta"; meta.textContent = payload.note_title || payload.feed_id || event.object_id || "未知笔记";
    const actions = document.createElement("div"); actions.className = "task-actions";
    const button = document.createElement("button"); button.type = "button"; button.className = "secondary-button"; button.textContent = "生成 AI 回复草稿";
    button.disabled = !status.configured;
    button.addEventListener("click", () => generateIntelligentReplyDraft(event, card, button));
    actions.append(button);
    card.append(badge, title, content, meta, actions);
    container.append(card);
  });
}

async function generateIntelligentReplyDraft(event, card, button) {
  const identity = accountStatusByName.get(event.account_slot)?.identity || {};
  const verifiedUid = identity.live_user_id || identity.user_id || "";
  if (!verifiedUid) {
    card.querySelector("p").textContent = "该账号还没有可用的已核验 UID。";
    return;
  }
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "生成中…";
  try {
    const result = await mutateJson(`${api}/inbound-events/${encodeURIComponent(event.event_id)}/intelligent-reply-draft`, "POST", {
      account_slot: event.account_slot,
      verified_uid: verifiedUid,
      reply_style: "natural",
    });
    card.querySelector("p").textContent = `已生成：${result.generation.reply}`;
    await loadDashboard();
  } catch (error) {
    card.querySelector("p").textContent = error.message;
    button.disabled = false;
    button.textContent = original;
  }
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
    resetDraftForm();
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
  const collectedReply = draft.action_type === "reply-comment" && Boolean(draft.source_event_id);
  let feedId = draft.action_type === "post-comment" ? draft.target_id : "";
  let token = "";
  if (!collectedReply) {
    if (draft.action_type === "reply-comment") {
      feedId = window.prompt("请输入这条评论所属的笔记 ID：", "");
      if (!feedId) return;
    }
    token = window.prompt("请输入该笔记当前的 XSEC Token：", "");
    if (!token) return;
  }
  const actionLabel = (draftModes[draft.action_type] || {}).label || draft.action_type;
  const contextNote = collectedReply ? "\n来源：已采集评论，所属笔记信息将自动带入" : "";
  const accepted = window.confirm(`目标账号：${draft.account_slot}\nUID：${draft.verified_uid}\n动作：${actionLabel}\n目标：${draft.target_summary || draft.target_id}${contextNote}\n\n最终文本：\n${draft.content}\n\n确认后将立即执行，是否继续？`);
  if (!accepted) return;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "执行中…";
  try {
    const confirmed = await mutateJson(`${api}/drafts/${draft.draft_id}/confirm`, "POST", {});
    const result = await mutateJson(`${api}/drafts/${draft.draft_id}/execute`, "POST", {
      approval_id: confirmed.approval.approval_id,
      ...(collectedReply ? {} : {
        feed_id: feedId,
        comment_id: draft.action_type === "reply-comment" ? draft.target_id : "",
        xsec_token: token,
      }),
    });
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
  const account = $("#task-account").value;
  if (!accountStatusByName.get(account)?.ready) {
    $("#task-message").textContent = "目标账号尚未 READY，任务没有创建。";
    return;
  }
  if (accountHasUnfinishedTask(account)) {
    $("#task-message").textContent = `账号 ${account} 已有未完成任务，请等待完成或先在任务列表中处理。`;
    updateTaskAvailability();
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
  const commentStyle = $("#task-comment-style").value;
  const commentCount = Number($("#task-comment-count").value);
  const commentCandidatePool = Number($("#task-comment-candidate-pool").value);
  const commentCollectionMinutes = Number($("#task-comment-collection-minutes").value);
  const homeBrowseCount = Number($("#task-home-browse-count").value);
  const homeLikeCount = Number($("#task-home-like-count").value);
  const homeCommentCount = Number($("#task-home-comment-count").value);
  const homeDuration = Number($("#task-home-duration").value);
  const homeMinRead = Number($("#task-home-min-read").value);
  const homeMaxRead = Number($("#task-home-max-read").value);
  const homeStyle = $("#task-home-style").value;
  const parameters = capability === "browse-feeds"
    ? { duration_minutes: durationMinutes, count: browseCount }
    : capability === "search-feeds"
      ? { keyword: target }
      : capability === "keyword-engagement"
        ? { keyword: target, action: engagementAction, count: engagementCount, candidate_pool_size: candidatePoolSize, collection_minutes: collectionMinutes }
      : capability === "random-comment"
        ? { count: commentCount, candidate_pool_size: commentCandidatePool, collection_minutes: commentCollectionMinutes, style: commentStyle, direct_send_authorized: true }
      : capability === "home-engagement"
        ? { browse_count: homeBrowseCount, like_count: homeLikeCount, comment_count: homeCommentCount, duration_minutes: homeDuration, min_read_seconds: homeMinRead, max_read_seconds: homeMaxRead, style: homeStyle, direct_send_authorized: true }
      : capability === "list-feeds"
        ? {}
        : capability === "user-profile"
          ? { user_id: target, xsec_token: token }
          : { feed_id: target, xsec_token: token };
  const capabilityName = taskCapabilityLabel(capability);
  const requestSummary = capability === "browse-feeds"
    ? `${capabilityName}：${durationMinutes} 分钟 / ${browseCount} 篇`
    : capability === "keyword-engagement"
      ? `${capabilityName}：${target} / 抽取 ${engagementCount} 篇 / 候选池 ${candidatePoolSize} 篇`
    : capability === "random-comment"
      ? `${capabilityName}：直接发送 ${commentCount} 条 / 候选池 ${commentCandidatePool} 篇`
    : capability === "home-engagement"
      ? `${capabilityName}：浏览 ${homeBrowseCount} 篇 / 点赞 ${homeLikeCount} 篇 / 评论 ${homeCommentCount} 篇`
    : target ? `${capabilityName}：${target}` : capabilityName;
  pendingSubmissionByAccount.set(account, {
    capability,
    request_summary: requestSummary,
  });
  taskMessageByAccount.set(account, `正在为账号 ${account} 创建并执行任务…`);
  syncAccountActivityUI();
  scheduleTaskActivityPoll();
  try {
    const created = await mutateJson(`${api}/tasks`, "POST", { source: "webui", account_slot: account, capability, request_summary: requestSummary, parameters });
    const executed = await mutateJson(`${api}/tasks/${created.task.task_id}/execute`, "POST", {});
    taskMessageByAccount.set(
      account,
      executed.task.result_summary || executed.task.recommended_action || stateLabel(executed.task.state)
    );
  } catch (error) {
    taskMessageByAccount.set(account, `任务失败：${error.message}`);
  } finally {
    pendingSubmissionByAccount.delete(account);
    await loadDashboard();
    syncAccountActivityUI();
    scheduleTaskActivityPoll();
  }
}

function setActionButtonLabel(button, label) {
  const arrow = document.createElement("span");
  arrow.setAttribute("aria-hidden", "true");
  arrow.textContent = "→";
  button.replaceChildren(document.createTextNode(label), arrow);
}

function taskSubmitLabel() {
  return ["random-comment", "home-engagement"].includes($("#task-capability").value) ? "创建并直接发送" : "创建并执行";
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
  $("#random-comment-settings").hidden = !config.commentSettings;
  $("#home-engagement-settings").hidden = !config.homeEngagementSettings;
  $("#token-field").hidden = !config.tokenRequired;
  $("#task-token").required = config.tokenRequired;
  updateTaskAvailability();
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

async function loadDiagnosis() {
  const badge = $("#diagnosis-state");
  badge.textContent = "检查中";
  badge.className = "diagnosis-badge";
  try {
    renderDiagnosis(await fetchJson(`${api}/doctor`));
  } catch (error) {
    badge.textContent = "检查失败";
    badge.className = "diagnosis-badge warn";
    $("#diagnosis-note").textContent = error.message;
  }
}

async function loadDashboard() {
  const refresh = $("#refresh");
  const health = $("#health");
  const grid = $("#account-grid");
  refresh.disabled = true;
  refresh.setAttribute("aria-busy", "true");
  try {
    const [healthData, accountData, capabilityData, systemData] = await Promise.all([
      fetchJson(`${api}/health`),
      fetchJson(`${api}/accounts`),
      fetchJson(`${api}/capabilities`),
      fetchJson(`${api}/system/status`),
    ]);
    health.dataset.state = healthData.status === "ok" ? "ok" : "error";
    health.lastElementChild.textContent = healthData.status === "ok" ? "本地服务正常" : "本地服务异常";
    $("#capability-count").textContent = capabilityData.summary.enabled_in_v1;
    renderSystem(systemData);

    const statuses = await fetchAccountStatuses(accountData);
    renderAccountRoster(accountData, statuses);
    await loadWorkData(accountData, statuses);
    scheduleAccountStatusPoll();
    loadDiagnosis();
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
  window.AutoXhsWorkspace?.render(window.location.hash);
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

async function saveReplyModelSettings(event) {
  event.preventDefault();
  const button = $("#save-reply-model");
  const message = $("#reply-model-message");
  button.disabled = true;
  message.textContent = "正在保存模型配置…";
  try {
    const result = await mutateJson(`${api}/reply-intelligence/settings`, "POST", {
      confirmed: true,
      api_key: $("#reply-model-api-key").value,
      base_url: $("#reply-model-base-url").value,
      model: $("#reply-model-name").value,
      timeout_seconds: Number($("#reply-model-timeout").value),
    });
    renderReplyModelSettings(result);
    message.textContent = "模型配置已保存，可以测试连接。";
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function testReplyModelConnection() {
  const button = $("#test-reply-model");
  const message = $("#reply-model-message");
  button.disabled = true;
  message.textContent = "正在调用模型检查连接…";
  try {
    const result = await mutateJson(`${api}/reply-intelligence/test`, "POST", {});
    message.textContent = `${result.message} · ${result.model}`;
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
  }
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
$("#open-account-create").addEventListener("click", () => setAccountCreatePanel(true));
$("#close-account-create").addEventListener("click", () => setAccountCreatePanel(false));
$("#account-setup-close").addEventListener("click", closeGuidedAccountSetup);
$("#account-setup-dismiss").addEventListener("click", closeGuidedAccountSetup);
$("#account-setup-copy").addEventListener("click", () => copyGuidedPairingBundle(true));
$("#account-setup-primary").addEventListener("click", runAccountSetupPrimaryAction);
$("#account-setup-dialog").addEventListener("cancel", (event) => {
  event.preventDefault();
  closeGuidedAccountSetup();
});
$("#switch-dialog-close").addEventListener("click", closeAccountSwitch);
$("#switch-dismiss").addEventListener("click", closeAccountSwitch);
$("#switch-primary").addEventListener("click", (event) => runAccountSwitchAction(event.currentTarget.dataset.action));
$("#switch-cancel").addEventListener("click", cancelAccountSwitch);
$("#identity-mismatch-close").addEventListener("click", closeIdentityMismatch);
$("#mismatch-dismiss").addEventListener("click", closeIdentityMismatch);
$("#mismatch-restore").addEventListener("click", restoreRecordedIdentity);
$("#mismatch-recheck").addEventListener("click", recheckMismatchIdentity);
$("#mismatch-use-current").addEventListener("click", prepareIdentityReplacement);
$("#mismatch-confirm-replace").addEventListener("click", confirmIdentityReplacement);
$("#identity-mismatch-dialog").addEventListener("cancel", (event) => {
  event.preventDefault();
  closeIdentityMismatch();
});
$("#remove-dialog-close").addEventListener("click", closeAccountRemoval);
$("#remove-account-cancel").addEventListener("click", closeAccountRemoval);
$("#remove-account-submit").addEventListener("click", removeAccountSlot);
$("#publish-confirm-close").addEventListener("click", closePublishConfirmation);
$("#publish-confirm-dismiss").addEventListener("click", closePublishConfirmation);
$("#publish-confirm-submit").addEventListener("click", () => executePublishConfirmation("publish-confirm"));
$("#publish-save-draft").addEventListener("click", () => executePublishConfirmation("save-draft"));
$("#publish-preview-confirmed").addEventListener("change", (event) => {
  $("#publish-confirm-submit").disabled = !event.target.checked;
});
$("#publish-confirm-dialog").addEventListener("cancel", (event) => {
  event.preventDefault();
  closePublishConfirmation();
});
$("#remove-confirm-name").addEventListener("input", (event) => {
  const matches = Boolean(activeAccountRemoval && event.target.value.trim() === activeAccountRemoval.name);
  $("#remove-account-submit").disabled = !matches;
  $("#remove-message").textContent = matches ? "名称已匹配，可以删除槽位。" : "";
});
$("#global-pause").addEventListener("click", toggleGlobalPause);
$("#settings-form").addEventListener("submit", saveSettings);
$("#reply-model-form").addEventListener("submit", saveReplyModelSettings);
$("#test-reply-model").addEventListener("click", testReplyModelConnection);
$("#export-diagnostics").addEventListener("click", exportDiagnostics);
$("#discover-profiles").addEventListener("click", discoverProfiles);
$("#profile-select").addEventListener("change", syncSelectedProfileName);
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
  $("#task-comment-style").value = "natural";
  $("#task-comment-count").value = "1";
  $("#task-comment-candidate-pool").value = "20";
  $("#task-comment-collection-minutes").value = "2";
  renderTaskTemplateActions();
});
$("#task-capability").addEventListener("change", updateTaskFields);
$("#task-engagement-count").addEventListener("input", (event) => {
  const pool = $("#task-candidate-pool");
  const count = Math.max(1, Number(event.target.value) || 1);
  pool.min = String(count);
  if (Number(pool.value) < count) pool.value = String(count);
});
$("#task-comment-count").addEventListener("input", (event) => {
  const pool = $("#task-comment-candidate-pool");
  const count = Math.max(1, Number(event.target.value) || 1);
  pool.min = String(count);
  if (Number(pool.value) < count) pool.value = String(count);
});
$("#draft-form").addEventListener("submit", submitDraft);
$("#comment-collection-form").addEventListener("submit", collectNewComments);
$("#comment-collection-account").addEventListener("change", updateCommentCollectionAvailability);
$("#draft-action").addEventListener("change", updateDraftFields);
$("#draft-generate").addEventListener("click", generateCommentDraft);
$("#account-mode").addEventListener("change", syncAccountMode);
syncAccountMode();
renderTaskTemplateActions();
resetDraftForm();
setupTaskPanels();
setupNavigation();
loadDashboard();
