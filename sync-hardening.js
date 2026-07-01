/* Persistência segura. Não altera HTML, CSS, impressão ou dimensões da página. */
(function () {
  const nativeFetch = window.__YARA_NATIVE_FETCH__ || window.fetch.bind(window);
  if (typeof window.__YARA_RESTORE_FETCH__ === "function") window.__YARA_RESTORE_FETCH__();

  const DEVICE_KEY = "yara_inventario_rig1_devices";
  const SYNC_KEY = "yara_sync_state_v4";
  const SNAPSHOT_PREFIX = "yara_snapshot_local_";
  const reportQueues = new Map();
  const reportHashes = new Map();
  let deviceQueue = Promise.resolve();
  let reconciling = false;

  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  function text(value) { return String(value ?? "").trim(); }
  function ip(value) { return text(value).toLowerCase(); }
  function nowName() { return new Date().toISOString().replaceAll(":", "-").replaceAll(".", "-"); }

  function readSync() {
    try { return JSON.parse(localStorage.getItem(SYNC_KEY) || "{}"); }
    catch (_) { return {}; }
  }
  function writeSync(next) { localStorage.setItem(SYNC_KEY, JSON.stringify(next)); }
  function patchSync(patch) { writeSync({ ...readSync(), ...patch }); }

  function stable(value) {
    if (Array.isArray(value)) return value.map(stable);
    if (!value || typeof value !== "object") return value;
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, stable(value[key])]));
  }

  async function digest(value) {
    const raw = JSON.stringify(stable(value));
    if (crypto?.subtle) {
      const data = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(raw));
      return Array.from(new Uint8Array(data), byte => byte.toString(16).padStart(2, "0")).join("");
    }
    let hash = 2166136261;
    for (let index = 0; index < raw.length; index += 1) hash = Math.imul(hash ^ raw.charCodeAt(index), 16777619);
    return String(hash >>> 0);
  }

  async function request(path, options = {}) {
    const response = await nativeFetch(path, { cache: "no-store", ...options });
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) {
      const error = new Error(data.message || data.error || `Servidor retornou ${response.status}`);
      error.status = response.status;
      error.data = data;
      throw error;
    }
    return data;
  }

  function downloadJson(filename, data) {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1500);
  }

  function normalizeDevices(list) {
    const result = [];
    const positions = new Map();
    (Array.isArray(list) ? list : []).forEach((raw, index) => {
      if (!raw || typeof raw !== "object" || !ip(raw.ip)) return;
      const device = typeof normalizeDevice === "function" ? normalizeDevice(raw, index) : { ...raw };
      const key = ip(device.ip);
      if (positions.has(key)) {
        const current = result[positions.get(key)];
        Object.entries(device).forEach(([field, value]) => {
          if (!text(current[field]) && text(value)) current[field] = value;
        });
      } else {
        device.number = device.number || result.length + 1;
        positions.set(key, result.length);
        result.push(device);
      }
    });
    return result;
  }

  function mergeDevices(server, local) {
    const merged = new Map();
    normalizeDevices(server).forEach(device => merged.set(ip(device.ip), { ...device }));
    normalizeDevices(local).forEach(device => {
      const key = ip(device.ip);
      merged.set(key, { ...(merged.get(key) || {}), ...device });
    });
    return Array.from(merged.values()).map((device, index) => ({ ...device, number: device.number || index + 1 }));
  }

  function localDevicesRaw() {
    try {
      const value = JSON.parse(localStorage.getItem(DEVICE_KEY) || "null");
      return Array.isArray(value) ? normalizeDevices(value) : [];
    } catch (_) { return []; }
  }

  function saveLocalDevices(devices) {
    const normalized = normalizeDevices(devices);
    localStorage.setItem(DEVICE_KEY, JSON.stringify(normalized));
    if (typeof refreshDeviceSelectors === "function") refreshDeviceSelectors();
    return normalized;
  }

  async function serverBackupDownload(label = "antes-substituicao") {
    const result = await request("/api/export");
    downloadJson(`backup-servidor-${label}-${nowName()}.json`, result.backup);
  }

  async function persistDevices(devices, mode = "replace", expectedRevision = null) {
    const normalized = saveLocalDevices(devices);
    const payload = { devices: normalized, mode };
    if (expectedRevision !== null && expectedRevision !== undefined) payload.expectedRevision = expectedRevision;
    const result = await request("/api/devices", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    patchSync({ deviceRevision: result.revision, deviceHash: result.contentHash, devicesPending: false });
    return { ...result, devices: normalized };
  }

  window.saveDeviceData = function (devices) {
    const normalized = saveLocalDevices(devices);
    patchSync({ devicesPending: true });
    deviceQueue = deviceQueue
      .catch(() => null)
      .then(() => persistDevices(normalized, "replace"))
      .catch(error => {
        console.error("Equipamentos mantidos localmente; servidor não confirmou:", error);
        patchSync({ devicesPending: true, deviceError: error.message });
        if (typeof showSaveStatus === "function") showSaveStatus("save-error", "Cadastro salvo só no navegador");
      });
    return normalized;
  };

  async function chooseDeviceConflict(local, server, serverMeta) {
    const choice = prompt(
      `A base local e a base do servidor estão diferentes.\n\n` +
      `Local: ${local.length} equipamentos\nServidor: ${server.length} equipamentos\n\n` +
      `Digite M para MESCLAR, S para usar SERVIDOR, L para usar LOCAL ou C para CANCELAR.`,
      "M"
    );
    const selected = text(choice).toUpperCase();
    if (!choice || selected === "C") return local;
    if (selected === "S") {
      localStorage.setItem(`${SNAPSHOT_PREFIX}${Date.now()}`, JSON.stringify({ devices: local }));
      return saveLocalDevices(server);
    }
    if (selected === "L") {
      await serverBackupDownload("antes-usar-local");
      return (await persistDevices(local, "replace", serverMeta.revision)).devices;
    }
    const merged = mergeDevices(server, local);
    await serverBackupDownload("antes-mesclar");
    return (await persistDevices(merged, "replace", serverMeta.revision)).devices;
  }

  async function reconcileDevices() {
    if (reconciling) return;
    reconciling = true;
    try {
      const hadLocal = localStorage.getItem(DEVICE_KEY) !== null;
      const local = hadLocal ? localDevicesRaw() : [];
      const result = await request("/api/devices");
      const server = normalizeDevices(result.devices);
      const localHash = await digest(local);
      const serverHash = await digest(server);

      if (!local.length && server.length) saveLocalDevices(server);
      else if (local.length && !server.length) {
        const send = confirm(`O servidor está vazio e existem ${local.length} equipamentos no navegador. Enviar a base local ao servidor?`);
        if (send) await persistDevices(local, "replace", result.revision);
      } else if (localHash !== serverHash) {
        await chooseDeviceConflict(local, server, result);
      } else {
        patchSync({ deviceRevision: result.revision, deviceHash: result.contentHash, devicesPending: false });
      }
      if (typeof refreshDeviceSelectors === "function") refreshDeviceSelectors();
      if (typeof refreshMonthlyUsedDevices === "function") await refreshMonthlyUsedDevices();
    } catch (error) {
      console.warn("Comparação local/servidor não concluída:", error);
    } finally {
      reconciling = false;
    }
  }

  function reportForHash(report) {
    const copy = clone(report);
    delete copy.updatedAt;
    delete copy.revision;
    delete copy.contentHash;
    delete copy.expectedRevision;
    return copy;
  }

  async function storeReportLocally(report, dayKey) {
    try { await saveToDatabase(report, dayKey); }
    catch (error) { console.warn("IndexedDB não salvou:", error); }
    try { localStorage.setItem(storageKey(dayKey), JSON.stringify(withoutImages(report))); }
    catch (error) { console.warn("Resumo local não salvou:", error); }
  }

  async function resolveReportConflict(report, error) {
    const choice = prompt(
      `O relatório ${report.dayKey} foi alterado no servidor.\n` +
      `Digite S para usar SERVIDOR, L para substituir pelo LOCAL ou C para cancelar.`,
      "S"
    );
    const selected = text(choice).toUpperCase();
    if (selected === "L") {
      const forced = { ...report };
      delete forced.expectedRevision;
      return sendReport(forced, true);
    }
    if (selected === "S") {
      const current = await request(`/api/report?dayKey=${encodeURIComponent(report.dayKey)}`);
      if (current.report) {
        await storeReportLocally(current.report, report.dayKey);
        patchSync({ [`reportRevision:${report.dayKey}`]: current.report.revision, [`reportPending:${report.dayKey}`]: false });
      }
      return { photosIncluded: true, serverSaved: false, conflict: true };
    }
    throw error;
  }

  async function sendReport(report, force = false) {
    const dayKey = report.dayKey;
    const hash = await digest(reportForHash(report));
    if (!force && reportHashes.get(dayKey) === hash) return { photosIncluded: true, serverSaved: true, deduplicated: true };
    const sync = readSync();
    const payload = { ...report, syncMode: "replace" };
    const revision = sync[`reportRevision:${dayKey}`] ?? report.revision;
    if (revision !== undefined && revision !== null) payload.expectedRevision = revision;
    try {
      const result = await request("/api/report", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      reportHashes.set(dayKey, hash);
      patchSync({
        [`reportRevision:${dayKey}`]: result.revision,
        [`reportHash:${dayKey}`]: result.contentHash,
        [`reportPending:${dayKey}`]: false
      });
      return { photosIncluded: true, serverSaved: true, databaseSaved: true, ...result };
    } catch (error) {
      if (error.status === 409) return resolveReportConflict(report, error);
      patchSync({ [`reportPending:${dayKey}`]: true, [`reportError:${dayKey}`]: error.message });
      return { photosIncluded: true, serverSaved: false, databaseSaved: true, error: error.message };
    }
  }

  window.saveDirectReport = async function (data) {
    const report = typeof repairSavedText === "function" ? repairSavedText(clone(data)) : clone(data);
    const dayKey = report.dayKey || brToISO(report.header?.dataRelatorio) || currentDayKey;
    report.dayKey = dayKey;
    report.updatedAt = new Date().toISOString();
    await storeReportLocally(report, dayKey);
    patchSync({ [`reportPending:${dayKey}`]: true });

    const hash = await digest(reportForHash(report));
    const previous = reportQueues.get(dayKey);
    if (previous?.hash === hash) return previous.promise;
    const promise = (previous?.promise || Promise.resolve())
      .catch(() => null)
      .then(() => sendReport(report));
    reportQueues.set(dayKey, { hash, promise });
    return promise;
  };

  const previousGetReport = window.getReportByDay;
  if (typeof previousGetReport === "function") {
    window.getReportByDay = async function (dayKey) {
      const report = await previousGetReport(dayKey);
      if (report?.revision !== undefined) {
        patchSync({ [`reportRevision:${dayKey}`]: report.revision, [`reportHash:${dayKey}`]: report.contentHash || "" });
      }
      return report;
    };
  }

  async function imageToDataUrl(value) {
    const source = text(value);
    if (!source || source.startsWith("data:")) return source;
    if (!/^\/?images\//i.test(source)) return source;
    const response = await nativeFetch(source, { cache: "no-store" });
    if (!response.ok) throw new Error(`Imagem não encontrada: ${source}`);
    const blob = await response.blob();
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(blob);
    });
  }

  async function portable(report) {
    const copy = clone(report);
    for (const item of copy.equipment || []) {
      item.beforeImage = await imageToDataUrl(item.beforeImage);
      item.afterImage = await imageToDataUrl(item.afterImage);
    }
    return copy;
  }

  async function localReports() {
    const map = new Map();
    try { (await getAllFromDatabase()).forEach(report => report?.dayKey && map.set(report.dayKey, report)); }
    catch (_) {}
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (!key?.startsWith(STORAGE_PREFIX)) continue;
      try {
        const report = JSON.parse(localStorage.getItem(key));
        const dayKey = report.dayKey || key.slice(STORAGE_PREFIX.length);
        if (!map.has(dayKey)) map.set(dayKey, { ...report, dayKey });
      } catch (_) {}
    }
    const result = [];
    for (const report of map.values()) result.push(await portable(report));
    return result;
  }

  window.exportBackup = async function () {
    await saveReport(true);
    await Promise.all(Array.from(reportQueues.values(), item => item.promise.catch(() => null)));
    const choice = prompt("Exportar S = servidor oficial, L = recuperação local ou M = mesclado?", "S");
    const selected = text(choice).toUpperCase();
    if (!choice) return;
    try {
      if (selected === "L") {
        downloadJson(`backup-local-${todayISO()}.json`, { version: 4, source: "local", exportedAt: new Date().toISOString(), devices: localDevicesRaw(), reports: await localReports(), sync: readSync() });
        return;
      }
      const server = (await request("/api/export")).backup;
      if (selected === "M") {
        const merged = new Map((server.reports || []).map(report => [report.dayKey, report]));
        (await localReports()).forEach(report => {
          const old = merged.get(report.dayKey);
          if (!old || Date.parse(report.updatedAt || 0) >= Date.parse(old.updatedAt || 0)) merged.set(report.dayKey, report);
        });
        server.source = "merged";
        server.devices = mergeDevices(server.devices, localDevicesRaw());
        server.reports = Array.from(merged.values()).sort((a, b) => a.dayKey.localeCompare(b.dayKey));
      }
      downloadJson(`backup-${server.source || "servidor"}-${todayISO()}.json`, server);
    } catch (error) {
      alert(`Não foi possível exportar: ${error.message}`);
    }
  };

  function importMode(label) {
    const value = prompt(`${label}\nDigite M para MESCLAR, R para SUBSTITUIR local e servidor, L para SOMENTE LOCAL ou C para cancelar.`, "M");
    const choice = text(value).toUpperCase();
    if (!value || choice === "C") return null;
    return choice === "R" ? "replace" : choice === "L" ? "local" : "merge";
  }

  window.importDevices = async function (input) {
    const file = input.files?.[0];
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      const incoming = normalizeDevices(Array.isArray(parsed) ? parsed : parsed.devices);
      if (!incoming.length) throw new Error("Nenhum equipamento com IP foi encontrado.");
      const mode = importMode(`Arquivo: ${incoming.length} equipamentos.`);
      if (!mode) return;
      if (mode === "local") {
        saveLocalDevices(incoming);
        patchSync({ devicesPending: true });
      } else {
        const server = await request("/api/devices");
        if (mode === "replace") await serverBackupDownload("antes-importar-equipamentos");
        const finalDevices = mode === "merge" ? mergeDevices(server.devices, incoming) : incoming;
        await persistDevices(finalDevices, "replace", server.revision);
      }
      clearDeviceForm();
      renderSavedData();
      alert("Importação de equipamentos confirmada.");
    } catch (error) {
      alert(`Importação não concluída: ${error.message}`);
    } finally { input.value = ""; }
  };

  window.importBackup = async function (input) {
    const file = input.files?.[0];
    if (!file) return;
    try {
      const backup = JSON.parse(await file.text());
      if (!Array.isArray(backup.reports) || !Array.isArray(backup.devices)) throw new Error("Backup inválido.");
      const mode = importMode(`Backup: ${backup.devices.length} equipamentos e ${backup.reports.length} relatórios.`);
      if (!mode) return;

      for (const report of backup.reports) {
        if (!report?.dayKey) continue;
        await storeReportLocally(report, report.dayKey);
      }
      saveLocalDevices(mode === "merge" ? mergeDevices(localDevicesRaw(), backup.devices) : backup.devices);

      if (mode === "local") {
        patchSync({ devicesPending: true, backupPending: true });
        alert("Backup importado somente no navegador e marcado como pendente.");
      } else {
        const result = await request("/api/restore", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ backup, mode })
        });
        if (!result.ok || result.errors?.length) throw new Error(`${result.errors?.length || 0} relatório(s) falharam.`);
        patchSync({ backupPending: false, devicesPending: false });
        alert(`Backup confirmado: ${result.reportsImported} relatório(s).${result.snapshot ? ` Snapshot: ${result.snapshot}` : ""}`);
      }
      await loadDay(currentDayKey);
    } catch (error) {
      alert(`Backup não concluído: ${error.message}`);
    } finally { input.value = ""; }
  };

  setTimeout(() => reconcileDevices(), 0);
})();
