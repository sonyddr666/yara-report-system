(function () {
  const DEVICE_STORE_KEY = "yara_inventario_rig1_devices";
  const REPORT_PREFIX = "yara_relatorio_completo_dia_";
  const DATABASE_NAME = "yara_relatorio_completo";
  const DATABASE_STORE = "dias";
  const originalRequest = window.fetch.bind(window);
  let enabled = true;
  let restoreRequested = false;
  let restoreTimer = null;

  function requestInfo(input, init) {
    const raw = typeof input === "string" ? input : input?.url || "";
    let pathname = raw;
    try { pathname = new URL(raw, location.href).pathname; } catch (_) {}
    const method = String(init?.method || input?.method || "GET").toUpperCase();
    return { pathname, method };
  }

  function savedDevices() {
    try {
      const value = JSON.parse(localStorage.getItem(DEVICE_STORE_KEY) || "null");
      return Array.isArray(value) ? value : null;
    } catch (_) {
      return null;
    }
  }

  function jsonResponse(payload) {
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json; charset=utf-8" }
    });
  }

  function finishRestore() {
    if (!enabled) return;
    enabled = false;
    clearTimeout(restoreTimer);
    window.fetch = originalRequest;
  }

  window.__YARA_NATIVE_FETCH__ = originalRequest;
  window.__YARA_RESTORE_FETCH__ = function () {
    restoreRequested = true;
    clearTimeout(restoreTimer);
    restoreTimer = setTimeout(finishRestore, 3000);
  };

  window.fetch = function (input, init) {
    if (!enabled) return originalRequest(input, init);
    const request = requestInfo(input, init);
    if (request.pathname === "/api/devices" && request.method === "GET") {
      const devices = savedDevices();
      if (devices?.length) return Promise.resolve(jsonResponse({ ok: true, devices }));
    }
    if (request.pathname === "/api/devices" && request.method === "POST") {
      const result = Promise.resolve(jsonResponse({ ok: true, deferred: true }));
      if (restoreRequested) setTimeout(finishRestore, 0);
      return result;
    }
    return originalRequest(input, init);
  };

  function localStorageDays() {
    const days = [];
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (key?.startsWith(REPORT_PREFIX)) days.push(key);
    }
    return days;
  }

  function indexedDbCount() {
    return new Promise(resolve => {
      if (!window.indexedDB) return resolve(0);
      const request = indexedDB.open(DATABASE_NAME);
      request.onerror = () => resolve(0);
      request.onsuccess = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(DATABASE_STORE)) {
          db.close();
          resolve(0);
          return;
        }
        const transaction = db.transaction(DATABASE_STORE, "readonly");
        const count = transaction.objectStore(DATABASE_STORE).count();
        count.onsuccess = () => resolve(Number(count.result || 0));
        count.onerror = () => resolve(0);
        transaction.oncomplete = () => db.close();
      };
    });
  }

  function clearIndexedDbReports() {
    return new Promise(resolve => {
      if (!window.indexedDB) return resolve();
      const request = indexedDB.open(DATABASE_NAME);
      request.onerror = () => resolve();
      request.onsuccess = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(DATABASE_STORE)) {
          db.close();
          resolve();
          return;
        }
        const transaction = db.transaction(DATABASE_STORE, "readwrite");
        transaction.objectStore(DATABASE_STORE).clear();
        transaction.oncomplete = () => { db.close(); resolve(); };
        transaction.onerror = () => { db.close(); resolve(); };
      };
    });
  }

  window.addEventListener("load", function () {
    setTimeout(async function () {
      try {
        const response = await originalRequest("/api/state", { cache: "no-store" });
        if (!response.ok) return;
        const state = await response.json();
        if (Number(state?.reports?.count || 0) !== 0) return;
        const keys = localStorageDays();
        const databaseCount = await indexedDbCount();
        const total = keys.length + databaseCount;
        if (!total) return;
        const remove = confirm(
          `O servidor está novo e vazio, mas este navegador ainda possui ${total} registro(s) local(is) antigo(s).\n\n` +
          "OK: apagar somente os relatórios locais antigos.\nCancelar: manter os dados locais para recuperação."
        );
        if (!remove) return;
        keys.forEach(key => localStorage.removeItem(key));
        await clearIndexedDbReports();
        location.reload();
      } catch (error) {
        console.warn("Não foi possível verificar dados locais antigos:", error);
      }
    }, 3500);
  });
})();
