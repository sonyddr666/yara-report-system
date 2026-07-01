/*
 * Correções de lógica e persistência.
 * Este arquivo não altera CSS, estrutura visual, dimensões A4 ou regras de impressão.
 */
(function () {
  const originals = {};
  const remember = name => {
    if (typeof window[name] === "function") originals[name] = window[name];
  };

  [
    "equipmentTemplate",
    "addEquipment",
    "collectReportData",
    "saveDirectReport",
    "getReportByDay",
    "getAllReports",
    "saveDeviceForm",
    "importDevices",
    "exportDevices",
    "exportBackup",
    "importBackup",
    "removeImage",
    "loadImage",
    "showMonthlyReport"
  ].forEach(remember);

  function normalizeIp(value) {
    return String(value || "").trim().toLowerCase();
  }

  function newEntryId() {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
      return globalThis.crypto.randomUUID();
    }
    return `entry-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function escapeAttribute(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll('"', "&quot;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function chooseBetterReport(current, incoming) {
    if (!current) return incoming;
    if (!incoming) return current;
    const currentTime = Date.parse(current.updatedAt || "") || 0;
    const incomingTime = Date.parse(incoming.updatedAt || "") || 0;
    if (incomingTime !== currentTime) return incomingTime > currentTime ? incoming : current;
    const currentImages = typeof reportImageCount === "function" ? reportImageCount(current) : 0;
    const incomingImages = typeof reportImageCount === "function" ? reportImageCount(incoming) : 0;
    return incomingImages > currentImages ? incoming : current;
  }

  async function urlToDataUrl(value) {
    const url = String(value || "");
    if (!url || url.startsWith("data:")) return url;
    if (!/^\/?images\//i.test(url)) return url;
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`Imagem não encontrada: ${url}`);
    const blob = await response.blob();
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(reader.error || new Error("Falha ao converter imagem"));
      reader.readAsDataURL(blob);
    });
  }

  async function portableReport(report) {
    const copy = clone(report);
    for (const item of copy.equipment || []) {
      item.beforeImage = await urlToDataUrl(item.beforeImage);
      item.afterImage = await urlToDataUrl(item.afterImage);
    }
    return copy;
  }

  // A identidade da base é o IP. Nome e MAC podem se repetir sem apagar registros legítimos.
  window.deviceIdentityRefs = function (device = {}) {
    const ip = normalizeIp(device.ip);
    return ip ? [`ip:${ip}`] : [];
  };

  window.dedupeDevices = function (devices = []) {
    const result = [];
    const byIp = new Map();

    devices.forEach((raw, index) => {
      if (!raw || typeof raw !== "object") return;
      const device = typeof normalizeDevice === "function"
        ? normalizeDevice(typeof repairSavedText === "function" ? repairSavedText(raw) : raw, index)
        : { ...raw };
      const ip = normalizeIp(device.ip);
      if (!ip) return;

      if (byIp.has(ip)) {
        const current = result[byIp.get(ip)];
        Object.entries(device).forEach(([key, value]) => {
          if (!String(current[key] ?? "").trim() && String(value ?? "").trim()) current[key] = value;
        });
        return;
      }

      device.ip = String(device.ip || "").trim();
      device.number = device.number || result.length + 1;
      byIp.set(ip, result.length);
      result.push(device);
    });

    return result
      .map((device, index) => ({ ...device, number: device.number || index + 1 }))
      .sort((a, b) => Number(a.number || 0) - Number(b.number || 0));
  };

  window.getDevices = function () {
    try {
      const raw = localStorage.getItem(DEVICE_STORE_KEY);
      if (raw) {
        const saved = JSON.parse(raw);
        if (Array.isArray(saved)) return dedupeDevices(saved);
      }
    } catch (error) {
      console.warn("Cadastro local ignorado:", error);
    }
    return dedupeDevices(clone(typeof DEFAULT_DEVICE_DATA !== "undefined" ? DEFAULT_DEVICE_DATA : []));
  };

  window.saveDeviceData = function (devices) {
    const normalized = dedupeDevices(devices);
    localStorage.setItem(DEVICE_STORE_KEY, JSON.stringify(normalized));
    if (typeof apiSaveDevices === "function") {
      apiSaveDevices(normalized).catch(error => console.warn("Base de equipamentos não salva no servidor:", error));
    }
    if (typeof refreshDeviceSelectors === "function") refreshDeviceSelectors();
    return normalized;
  };

  window.findDeviceByIp = function (ip) {
    const wanted = normalizeIp(ip);
    return getDevices().find(device => normalizeIp(device.ip) === wanted) || null;
  };

  window.deviceUsageKeyFromParts = function ({ ip = "" } = {}) {
    const normalized = normalizeIp(ip);
    return normalized ? `ip:${normalized}` : "";
  };

  window.deviceUsageKey = function (device = {}) {
    return deviceUsageKeyFromParts({ ip: device.ip });
  };

  window.reportItemUsageKey = function (item = {}) {
    return deviceUsageKeyFromParts({ ip: item.ip });
  };

  // Cada linha diária recebe um ID próprio. Duplicar o mesmo IP continua permitido.
  if (originals.equipmentTemplate) {
    window.equipmentTemplate = function (data, index) {
      const item = { ...(data || {}) };
      item.entryId = item.entryId || newEntryId();
      const html = originals.equipmentTemplate(item, index);
      return html.replace(
        '<article class="equipment-card" data-equipment ',
        `<article class="equipment-card" data-equipment data-entry-id="${escapeAttribute(item.entryId)}" `
      );
    };
  }

  if (originals.addEquipment) {
    window.addEquipment = function (data = null) {
      let item = data;
      if (item && typeof isLoadingReport !== "undefined" && !isLoadingReport) {
        item = { ...item, entryId: newEntryId() };
      }
      return originals.addEquipment(item);
    };
  }

  if (originals.collectReportData) {
    window.collectReportData = function () {
      const data = originals.collectReportData();
      const cards = Array.from(document.querySelectorAll("#reportPage [data-equipment]"));
      data.equipment = (data.equipment || []).map((item, index) => {
        const card = cards[index];
        const entryId = card?.dataset.entryId || newEntryId();
        if (card) card.dataset.entryId = entryId;
        return {
          ...item,
          entryId,
          attendedType: item.equipmentType || "",
          beforeImageRemoved: card?.dataset.beforeImageRemoved === "true",
          afterImageRemoved: card?.dataset.afterImageRemoved === "true"
        };
      });
      return data;
    };
  }

  if (originals.removeImage) {
    window.removeImage = function (button, type) {
      const card = button?.closest?.("[data-equipment]");
      if (card) card.dataset[`${type}ImageRemoved`] = "true";
      return originals.removeImage(button, type);
    };
  }

  if (originals.loadImage) {
    window.loadImage = async function (input, type) {
      const card = input?.closest?.("[data-equipment]");
      if (card) delete card.dataset[`${type}ImageRemoved`];
      return originals.loadImage(input, type);
    };
  }

  // Servidor é a fonte principal; IndexedDB mantém a cópia offline com fotos.
  window.saveDirectReport = async function (data) {
    data = typeof repairSavedText === "function" ? repairSavedText(data) : data;
    const dayKey = data.dayKey || brToISO(data.header?.dataRelatorio) || currentDayKey;
    data.dayKey = dayKey;
    data.updatedAt = new Date().toISOString();

    let serverSaved = false;
    let databaseSaved = false;
    let photosIncluded = false;

    try {
      await apiSaveReport(data);
      serverSaved = true;
      photosIncluded = true;
    } catch (error) {
      console.warn("Servidor não salvou; usando armazenamento offline:", error);
    }

    try {
      await saveToDatabase(data, dayKey);
      databaseSaved = true;
      photosIncluded = true;
    } catch (error) {
      console.warn("IndexedDB não salvou:", error);
    }

    try {
      localStorage.setItem(storageKey(dayKey), JSON.stringify(withoutImages(data)));
    } catch (error) {
      console.warn("Resumo local não salvo:", error);
    }

    return { photosIncluded, serverSaved, databaseSaved };
  };

  window.getReportByDay = async function (dayKey) {
    let best = null;

    try {
      const server = await apiLoadReport(dayKey);
      best = chooseBetterReport(best, server && repairSavedText(server));
    } catch (error) {
      console.warn("Servidor indisponível para carregar relatório:", error);
    }

    try {
      const database = await loadFromDatabase(dayKey);
      best = chooseBetterReport(best, database && repairSavedText(database));
    } catch (error) {
      console.warn("Banco local indisponível:", error);
    }

    try {
      const raw = localStorage.getItem(storageKey(dayKey));
      if (raw) best = chooseBetterReport(best, repairSavedText(JSON.parse(raw)));
    } catch (error) {
      console.warn("Registro local corrompido:", error);
    }

    return best;
  };

  window.getAllReports = async function () {
    const reports = new Map();
    const add = report => {
      if (!report?.dayKey) return;
      reports.set(report.dayKey, chooseBetterReport(reports.get(report.dayKey), repairSavedText(report)));
    };

    try {
      (await apiLoadReports()).forEach(add);
    } catch (error) {
      console.warn("Servidor indisponível para listar relatórios:", error);
    }

    try {
      (await getAllFromDatabase()).forEach(add);
    } catch (error) {
      console.warn("IndexedDB indisponível para listar relatórios:", error);
    }

    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (!key?.startsWith(STORAGE_PREFIX)) continue;
      try {
        const report = JSON.parse(localStorage.getItem(key));
        add({ ...report, dayKey: report.dayKey || key.slice(STORAGE_PREFIX.length) });
      } catch (error) {
        console.warn("Registro local ignorado:", error);
      }
    }

    return Array.from(reports.values()).sort((a, b) => a.dayKey.localeCompare(b.dayKey));
  };

  // Cadastro principal aceita somente equipamentos com IP e bloqueia apenas IP repetido.
  window.saveDeviceForm = function (event) {
    event.preventDefault();
    const originalIp = document.getElementById("deviceOriginalIp").value.trim();
    const device = readDeviceForm();

    if (!device.name || !device.ip) {
      alert("Informe nome e IP. A lista principal mostra somente equipamentos que possuem IP.");
      return;
    }

    const devices = getDevices();
    const duplicated = devices.some(item => {
      if (originalIp && normalizeIp(item.ip) === normalizeIp(originalIp)) return false;
      return normalizeIp(item.ip) === normalizeIp(device.ip);
    });
    if (duplicated) {
      alert("Já existe um equipamento com esse IP.");
      return;
    }

    const existing = devices.find(item => normalizeIp(item.ip) === normalizeIp(originalIp));
    const maxNumber = devices.reduce((max, item) => Math.max(max, Number(item.number) || 0), 0);
    device.number = existing?.number || maxNumber + 1;
    const next = existing
      ? devices.map(item => normalizeIp(item.ip) === normalizeIp(originalIp) ? device : item)
      : [...devices, device];

    saveDeviceData(next);
    fillDeviceForm(device);
    renderSavedData();
  };

  window.exportDevices = function () {
    const backup = {
      version: 2,
      exportedAt: new Date().toISOString(),
      rule: "Somente equipamentos com IP; MD410 é tipo de atendimento, não cadastro próprio.",
      devices: getDevices()
    };
    const blob = new Blob([JSON.stringify(backup, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `dados-salvos-rig1-${todayISO()}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  };

  window.importDevices = async function (input) {
    const file = input.files?.[0];
    if (!file) return;
    try {
      const backup = JSON.parse(await file.text());
      const source = Array.isArray(backup) ? backup : backup.devices;
      if (!Array.isArray(source) || !source.length) throw new Error("Cadastro vazio ou inválido.");
      const devices = dedupeDevices(source);
      const ignored = source.length - devices.length;
      if (!devices.length) throw new Error("Nenhum registro com IP foi encontrado.");
      if (!confirm(`Importar ${devices.length} equipamento(s) com IP? ${ignored} registro(s) sem IP ou repetido(s) serão ignorados. O cadastro atual será substituído.`)) return;
      saveDeviceData(devices);
      clearDeviceForm();
      renderSavedData();
      alert(`Dados importados: ${devices.length} equipamento(s) com IP.`);
    } catch (error) {
      console.error(error);
      alert(`Não foi possível importar esse cadastro. ${error.message || ""}`.trim());
    } finally {
      input.value = "";
    }
  };

  window.exportBackup = async function () {
    await saveReport(true);
    try {
      const reports = [];
      for (const report of await getAllReports()) reports.push(await portableReport(report));
      const backup = {
        version: 3,
        exportedAt: new Date().toISOString(),
        rules: {
          deviceBase: "somente registros com IP",
          md410: "tipo atendido usando o mesmo registro e IP do MD400",
          monthlyCoverage: "IP único",
          dailyTotal: "quantidade de ocorrências"
        },
        devices: getDevices(),
        reports
      };
      const blob = new Blob([JSON.stringify(backup)], { type: "application/json" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `backup-relatorio-yara-${todayISO()}.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    } catch (error) {
      console.error(error);
      alert(`Não foi possível exportar o backup completo: ${error.message || error}`);
    }
  };

  window.importBackup = async function (input) {
    const file = input.files?.[0];
    if (!file) return;
    try {
      const backup = JSON.parse(await file.text());
      const reports = Array.isArray(backup) ? backup : backup.reports;
      if (!Array.isArray(reports) || !reports.length) throw new Error("Backup vazio ou inválido.");
      if (!confirm(`Importar ${reports.length} dia(s)? Dias com a mesma data serão substituídos.`)) return;

      if (!Array.isArray(backup) && Array.isArray(backup.devices)) saveDeviceData(backup.devices);

      let imported = 0;
      let ignored = 0;
      for (const report of reports) {
        if (!report?.dayKey || !report?.header || !Array.isArray(report.equipment)) {
          ignored += 1;
          continue;
        }
        await saveDirectReport(report);
        imported += 1;
      }
      alert(`Backup importado: ${imported} dia(s).${ignored ? ` ${ignored} inválido(s) ignorado(s).` : ""}`);
      await loadDay(currentDayKey);
    } catch (error) {
      console.error(error);
      alert(`Não foi possível importar esse backup. ${error.message || ""}`.trim());
    } finally {
      input.value = "";
    }
  };

  async function loadCoverage(monthKey, reports) {
    const company = document.getElementById("empresa")?.textContent.trim() || "";
    try {
      const response = await fetch(`/api/coverage?month=${encodeURIComponent(monthKey)}&company=${encodeURIComponent(company)}`, { cache: "no-store" });
      if (response.ok) {
        const result = await response.json();
        if (result.ok) return result;
      }
    } catch (error) {
      console.warn("Cobertura mensal do servidor indisponível:", error);
    }

    const devices = getDevices();
    const base = new Set(devices.map(device => normalizeIp(device.ip)).filter(Boolean));
    const occurrences = reports.flatMap(report => report.equipment || []);
    const used = new Set(occurrences.map(item => normalizeIp(item.ip)).filter(ip => base.has(ip)));
    return {
      totalRegistered: base.size,
      uniqueAttended: used.size,
      pendingCount: Math.max(0, base.size - used.size),
      totalOccurrences: occurrences.length
    };
  }

  if (originals.showMonthlyReport) {
    window.showMonthlyReport = async function () {
      await saveReport(true);
      const typedMonth = prompt("Digite o mês no formato AAAA-MM:", currentDayKey.slice(0, 7));
      if (typedMonth === null) return;
      const monthKey = typedMonth.trim();
      if (!/^\d{4}-\d{2}$/.test(monthKey)) {
        alert("Use o formato AAAA-MM.");
        return;
      }

      const reports = (await getAllReports()).filter(report => report.dayKey.startsWith(monthKey));
      if (!reports.length) {
        alert("Nenhum relatório salvo nesse mês.");
        return;
      }

      const coverage = await loadCoverage(monthKey, reports);
      document.getElementById("monthlyOutput").innerHTML = `
        <main class="report-page monthly-cover">
          <div class="report-inner">
            <div class="monthly-cover-box">
              <div class="monthly-cover-title">
                <h1>RELATÓRIO FOTOGRÁFICO MENSAL</h1>
                <strong>${escapeHtml(document.getElementById("empresa")?.textContent.trim() || "YARA")} — ${escapeHtml(monthName(monthKey).toUpperCase())}</strong>
              </div>
              <div class="monthly-stats">
                <div class="monthly-stat"><strong>${reports.length}</strong><span>Dias registrados</span></div>
                <div class="monthly-stat"><strong>${coverage.uniqueAttended}/${coverage.totalRegistered}</strong><span>Atendidos / base com IP</span></div>
                <div class="monthly-stat"><strong>${coverage.totalOccurrences}</strong><span>Itens (${coverage.pendingCount} pendentes)</span></div>
              </div>
            </div>
          </div>
        </main>
        ${reports.map(monthlyReportPagesTemplate).join("")}
      `;

      applyPrintVisibility();
      document.body.className = "monthly";
      window.scrollTo({ top: 0, behavior: "smooth" });
    };
  }

  async function normalizeInitialDeviceBase() {
    let devices = [];
    try {
      devices = await apiLoadDevices();
    } catch (error) {
      console.warn("Base do servidor indisponível durante a normalização:", error);
    }
    if (!Array.isArray(devices) || !devices.length) devices = getDevices();
    const normalized = dedupeDevices(devices);
    localStorage.setItem(DEVICE_STORE_KEY, JSON.stringify(normalized));
    try {
      await apiSaveDevices(normalized);
    } catch (error) {
      console.warn("Base filtrada não enviada ao servidor:", error);
    }
    refreshDeviceSelectors();
    await refreshMonthlyUsedDevices();
    if (document.getElementById("savedDataPanel")?.classList.contains("open")) renderSavedData();
  }

  // O script original já inicializou a página. Aqui apenas corrigimos os dados e seletores.
  normalizeInitialDeviceBase().catch(error => console.error("Falha ao aplicar correções de lógica:", error));
})();
