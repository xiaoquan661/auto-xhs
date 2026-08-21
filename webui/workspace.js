(() => {
  const definitions = {
    overview: {
      breadcrumb: "本地工作台 · 今日",
      title: "今日总览",
      description: "先处理异常和待确认事项，再开始新的运营任务。",
    },
    accounts: {
      breadcrumb: "本地工作台 · 账号",
      title: "账号",
      description: "查看每个槽位的连接、Profile、扩展与身份状态。",
    },
    tasks: {
      breadcrumb: "本地工作台 · 任务",
      title: "任务",
      description: "按运营目标创建任务，并持续查看执行状态。",
    },
    approvals: {
      breadcrumb: "本地工作台 · 人工确认",
      title: "确认中心",
      description: "集中核对账号、目标与最终文本，再决定是否执行。",
    },
    records: {
      breadcrumb: "本地工作台 · 结果",
      title: "执行记录",
      description: "查看任务终态、结果摘要和失败原因。",
    },
    system: {
      breadcrumb: "本地工作台 · 系统",
      title: "系统",
      description: "诊断运行环境，并管理并发、配额和熔断规则。",
    },
  };

  const aliases = {
    top: "overview",
    work: "tasks",
    setup: "accounts",
    diagnosis: "system",
  };

  function normalize(value) {
    const requested = String(value || "").replace(/^#/, "");
    const route = aliases[requested] || requested;
    return definitions[route] ? route : "overview";
  }

  function render(route, { focus = false } = {}) {
    const activeRoute = normalize(route);
    document.querySelectorAll(".workspace-view[data-workspace]").forEach((view) => {
      const active = view.dataset.workspace === activeRoute;
      view.hidden = !active;
      view.classList.toggle("active", active);
    });
    document.querySelectorAll("[data-workspace-link]").forEach((link) => {
      const active = link.dataset.workspaceLink === activeRoute;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });

    const meta = definitions[activeRoute];
    const breadcrumb = document.querySelector("#workspace-breadcrumb");
    const title = document.querySelector("#workspace-title");
    const description = document.querySelector("#workspace-description");
    if (breadcrumb) breadcrumb.textContent = meta.breadcrumb;
    if (title) title.textContent = meta.title;
    if (description) description.textContent = meta.description;
    document.title = `${meta.title} · auto-xhs`;
    document.body.dataset.workspace = activeRoute;
    if (focus && title) title.focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: "auto" });
    window.dispatchEvent(new CustomEvent("auto-xhs:workspace-change", { detail: { route: activeRoute } }));
    return activeRoute;
  }

  function navigate(route, { replace = false, focus = false } = {}) {
    const activeRoute = normalize(route);
    const targetHash = `#${activeRoute}`;
    if (window.location.hash !== targetHash) {
      if (replace) window.history.replaceState(null, "", targetHash);
      else window.history.pushState(null, "", targetHash);
    }
    return render(activeRoute, { focus });
  }

  function setup() {
    document.addEventListener("click", (event) => {
      const link = event.target.closest("[data-workspace-link]");
      if (!link) return;
      event.preventDefault();
      navigate(link.dataset.workspaceLink, { focus: true });
    });
    window.addEventListener("hashchange", () => render(window.location.hash, { focus: true }));
    render(window.location.hash, { focus: false });
  }

  window.AutoXhsWorkspace = { definitions, navigate, render, current: () => normalize(window.location.hash) };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setup, { once: true });
  else setup();
})();
