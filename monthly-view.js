window.showMonthlyReport = async function () {
  var tools = window.__YARA_MONTHLY__;
  await saveReport(true);
  var typed = prompt("Digite o mês no formato AAAA-MM:", currentDayKey.slice(0, 7));
  if (typed === null) return;
  var month = tools.cleanText(typed);
  if (!/^\d{4}-\d{2}$/.test(month)) return alert("Use o formato AAAA-MM.");
  try {
    var responses = await Promise.all([
      tools.request("/api/reports?month=" + encodeURIComponent(month)),
      tools.request("/api/coverage?month=" + encodeURIComponent(month))
    ]);
    var reportResult = responses[0];
    var coverage = responses[1];
    var reports = (reportResult.reports || []).filter(function (report) {
      return report.dayKey && report.dayKey.indexOf(month) === 0;
    });
    if (!reports.length) return alert("Nenhum relatório confirmado no servidor nesse mês.");
    reports.forEach(function (report) {
      (report.equipment || []).forEach(function (item) {
        if (!tools.validImage(item.beforeImage)) item.beforeImage = "";
        if (!tools.validImage(item.afterImage)) item.afterImage = "";
      });
    });
    var days = coverage.days == null ? reports.length : coverage.days;
    var occurrences = coverage.totalOccurrences == null ? 0 : coverage.totalOccurrences;
    var unique = coverage.uniqueAttended == null ? 0 : coverage.uniqueAttended;
    var registered = coverage.totalRegistered == null ? 0 : coverage.totalRegistered;
    var pending = coverage.pendingCount == null ? 0 : coverage.pendingCount;
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
