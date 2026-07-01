(function () {
  const STORE = "yara_inventario_rig1_devices";
  const originalRequest = window.fetch.bind(window);
  let enabled = true;

  function info(input, init) {
    const raw = typeof input === "string" ? input : input?.url || "";
    let path = raw;
    try { path = new URL(raw, location.href).pathname; } catch (_) {}
    const method = String(init?.method || input?.method || "GET").toUpperCase();
    return { path, method };
  }

  function savedDevices() {
    try {
      const value = JSON.parse(localStorage.getItem(STORE) || "null");
      return Array.isArray(value) ? value : null;
    } catch (_) {
      return null;
    }
  }

  function response(payload) {
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json; charset=utf-8" }
    });
  }

  window.__YARA_NATIVE_FETCH__ = originalRequest;
  window.__YARA_RESTORE_FETCH__ = function () {
    enabled = false;
    window.fetch = originalRequest;
  };

  window.fetch = function (input, init) {
    if (!enabled) return originalRequest(input, init);
    const request = info(input, init);
    if (request.path === "/api/devices" && request.method === "GET") {
      const devices = savedDevices();
      if (devices?.length) return Promise.resolve(response({ ok: true, devices }));
    }
    if (request.path === "/api/devices" && request.method === "POST") {
      return Promise.resolve(response({ ok: true, deferred: true }));
    }
    return originalRequest(input, init);
  };
})();
