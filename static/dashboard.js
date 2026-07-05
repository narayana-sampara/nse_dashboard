const state = { data: null, signal: "buys", sector: "all" };
const grid = document.querySelector("#sector-grid");
const loading = document.querySelector("#loading");
const errorPanel = document.querySelector("#error");
const refreshButton = document.querySelector("#refresh-button");
const sectorFilter = document.querySelector("#sector-filter");

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  })[char]);
}

function stockRow(stock, index) {
  const direction = stock.change_pct >= 0 ? "up" : "down";
  const sign = stock.change_pct >= 0 ? "+" : "";
  const side = state.signal === "buys" ? "buy" : "sell";
  return `
    <article class="stock-row ${side}" title="${escapeHtml(stock.reasons.join(" · "))}">
      <span class="rank">${String(index + 1).padStart(2, "0")}</span>
      <div class="stock-name"><strong>${escapeHtml(stock.name)}</strong><span>${escapeHtml(stock.symbol)}</span></div>
      <div class="metric"><small>Price</small>₹${stock.price.toLocaleString("en-IN")}</div>
      <div class="metric change ${direction}"><small>Day</small>${sign}${stock.change_pct}%</div>
      <div class="metric confidence"><small>Score</small><span class="score">${stock.score > 0 ? "+" : ""}${stock.score}</span></div>
    </article>`;
}

function render() {
  if (!state.data) return;
  const sectors = state.data.sectors.filter((sector) => state.sector === "all" || sector.name === state.sector);
  grid.innerHTML = sectors.map((sector) => {
    const stocks = sector[state.signal];
    const label = state.signal === "buys" ? "buy" : "sell";
    return `
      <section class="sector-card">
        <header class="sector-head"><h2>${escapeHtml(sector.name)}</h2><span>${sector.scanned} stocks screened</span></header>
        ${stocks.length ? stocks.map(stockRow).join("") : `<div class="empty">No qualifying ${label} signal today</div>`}
      </section>`;
  }).join("");
}

function populateFilters(sectors) {
  sectorFilter.innerHTML = `<option value="all">All sectors</option>${sectors.map(
    (sector) => `<option value="${escapeHtml(sector.name)}">${escapeHtml(sector.name)}</option>`
  ).join("")}`;
}

async function loadDashboard(force = false) {
  loading.hidden = false;
  grid.hidden = true;
  errorPanel.hidden = true;
  refreshButton.disabled = true;
  try {
    const response = await fetch(`/api/dashboard${force ? "?refresh=1" : ""}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to load market signals");
    state.data = payload;
    populateFilters(payload.sectors);
    document.querySelector("#market-date").textContent = payload.market_date || "Unavailable";
    document.querySelector("#stocks-scored").textContent = `${payload.stocks_scored} / ${payload.universe_size}`;
    document.querySelector("#sector-count").textContent = payload.sectors.length;
    document.querySelector("#updated-at").textContent = `Updated ${new Date(payload.generated_at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}`;
    render();
    grid.hidden = false;
  } catch (error) {
    errorPanel.textContent = `${error.message}. Check the internet connection and try again.`;
    errorPanel.hidden = false;
  } finally {
    loading.hidden = true;
    refreshButton.disabled = false;
  }
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((item) => {
      item.classList.toggle("active", item === tab);
      item.setAttribute("aria-selected", item === tab ? "true" : "false");
    });
    state.signal = tab.dataset.signal;
    render();
  });
});

sectorFilter.addEventListener("change", () => { state.sector = sectorFilter.value; render(); });
refreshButton.addEventListener("click", () => loadDashboard(true));
loadDashboard();
