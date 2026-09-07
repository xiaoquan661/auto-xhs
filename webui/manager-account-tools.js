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
  async function call(machine, account, action, values = {}) {
    const payload = await mutateJson(`${managerApi}/machines/${encodeURIComponent(machine)}/account-lifecycle`,
      "POST", {account, action, values});
    return payload.operation;
  }
  function open(action, account = {}) {
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
    if (action === "create") {
      const machine = field("machine", "所属员工机", "select"); machine.required = true;
      option(machine, "", "请选择员工机");
      state.machines.forEach((item) => option(machine, item.machine_id, item.host_alias || item.machine_id));
      if (state.machines.length === 1) machine.value = state.machines[0].machine_id;
      field("name", "账号槽位名称").required = true;
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
          const code = node("textarea"); code.readOnly = true; code.value = bundle;
          code.setAttribute("aria-label", "扩展配对信息");
          const copy = node("button", "复制配对信息"); copy.type = "button";
          copy.addEventListener("click", async () => {
            try {await navigator.clipboard.writeText(bundle); result.textContent = "已复制，请在员工机扩展中确认配对。";}
            catch (_) {result.textContent = "请选中上方配对信息手动复制。";}
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
          form.append(code, copy, check);
          result.textContent = "请将配对信息粘贴到对应员工机 Profile 的扩展中并确认。";
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
