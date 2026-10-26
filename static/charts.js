// Renders the cost comparison charts from JSON embedded in the page.
(function () {
  const dataEl = document.getElementById("chart-data");
  if (!dataEl || typeof Chart === "undefined") return;
  const data = JSON.parse(dataEl.textContent);

  // Box heights come from data-height (inline style attributes are blocked by the CSP).
  document.querySelectorAll(".chart-box[data-height]").forEach((el) => {
    el.style.height = parseInt(el.dataset.height, 10) + "px";
  });

  const COLORS = {
    estimated: "#93a4c3",
    actual: "#2457d6",
    over: "#d14343",
    under: "#2e9d57",
    on: "#9aa3b2",
    pie: ["#2457d6", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6", "#06b6d4", "#ec4899", "#84cc16", "#64748b", "#f97316"],
  };
  const money = (v) => Number(v).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  const short = (v) => {
    const a = Math.abs(v);
    if (a >= 1e6) return (v / 1e6).toFixed(1) + "M";
    if (a >= 1e3) return (v / 1e3).toFixed(0) + "K";
    return v;
  };
  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
  Chart.defaults.color = "#4b5563";
  Chart.defaults.maintainAspectRatio = false;

  // 1) Estimated vs Actual by category (grouped bars)
  new Chart(document.getElementById("chart-category"), {
    type: "bar",
    data: {
      labels: data.categories.map((c) => c.category),
      datasets: [
        { label: "Estimated", data: data.categories.map((c) => c.estimated), backgroundColor: COLORS.estimated, borderRadius: 4 },
        { label: "Actual", data: data.categories.map((c) => c.actual), backgroundColor: COLORS.actual, borderRadius: 4 },
      ],
    },
    options: {
      plugins: {
        legend: { position: "top", align: "end" },
        tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${money(ctx.raw)}` } },
      },
      scales: { y: { beginAtZero: true, ticks: { callback: short } }, x: { grid: { display: false } } },
    },
  });

  // 2) Variance per item (horizontal bars, red = over, green = under)
  const items = [...data.items].sort((a, b) => b.variance - a.variance);
  new Chart(document.getElementById("chart-variance"), {
    type: "bar",
    data: {
      labels: items.map((i) => i.item),
      datasets: [{
        label: "Variance (Actual − Estimated)",
        data: items.map((i) => i.variance),
        backgroundColor: items.map((i) => COLORS[i.status_key]),
        borderRadius: 4,
      }],
    },
    options: {
      indexAxis: "y",
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const it = items[ctx.dataIndex];
              const pct = it.pct_variance === null ? "n/a" : (it.pct_variance > 0 ? "+" : "") + it.pct_variance + "%";
              return `${ctx.raw > 0 ? "+" : ""}${money(ctx.raw)} (${pct}) · ${it.status}`;
            },
          },
        },
      },
      scales: { x: { ticks: { callback: short }, grid: { color: (c) => (c.tick.value === 0 ? "#6b7280" : "#e5e7eb") } }, y: { grid: { display: false } } },
    },
  });

  // 3) Share of actual spend by category (doughnut)
  new Chart(document.getElementById("chart-share"), {
    type: "doughnut",
    data: {
      labels: data.categories.map((c) => c.category),
      datasets: [{ data: data.categories.map((c) => c.actual), backgroundColor: COLORS.pie, borderWidth: 2, borderColor: "#fff" }],
    },
    options: {
      cutout: "58%",
      plugins: {
        legend: { position: "right" },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const total = ctx.dataset.data.reduce((s, v) => s + v, 0) || 1;
              return `${ctx.label}: ${money(ctx.raw)} (${((ctx.raw / total) * 100).toFixed(1)}%)`;
            },
          },
        },
      },
    },
  });
  // ---------------------------------------------------------------- PERT
  const pert = data.pert || {};
  const pdf = (x, mu, sd) => Math.exp(-0.5 * ((x - mu) / sd) ** 2) / (sd * Math.sqrt(2 * Math.PI));
  function curve(mu, sd) {
    const pts = [];
    for (let i = 0; i <= 80; i++) { const x = mu - 4 * sd + (8 * sd * i) / 80; pts.push({ x, y: pdf(x, mu, sd) }); }
    return pts;
  }
  function markers(defs, mu, sd) {
    const top = pdf(mu, mu, sd) * 1.05;
    return defs.filter((d) => d.x !== null && d.x !== undefined).map((d) => ({
      type: "line", label: d.label, data: [{ x: d.x, y: 0 }, { x: d.x, y: top }],
      borderColor: d.color, borderWidth: 2, borderDash: d.dash || [], pointRadius: 0, fill: false,
    }));
  }
  function distributionChart(id, mu, sd, defs, unit) {
    const el = document.getElementById(id);
    if (!el || !(sd > 0)) return;
    new Chart(el, {
      type: "line",
      data: { datasets: [
        { label: "Probability", data: curve(mu, sd), borderColor: COLORS.actual, backgroundColor: "rgba(36,87,214,0.10)", fill: true, pointRadius: 0, tension: 0.3 },
        ...markers(defs, mu, sd),
      ] },
      options: {
        parsing: false,
        plugins: { legend: { position: "top", align: "end", labels: { filter: (i) => i.datasetIndex > 0, boxHeight: 2 } },
                   tooltip: { enabled: false } },
        scales: { x: { type: "linear", ticks: { callback: (v) => (unit === "days" ? v.toFixed(0) + "d" : Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(2) + "M" : short(v)) } },
                  y: { display: false, beginAtZero: true } },
      },
    });
  }
  if (pert.cost) {
    const c = pert.cost;
    distributionChart("chart-pert-cost", c.expected, c.sd, [
      { x: c.expected, label: "Expected", color: COLORS.actual, dash: [4, 4] },
      { x: c.budget, label: "Budget", color: COLORS.under },
      { x: c.actual, label: "Actual", color: COLORS.over },
    ], "money");
  }
  if (pert.schedule) {
    const s = pert.schedule;
    distributionChart("chart-pert-time", s.duration, s.sd, [
      { x: s.duration, label: "Expected", color: COLORS.actual, dash: [4, 4] },
      { x: s.target, label: "Target", color: COLORS.over },
    ], "days");
    const acts = s.activities;
    const gantt = document.getElementById("chart-gantt");
    if (gantt) new Chart(gantt, {
      type: "bar",
      data: {
        labels: acts.map((a) => `${a.code}  ${a.item.length > 28 ? a.item.slice(0, 27) + "…" : a.item}`),
        datasets: [{ label: "Scheduled (ES → EF)", data: acts.map((a) => [a.es, a.ef]),
                     backgroundColor: acts.map((a) => (a.critical ? COLORS.over : COLORS.estimated)), borderRadius: 3, barPercentage: 0.7 }],
      },
      options: {
        indexAxis: "y",
        plugins: { legend: { display: false },
                   tooltip: { callbacks: { label: (ctx) => { const a = acts[ctx.dataIndex];
                     return `Day ${a.es.toFixed(1)} → ${a.ef.toFixed(1)} · slack ${a.slack.toFixed(1)}${a.critical ? " · critical" : ""}`; } } } },
        scales: { x: { min: 0, title: { display: true, text: "Days from start" } }, y: { grid: { display: false }, ticks: { font: { size: 11 } } } },
      },
    });
  }
})();
