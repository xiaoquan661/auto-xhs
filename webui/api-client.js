(() => {
  const api = "/api/v1";
  let sessionToken = "";

  async function fetchJson(path) {
    const response = await fetch(path, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || payload.success === false) {
      const error = new Error(payload.error?.message || "请求失败");
      error.code = payload.error?.code || "REQUEST_FAILED";
      throw error;
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
    if (!response.ok || payload.success === false) {
      const error = new Error(payload.error?.message || "请求失败");
      error.code = payload.error?.code || "REQUEST_FAILED";
      throw error;
    }
    return payload;
  }

  window.AutoXhsApi = { api, fetchJson, mutateJson };
})();
