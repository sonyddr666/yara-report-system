(function () {
  const nativeFetch = window.__YARA_NATIVE_FETCH__ || window.fetch.bind(window);
  function text(value) { return String(value ?? "").trim(); }
  async function request(url, options = {}) {
    const response = await nativeFetch(url, { cache: "no-store", ...options });
    let result = {};
    try { result = await response.json(); } catch (_) {}
    if (!response.ok || result.ok === false) throw new Error(result.error || `Servidor retornou ${response.status}`);
    return result;
  }
  function download(name, value) {
    const blob = new Blob([JSON.stringify(value, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1200);
  }
  async function clearDays(days) {
    for (const day of days) {
      try { localStorage.removeItem(storageKey(day)); } catch (_) {}
      try { await deleteFromDatabase(day); } catch (_) {}
    }
  }
  window.exportBackup = async function () {
    await saveReport(true);
    try {
      const result = await request("/api/export");
      download(`backup-oficial-yara-${todayISO()}.json`, result.backup);
    } catch (error) {
      alert(`Não foi possível exportar o backup oficial: ${error.message}`);
    }
  };
  window.importBackup = async function (input) {
    const file = input.files?.[0];
    if (!file) return;
    try {
      const backup = JSON.parse(await file.text());
      if (!Array.isArray(backup.devices) || !Array.isArray(backup.reports)) throw new Error("Backup inválido.");
      const choice = prompt(`Backup: ${backup.devices.length} equipamentos e ${backup.reports.length} dias.\n\nDigite M para MESCLAR, S para SUBSTITUIR tudo ou C para cancelar.`, "M");
      const selected = text(choice).toUpperCase();
      if (!choice || selected === "C") return;
      const mode = selected === "S" ? "replace" : "merge";
      const result = await request("/api/restore", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ backup, mode })
      });
      const days = backup.reports.map(report => report?.dayKey).filter(Boolean);
      await clearDays(days);
      const state = await request("/api/devices");
      localStorage.setItem(DEVICE_STORE_KEY, JSON.stringify(state.devices || []));
      refreshDeviceSelectors();
      await loadDay(currentDayKey);
      alert(`Importação confirmada pelo servidor.\n${result.reportsImported ?? days.length} dias processados.\n${result.devicesSaved ?? backup.devices.length} equipamentos na base.\n${(result.warnings || []).length} aviso(s).`);
    } catch (error) {
      console.error(error);
      alert(`Importação cancelada sem sucesso parcial: ${error.message}`);
    } finally {
      input.value = "";
    }
  };
})();
