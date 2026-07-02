/* Correções finais de persistência. O HTML, CSS e impressão não são alterados. */
(function () {
  const nativeFetch = window.__YARA_NATIVE_FETCH__ || window.fetch.bind(window);
  const imagePlaceholders = new Set(["[image_removed]", "[imagem_removida]", "[removed]", "null", "none", "undefined"]);

  function cleanText(value) {
    return String(value == null ? "" : value).trim();
  }

  function validImage(value) {
    const raw = cleanText(value);
    if (!raw || imagePlaceholders.has(raw.toLowerCase())) return false;
    return raw.indexOf("data:image/") === 0 || /^\/?images\//i.test(raw);
  }

  async function request(url, options) {
    const response = await nativeFetch(url, Object.assign({ cache: "no-store" }, options || {}));
    let result = {};
    try { result = await response.json(); } catch (_) {}
    if (!response.ok || result.ok === false) {
      throw new Error(result.error || ("Servidor retornou " + response.status));
    }
    return result;
  }

  function download(name, value) {
    const blob = new Blob([JSON.stringify(value, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = name;
    link.click();
    setTimeout(function () { URL.revokeObjectURL(link.href); }, 1200);
  }

  async function clearImportedDays(days) {
    for (const day of days) {
      try { localStorage.removeItem(storageKey(day)); } catch (_) {}
      try { await deleteFromDatabase(day); } catch (_) {}
    }
  }

  window.exportBackup = async function () {
    await saveReport(true);
    try {
      const result = await request("/api/export");
      download("backup-oficial-yara-" + todayISO() + ".json", result.backup);
    } catch (error) {
      alert("Não foi possível exportar o backup oficial: " + error.message);
    }
  };

  window.importBackup = async function (input) {
    const file = input.files && input.files[0];
    if (!file) return;
    try {
      const backup = JSON.parse(await file.text());
      if (!Array.isArray(backup.devices) || !Array.isArray(backup.reports)) {
        throw new Error("Backup inválido.");
      }
      const choice = prompt(
        "Backup: " + backup.devices.length + " equipamentos e " + backup.reports.length +
        " dias.\n\nDigite M para MESCLAR, S para SUBSTITUIR tudo ou C para cancelar.",
        "M"
      );
      const selected = cleanText(choice).toUpperCase();
      if (!choice || selected === "C") return;
      const mode = selected === "S" ? "replace" : "merge";
      const result = await request("/api/restore", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ backup: backup, mode: mode })
      });
      const days = backup.reports.map(function (report) { return report && report.dayKey; }).filter(Boolean);
      await clearImportedDays(days);
      const state = await request("/api/devices");
      localStorage.setItem(DEVICE_STORE_KEY, JSON.stringify(state.devices || []));
      refreshDeviceSelectors();
      await loadDay(currentDayKey);
      alert(
        "Importação confirmada pelo servidor.\n" +
        (result.reportsImported == null ? days.length : result.reportsImported) + " dias processados.\n" +
        (result.devicesSaved == null ? backup.devices.length : result.devicesSaved) + " equipamentos na base.\n" +
        ((result.warnings || []).length) + " aviso(s)."
      );
    } catch (error) {
      console.error(error);
      alert("Importação cancelada sem sucesso parcial: " + error.message);
    } finally {
      input.value = "";
    }
  };

  window.showMonthlyReport = async function () {
    await saveReport(true);
    const typed = prompt("Digite o mês no formato AAAA-MM:", currentDayKey.slice(0, 7));
    if (typed === null) return;
    const month = cleanText(typed);
    if (!/^\d{4}-\d{2}$/.test(month)) {
      alert("Use o formato AAAA-MM.");
      return;
    }
    try {
      const values = await Promise.all([
        request("/api/reports?month=" + encodeURIComponent(month)),
        request("/api/coverage?month=" + encodeURIComponent(month))
      ]);
      const reportResult = values[0];
      const coverage = values[1];
      const reports = (reportResult.reports || []).filter(function (report) {
        return report.dayKey && report.dayKey.indexOf(month) === 0;
      });
      if (!reports.length) {
        alert("Nenhum relatório confirmado no servidor nesse mês.");
        return;
      }
      reports.forEach(function (report) {
        (report.equipment || []).forEach(function (item) {
          if (!validImage(item.beforeImage)) item.beforeImage = "";
          if (!validImage(item.afterImage)) item.afterImage = "";
        });
      });
      const days = coverage.days == null ? reports.length : coverage.days;
      const occurrences = coverage.totalOccurrences == null ? 0 : coverage.totalOccurrences;
      const unique = coverage.uniqueAttended == null ? 0 : coverage.uniqueAttended;
      const registered = coverage.totalRegistered == null ? 0 : coverage.totalRegistered;
      const pending = coverage.pendingCount == null ? 0 : coverage.pendingCount;
      document.getElementById("monthlyOutput").innerHTML = `
        <main class="report-page monthly-cover"><div class="report-inner"><div class="monthly-cover-box">
          <div class="monthly-cover-title"><h1>RELATÓRIO FOTOGRÁFICO MENSAL</h1><strong>${escapeHtml(document.getElementById("empresa")?.textContent.trim() || "YARA")} — ${escapeHtml(monthName(month).toUpperCase())}</strong></div>
          <div class="monthly-stats">
            <div class="monthly-stat"><strong>${days}</strong><span>Dias registrados</span></div>
            <div class="monthly-stat"><strong>${occurrences}</strong><span>Atendimentos</span></div>
            <div class="monthly-stat"><strong>${unique}/${registered}</strong><span>IPs atendidos (${pending} pendentes)</span></div>
          </div>
        </div></div></main>${reports.map(monthlyReportPagesTemplate).join("")}`;
      applyPrintVisibility();
      document.body.className = "monthly";
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (error) {
      alert("Não foi possível gerar o mês oficial: " + error.message);
    }
  };
})();
