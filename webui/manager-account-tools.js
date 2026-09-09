window.AutoXhsAccountTools = function ({state, mutateJson, fetchJson, managerApi, load}) {
  const titles = {create: "新增员工机账号", delete: "删除账号槽位", "pair-begin": "扩展配对",
    "switch-begin": "切换账号", "switch-complete": "完成换号", logout: "退出登录",
    "bridge-stop": "停止 Bridge"};
  const descriptions = {
    create: "选择账号所属员工机，创建独立环境或绑定该员工机已有的 Chrome Profile。",
    delete: "账号槽位将移入员工机归档，Chrome Profile 和小红书登录数据保留。请输入槽位名称确认。",
    "pair-begin": "生成配对信息后，在对应员工机的 Chrome Profile 扩展中粘贴并确认。",
    "switch-begin": "退出当前账号后，请在对应员工机的 Chrome Profile 中登录新账号。",
    "switch-complete": "在员工机登录新账号后，填写期望 UID 并核验身份，完成换号。",
    logout: "退出该员工机账号的小红书登录会话。",
    "bridge-stop": "停止该账号的 Bridge 服务。",
  };
  const node = (tag, text = "") => {
    const item = document.createElement(tag); item.textContent = text; return item;
  };
  async function copyPairingText(code) {
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(code.value);
        return true;
      } catch (_) {
        // Continue with selection-based copying inside the active dialog.
      }
    }
    code.focus();
    code.select();
    code.setSelectionRange(0, code.value.length);
    try {
      return document.execCommand("copy");
    } catch (_) {
      return false;
    }
  }
  async function call(machine, account, action, values = {}) {
    const payload = await mutateJson(`${managerApi}/machines/${encodeURIComponent(machine)}/account-lifecycle`,
      "POST", {account, action, values});
    return payload.operation;
  }
  async function inspectIdentity(account) {
    const dialog = node("dialog"); dialog.className = "manager-account-tools";
    dialog.setAttribute("aria-label", "账号身份核对");
    const heading = node("div"); heading.className = "manager-persona-toolbar";
    const close = node("button", "关闭"); close.type = "button"; close.className = "secondary-button";
    close.addEventListener("click", () => dialog.close());
    heading.append(node("h2", "账号身份核对"), close);
    const facts = node("dl"); facts.className = "manager-identity-summary";
    const result = node("p", "正在读取员工机当前登录身份并核对槽位记录…");
    result.className = "manager-account-tools-result"; result.setAttribute("role", "status");
    const confirm = node("button", "确认此账号并绑定"); confirm.type = "button";
    confirm.className = "primary-button"; confirm.hidden = true;
    const retry = node("button", "重新检查身份"); retry.type = "button";
    retry.className = "secondary-button"; retry.disabled = true;
    const actions = node("div"); actions.className = "manager-identity-actions";
    actions.append(confirm, retry);
    dialog.append(heading, node("p", `${account.machine_id} / ${account.account_slot}`), facts, result, actions);
    dialog.addEventListener("close", () => dialog.remove());
    document.body.append(dialog); dialog.showModal();
    const base = `${managerApi}/machines/${encodeURIComponent(account.machine_id)}/accounts/${encodeURIComponent(account.account_slot)}`;
    let observedUid = "";
    function show(payload, observed = {}) {
      const snapshot = payload.account || {};
      const identity = snapshot.identity || {};
      const platform = snapshot.platform || account.platform;
      observedUid = observed.user_id || identity.live_user_id || "";
      const fact = (label, value) => {
        const row = node("div"); row.append(node("dt", label), node("dd", value || "尚未读取"));
        facts.append(row);
      };
      facts.replaceChildren();
      fact("平台", platform === "rednote" ? "RedNote" : "小红书");
      fact("当前登录昵称", observed.nickname || identity.live_nickname);
      fact("当前登录 UID", observedUid);
      fact("槽位绑定 UID", identity.user_id || "尚未绑定");
      confirm.hidden = true;
      if (payload.success === false) {
        result.textContent = payload.message || "本次身份核对未完成，请重新检查。";
      } else if (identity.switch_pending) {
        result.textContent = "该槽位正在换号，请通过“完成换号”核验新账号。";
      } else if (!observedUid || identity.logged_out) {
        result.textContent = "当前登录身份尚未读取成功，请在员工机确认登录后重新检查。";
      } else if (observed.platform && observed.platform !== platform) {
        result.textContent = "当前登录平台与槽位平台不一致，请检查对应员工机的 Profile。";
      } else if (identity.recorded === false) {
        result.textContent = "已读取当前登录账号，等待首次绑定确认。请核对上方昵称与 UID。";
        confirm.hidden = false;
      } else if (identity.matches_record) {
        const ready = snapshot.raw_status === "READY" || snapshot.status === "READY";
        result.textContent = ready ? "身份核对一致，账号已就绪（READY）。"
          : `身份核对一致。${snapshot.recommended_action || snapshot.next_action || "请检查账号连接状态。"}`;
      } else {
        result.textContent = "当前登录 UID 与槽位记录不一致，请通过换号流程核验。";
      }
    }
    async function refreshList() {
      try {await load();} catch (_) {result.textContent += " 账号列表待下次刷新。";}
    }
    async function check() {
      retry.disabled = true; confirm.hidden = true;
      facts.replaceChildren(); observedUid = "";
      result.textContent = "正在读取员工机当前登录身份并核对槽位记录…";
      try {
        const payload = await mutateJson(`${base}/identity/check`, "POST", {});
        show(payload, payload.operation?.identity);
      } catch (error) {result.textContent = error.message;}
      finally {retry.disabled = false;}
      await refreshList();
    }
    confirm.addEventListener("click", async () => {
      confirm.disabled = true; retry.disabled = true;
      result.textContent = "正在重新核验并绑定所确认的 UID…";
      try {
        await call(account.machine_id, account.account_slot, "identity-record",
          {confirmed: true, expected_user_id: observedUid});
        confirm.hidden = true;
        const payload = await fetchJson(`${base}/status`);
        show(payload);
      } catch (error) {
        confirm.hidden = true;
        result.textContent = `${error.message} 请重新检查身份以核对最新结果。`;
      } finally {confirm.disabled = false; retry.disabled = false;}
      await refreshList();
    });
    retry.addEventListener("click", check);
    await check();
  }
  function open(action, account = {}) {
    if (action === "identity-check") return inspectIdentity(account);
    const dialog = node("dialog"); dialog.className = "manager-account-tools";
    dialog.setAttribute("aria-label", titles[action]);
    const heading = node("div"); heading.className = "manager-persona-toolbar";
    const close = node("button", "关闭"); close.type = "button"; close.className = "secondary-button";
    close.addEventListener("click", () => dialog.close());
    heading.append(node("h2", titles[action]), close);
    const form = node("form");
    const fields = {};
    function field(name, title, tag = "input") {
      const label = node("label"); const input = node(tag);
      input.name = name; fields[name] = input;
      label.append(node("span", title), input); form.append(label); return input;
    }
    function option(select, value, title) {
      const item = node("option", title); item.value = value; select.append(item);
    }
    function platformField(value) {
      const platform = field("platform", "账号平台", "select");
      option(platform, "", "请选择平台");
      option(platform, "xiaohongshu", "小红书（xiaohongshu.com）");
      option(platform, "rednote", "RedNote（rednote.com）");
      platform.value = value || "";
      platform.required = true;
      return platform;
    }
    if (action === "create") {
      const machine = field("machine", "所属员工机", "select"); machine.required = true;
      option(machine, "", "请选择员工机");
      state.machines.forEach((item) => option(machine, item.machine_id, item.host_alias || item.machine_id));
      if (state.machines.length === 1) machine.value = state.machines[0].machine_id;
      field("name", "账号槽位名称").required = true;
      platformField("");
      const mode = field("mode", "账号环境", "select");
      option(mode, "create", "创建独立 Chrome Profile"); option(mode, "import", "绑定已有 Chrome Profile");
      const profile = field("profile", "员工机 Chrome Profile", "select");
      const discover = node("button", "读取员工机 Profile"); discover.type = "button";
      form.append(discover);
      const updateMode = () => {
        profile.parentElement.hidden = mode.value !== "import";
        profile.required = mode.value === "import"; discover.hidden = mode.value !== "import";
      };
      mode.addEventListener("change", updateMode); updateMode();
      machine.addEventListener("change", () => profile.replaceChildren());
      discover.addEventListener("click", async () => {
        discover.disabled = true;
        try {
          if (!machine.value) throw new Error("请先选择员工机");
          const payload = await call(machine.value, "", "profiles");
          profile.replaceChildren();
          for (const item of payload.profiles || []) {
            if (item.bound_account) continue;
            option(profile, JSON.stringify(item), `${item.display_name || item.profile_directory} · ${item.profile_directory}`);
          }
          result.textContent = profile.options.length ? "请选择需要绑定的 Profile。" : "暂无可绑定的 Profile。";
        } catch (error) {result.textContent = error.message;}
        finally {discover.disabled = false;}
      });
    } else {
      form.append(node("strong", `${account.machine_id} / ${account.account_slot}`));
      if (action === "pair-begin") {
        const platform = platformField(account.platform);
        platform.disabled = Boolean(account.paired || account.extension_instance_id
          || account.identity?.user_id || account.recorded_user_id || account.expected_user_id);
        form.append(node("p", platform.disabled ? "配对信息使用该账号已绑定的平台。"
          : "请选择该账号实际登录的平台，配对信息和账号平台将一同保存。"));
      }
      if (action === "delete") field("confirmation_name", "输入完整槽位名称").required = true;
      if (action === "switch-begin") field("target_user_id", "新账号 UID（可选）");
      if (action === "switch-complete") field("expected_user_id", "新账号 UID").required = true;
    }
    const submit = node("button", action === "create" ? "创建账号" : `确认${titles[action]}`);
    submit.type = "submit"; submit.className = "primary-button";
    const result = node("p"); result.className = "manager-account-tools-result";
    result.setAttribute("role", "status");
    form.append(submit, result);
    form.addEventListener("submit", async (event) => {
      event.preventDefault(); submit.disabled = true; result.textContent = "正在提交…";
      const machine = fields.machine?.value || account.machine_id;
      const slot = fields.name?.value.trim() || account.account_slot || "";
      let verb = action;
      const values = {confirmed: true};
      try {
        if (fields.platform && (!fields.platform.value
          || !["xiaohongshu", "rednote"].includes(fields.platform.value))) {
          throw new Error("请选择账号平台");
        }
        if (fields.platform && (action === "create" || fields.platform.value !== account.platform)) {
          values.platform = fields.platform.value;
        }
        for (const name of ["confirmation_name", "target_user_id", "expected_user_id"]) {
          if (fields[name]) values[name] = fields[name].value.trim();
        }
        if (action === "create") {
          values.name = slot; verb = fields.mode.value;
          if (verb === "import") {
            const profile = JSON.parse(fields.profile.value);
            values.user_data_dir = profile.profile_path.replace(/[\\/]?[^\\/]+$/, "");
            values.profile_directory = profile.profile_directory;
            values.profile_display_name = profile.display_name || "";
          }
        }
        const operation = await call(machine, slot, verb, values);
        result.textContent = operation.message || operation.next_action || "操作已完成。";
        if (action === "pair-begin") {
          const bundle = operation.pairing?.pairing_bundle || "";
          const pairedPlatform = operation.pairing?.platform || fields.platform.value;
          fields.platform.value = pairedPlatform;
          fields.platform.disabled = true;
          const code = node("textarea"); code.readOnly = true; code.value = bundle;
          code.setAttribute("aria-label", "扩展配对信息");
          const copy = node("button", "复制配对信息"); copy.type = "button";
          copy.className = "secondary-button";
          const copyStatus = node("p"); copyStatus.className = "manager-account-tools-result";
          copyStatus.setAttribute("role", "status");
          copy.disabled = !bundle;
          copy.addEventListener("click", async () => {
            copy.disabled = true;
            copy.textContent = "正在复制…";
            const copied = await copyPairingText(code);
            copy.textContent = copied ? "已复制配对信息" : "重新复制配对信息";
            copyStatus.textContent = copied ? "已复制，请在对应员工机扩展中粘贴并确认配对。"
              : "已选中全部配对信息，请按 Ctrl+C 复制。";
            copy.disabled = false;
          });
          const check = node("button", "检查配对结果"); check.type = "button";
          check.addEventListener("click", async () => {
            check.disabled = true;
            try {
              const response = await call(machine, slot, "pair-status");
              result.textContent = response.pairing?.paired ? "配对已完成，可检查账号身份。" : "等待员工机扩展确认配对。";
            } catch (error) {result.textContent = error.message;}
            finally {check.disabled = false;}
          });
          form.append(code, copy, copyStatus, check);
          result.textContent = `配对平台：${pairedPlatform === "rednote" ? "RedNote" : "小红书"}。`
            + "请将配对信息粘贴到对应员工机 Profile 的扩展中并确认。";
        }
        submit.hidden = true;
        try {
          await fetchJson(`${managerApi}/machines/${encodeURIComponent(machine)}/accounts`);
          await load();
        } catch (_) {result.textContent += " 账号列表待下次刷新。";}
      } catch (error) { result.textContent = error.message; }
      finally {submit.disabled = false;}
    });
    dialog.append(heading, node("p", descriptions[action]), form);
    dialog.addEventListener("close", () => dialog.remove());
    document.body.append(dialog); dialog.showModal();
  }
  return {open};
};
