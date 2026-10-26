// Renders the cost comparison charts from JSON embedded in the page.
(function () {
  const dataEl = document.getElementById("chart-data");
  if (!dataEl || typeof Chart === "undefined") return;
  const data = JSON.parse(dataEl.textContent);

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
})();
