// Deal Sniper — interface locale. Annonces : /api/ads ; réglages : /api/settings.

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const euros = (v) => (v == null ? "?" : `${Number(v).toLocaleString("fr-FR", { maximumFractionDigits: 0 })} €`);
const FILTER_IDS = ["f-text", "f-category", "f-type", "f-score", "f-ease", "f-price", "f-margin", "f-days", "f-sort"];
const SEARCH_FIELDS = { ebay: ["query", "condition", "max_price"], default: ["query", "category", "max_price"] };
const FIELD_LABELS = { query: "Recherche", condition: "État", max_price: "Prix max (€)", category: "Catégorie (n°)" };
const CONDITIONS = { any: "Tous états", parts: "Pour pièces / HS", used: "Occasion" };

// Logos des sites : favicon officiel du site, avec repli sur l'initiale.
const SITE_DOMAINS = { ebay: "www.ebay.fr", leboncoin: "www.leboncoin.fr", vinted: "www.vinted.fr", facebook: "www.facebook.com" };
const SITE_NAMES = { ebay: "eBay", leboncoin: "leboncoin", vinted: "Vinted", facebook: "Facebook" };
const siteName = (site) => SITE_NAMES[site] || site;
function siteLogo(site, size = 16) {
  const initial = esc((siteName(site) || "?")[0].toUpperCase());
  const fallback = `<span class="logo-fallback" style="width:${size}px;height:${size}px">${initial}</span>`;
  const domain = SITE_DOMAINS[site];
  if (!domain) return fallback;
  // 1er échec : service d'icônes de Google ; 2e échec : initiale du site.
  return `<img class="logo" src="https://${domain}/favicon.ico" width="${size}" height="${size}" alt="" referrerpolicy="no-referrer"
    onerror="if(!this.dataset.retry){this.dataset.retry=1;this.src='https://www.google.com/s2/favicons?domain=${domain}&sz=64'}else{this.outerHTML='${fallback.replace(/"/g, "&quot;")}'}">`;
}

let allAds = [];
let status = "analyzed";
let selectedSites = new Set();  // vide = tous les sites
let settings = null;

function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), 3500);
}

async function api(url, options = {}) {
  const resp = await fetch(url, { headers: { "Content-Type": "application/json" }, ...options });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || data.message || `HTTP ${resp.status}`);
  return data;
}

// ---------- Onglets ----------
// Tous les onglets sauf « Annonces » modifient config.yaml et partagent le bouton Enregistrer.
function showTab(tab) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".page").forEach((p) => (p.hidden = p.id !== `tab-${tab}`));
  $("#savebar").hidden = tab === "ads";
  if (tab !== "ads" && !settings) loadSettings();
  try { localStorage.setItem("dealsniper-tab", tab); } catch (e) { /* stockage indisponible */ }
}
document.querySelectorAll(".tab").forEach((btn) => btn.addEventListener("click", () => showTab(btn.dataset.tab)));

// ---------- Thème clair / sombre ----------
function setTheme(theme) {
  if (theme) document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
  document.querySelectorAll("#theme-choice button").forEach((b) => b.classList.toggle("on", b.dataset.value === theme));
  try { theme ? localStorage.setItem("dealsniper-theme", theme) : localStorage.removeItem("dealsniper-theme"); } catch (e) { /* idem */ }
}
const isDark = () => (document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark";
$("#theme").addEventListener("click", () => setTheme(isDark() ? "light" : "dark"));
document.querySelectorAll("#theme-choice button").forEach((b) => b.addEventListener("click", () => setTheme(b.dataset.value)));
setTheme(document.documentElement.dataset.theme || "");

// ---------- Filtres (mémorisés dans le navigateur) ----------
function saveFilters() {
  try {
    const values = Object.fromEntries(FILTER_IDS.map((id) => [id, $("#" + id).value]));
    localStorage.setItem("dealsniper-filters", JSON.stringify({ ...values, status, sites: [...selectedSites] }));
  } catch (e) { /* stockage indisponible */ }
}

function restoreFilters() {
  try {
    const values = JSON.parse(localStorage.getItem("dealsniper-filters") || "{}");
    FILTER_IDS.forEach((id) => { if (values[id] != null) $("#" + id).value = values[id]; });
    if (values.status != null) setStatus(values.status, false);
    selectedSites = new Set(values.sites || []);
  } catch (e) { /* idem */ }
}

function setStatus(value, reload = true) {
  status = value;
  document.querySelectorAll("#f-status button").forEach((b) => b.classList.toggle("on", b.dataset.value === value));
  if (reload) { saveFilters(); loadAds(); }
}

function fillSelect(id, values) {
  const el = $(id);
  const current = el.value;
  el.innerHTML = el.options[0].outerHTML + [...values].sort().map((v) => `<option>${esc(v)}</option>`).join("");
  if (values.has(current)) el.value = current;
}

// ---------- Budget IA et sites ----------
const dollars = (v) => `${Number(v || 0).toLocaleString("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} $`;

function renderBudget(spent, cap) {
  const ratio = cap ? Math.min(1, spent / cap) : 0;
  $("#budget-text").textContent = cap ? `IA : ${dollars(spent)} / ${dollars(cap)}` : `IA : ${dollars(spent)} (sans limite)`;
  $("#budget-fill").style.width = `${Math.round(ratio * 100)}%`;
  $("#budget-fill").className = ratio >= 1 ? "full" : ratio >= 0.8 ? "warn" : "";
  $("#budget-today").textContent = `Dépensé aujourd'hui : ${dollars(spent)}${cap ? ` sur ${dollars(cap)}` : ""}`;
}

function renderSiteChips(sites) {
  const chip = (value, label, logo, on) =>
    `<button class="chip ${on ? "on" : ""}" data-site="${esc(value)}">${logo}${esc(label)}</button>`;
  $("#f-sites").innerHTML = chip("", "Tous les sites", "", selectedSites.size === 0)
    + [...sites].sort().map((site) => chip(site, siteName(site), siteLogo(site), selectedSites.has(site))).join("");
}

$("#f-sites").addEventListener("click", (e) => {
  const button = e.target.closest(".chip");
  if (!button) return;
  const site = button.dataset.site;
  if (!site) selectedSites.clear();
  else if (selectedSites.has(site)) selectedSites.delete(site);
  else selectedSites.add(site);
  saveFilters();
  renderSiteChips(new Set(allAds.map((a) => a.source).filter(Boolean)));
  renderAds();
});

// ---------- Progression d'un passage ----------
const SPINNER = "|/-\\";
let progressLines = [], spin = 0;
function renderProgress(lines) {
  progressLines = lines;
  const box = $("#progress");
  box.hidden = !lines.length;
  box.innerHTML = lines.map((l) =>
    `<div class="prog-line"><b>${esc(l.site)}</b> : ${esc(l.text)} <span class="spin">${l.done ? "✓" : SPINNER[spin]}</span></div>`).join("");
}
setInterval(() => {  // « buffering » : le caractère tourne entre deux rafraîchissements
  if (!progressLines.length) return;
  spin = (spin + 1) % SPINNER.length;
  renderProgress(progressLines);
}, 150);

// ---------- Annonces ----------
async function loadAds() {
  try {
    const data = await api(`/api/ads?status=${encodeURIComponent(status)}&days=${$("#f-days").value}`);
    allAds = data.ads;
    renderBudget(data.cost_today, data.cost_cap);
    $("#run").disabled = data.running;
    $("#run").textContent = data.running ? "⏳ Passage en cours…" : "▶ Lancer un passage";
    if (data.error) toast("Dernier passage en erreur : " + data.error);
    renderProgress(data.running ? data.progress || [] : []);
    renderSiteChips(new Set(allAds.map((a) => a.source).filter(Boolean)));
    fillSelect("#f-category", new Set(allAds.map((a) => a.category).filter(Boolean)));
    renderAds();
  } catch (e) {
    $("#ads").innerHTML = `<div class="empty">Impossible de charger les annonces : ${esc(e.message)}</div>`;
  }
}

function filteredAds() {
  const text = $("#f-text").value.trim().toLowerCase();
  const category = $("#f-category").value, type = $("#f-type").value;
  const minScore = Number($("#f-score").value || 0);
  const minEase = Number($("#f-ease").value || 0);
  const maxPrice = $("#f-price").value === "" ? null : Number($("#f-price").value);
  const minMargin = $("#f-margin").value === "" ? null : Number($("#f-margin").value);

  const ads = allAds.filter((ad) => {
    const a = ad.analysis || {};
    if (text && !`${ad.title} ${ad.description} ${ad.location} ${ad.category}`.toLowerCase().includes(text)) return false;
    if (selectedSites.size && !selectedSites.has(ad.source)) return false;
    if (category && ad.category !== category) return false;
    if (type && a.type_affaire !== type) return false;
    if (minScore && (ad.score ?? 0) < minScore) return false;
    if (minEase && (a.facilite_reparation ?? 0) < minEase) return false;
    if (maxPrice != null && (ad.total_price ?? Infinity) > maxPrice) return false;
    if (minMargin != null && (a.marge_nette_estimee ?? -Infinity) < minMargin) return false;
    return true;
  });

  const margin = (ad) => (ad.analysis || {}).marge_nette_estimee ?? -1e9;
  const sorters = {
    recent: (x, y) => (y.seen_at || "").localeCompare(x.seen_at || ""),
    score: (x, y) => (y.score ?? -1) - (x.score ?? -1),
    margin: (x, y) => margin(y) - margin(x),
    price: (x, y) => (x.total_price ?? 1e9) - (y.total_price ?? 1e9),
  };
  return ads.sort(sorters[$("#f-sort").value] || sorters.recent);
}

function easeBadge(a) {
  const ease = a.facilite_reparation;
  if (ease == null || a.type_affaire === "revente_directe") return "";
  const level = ease >= 7 ? "pos" : ease >= 4 ? "mid" : "neg";
  return ` <span class="ease ${level}" title="Facilité de réparation">facilité ${ease}/10</span>`;
}

// « Laon 02000 02000 » → « Laon 02000 »
const cleanPlace = (place) => String(place || "").split(/\s+/).filter((w, i, all) => all.indexOf(w) === i).join(" ");

function adCard(ad) {
  const a = ad.analysis || {};
  const scoreClass = ad.score >= 7 ? "s-good" : ad.score >= 5 ? "s-mid" : "s-low";
  const score = ad.score != null ? `<span class="score ${scoreClass}">${ad.score}</span>` : "";
  const image = ad.image
    ? `<img src="${esc(ad.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'placeholder',textContent:'📦'}))">`
    : `<span class="placeholder">📦</span>`;
  const buy = euros(ad.total_price ?? ad.price);
  const marginValue = a.marge_nette_estimee;
  const margin = marginValue == null ? "—" : `${marginValue >= 0 ? "+" : ""}${euros(marginValue)}`;
  const marginClass = marginValue == null ? "" : marginValue >= 0 ? "pos" : "neg";
  const fault = !a.panne_probable ? "" : a.panne_probable.toLowerCase() === "aucune" ? "Aucune — revente en l'état" : a.panne_probable;
  const saleType = ad.price_mode === "enchere" ? "Enchère" : "Prix fixe";
  const place = cleanPlace(ad.location);

  return `<article class="ad ${ad.status}">
    <a class="thumb" href="${esc(ad.url)}" target="_blank" rel="noopener">${image}${score}</a>
    <div class="body">
      <a class="title" href="${esc(ad.url)}" target="_blank" rel="noopener">${esc(ad.title)}</a>
      <div class="stats">
        <div><span>Achat</span><b>${buy}</b></div>
        <div><span>Revente</span><b>${a.valeur_revente_estimee != null ? euros(a.valeur_revente_estimee) : "—"}</b></div>
        <div class="${marginClass}"><span>Marge</span><b>${margin}</b></div>
      </div>
      ${fault ? `<div class="fault">🔧 ${esc(fault)}${easeBadge(a)}</div>` : ""}
      ${ad.reason ? `<div class="reason">✗ ${esc(ad.reason)}</div>` : ""}
      <div class="meta">
        <div>${place ? `📍 ${esc(place)} · ` : ""}${saleType}</div>
        <div class="site-line">${siteLogo(ad.source, 14)} ${esc(siteName(ad.source))}${ad.category ? ` · ${esc(ad.category)}` : ""}</div>
      </div>
    </div>
  </article>`;
}

function renderAds() {
  const ads = filteredAds();
  $("#summary").textContent = `${ads.length} annonce(s) sur ${allAds.length}`;
  $("#ads").innerHTML = ads.length
    ? ads.slice(0, 500).map(adCard).join("")
    : '<div class="empty">Aucune annonce ne correspond à ces filtres.</div>';
}

FILTER_IDS.forEach((id) =>
  $("#" + id).addEventListener(id === "f-text" ? "input" : "change", () => {
    saveFilters();
    if (id === "f-days") loadAds(); else renderAds();
  })
);
document.querySelectorAll("#f-status button").forEach((b) => b.addEventListener("click", () => setStatus(b.dataset.value)));
$("#reset-filters").addEventListener("click", () => {
  FILTER_IDS.forEach((id) => { const el = $("#" + id); el.value = el.tagName === "SELECT" ? el.options[0].value : ""; });
  $("#f-days").value = "7";
  selectedSites.clear();
  setStatus("analyzed");
});

$("#run").addEventListener("click", async () => {
  try {
    await api("/api/run", { method: "POST" });
    toast("Passage lancé…");
    $("#run").disabled = true;
    const poll = setInterval(async () => {
      await loadAds();
      if (!$("#run").disabled) { clearInterval(poll); toast("Passage terminé"); }
    }, 1500);
  } catch (e) { toast(e.message); }
});

$("#test-notif").addEventListener("click", async () => {
  const data = await api("/api/test-notif", { method: "POST" }).catch(() => ({ ok: false }));
  toast(data.ok ? "Notification envoyée ✅" : "Échec : vérifiez TELEGRAM_* / DISCORD_* dans .env ❌");
});

// ---------- Réglages ----------

const words = (list) => (list || []).join(", ");

function searchRow(siteName, row) {
  const fields = SEARCH_FIELDS[siteName] || SEARCH_FIELDS.default;
  const cells = fields.map((f) => {
    if (f === "condition") {
      const opts = Object.entries(CONDITIONS)
        .map(([value, label]) => `<option value="${value}" ${(row[f] || "any") === value ? "selected" : ""}>${label}</option>`).join("");
      return `<td><select data-field="condition">${opts}</select></td>`;
    }
    const type = f === "max_price" ? "number" : "text";
    return `<td><input data-field="${f}" type="${type}" value="${esc(row[f] ?? "")}" placeholder="${FIELD_LABELS[f] || f}"></td>`;
  });
  return `<tr>${cells.join("")}<td><button class="danger" data-remove title="Supprimer">✕</button></td></tr>`;
}

function renderSites() {
  $("#sites").innerHTML = Object.entries(settings.sites).map(([name, site]) => {
    const fields = SEARCH_FIELDS[name] || SEARCH_FIELDS.default;
    const minutes = (site.interval || ["", ""]).map((v) => (v === "" ? "" : Math.round(v / 60)));
    return `<div class="card" data-site="${esc(name)}">
      <div class="card-head">
        <strong class="site-title">${siteLogo(name, 20)} ${esc(siteName(name))}</strong>
        <label class="field"><input type="checkbox" data-enabled ${site.enabled !== false ? "checked" : ""}> activé</label>
        <label class="field">En surveillance continue, toutes les <input type="number" min="1" data-interval="0" value="${esc(minutes[0])}"> à
          <input type="number" min="1" data-interval="1" value="${esc(minutes[1])}"> minutes</label>
      </div>
      <table><thead><tr>${fields.map((f) => `<th>${FIELD_LABELS[f] || f}</th>`).join("")}<th></th></tr></thead>
        <tbody>${(site.searches || []).map((r) => searchRow(name, r)).join("")}</tbody></table>
      <button class="ghost" data-add-search>+ Ajouter une recherche</button>
    </div>`;
  }).join("");
}

function categoryCard(cat) {
  return `<div class="card category">
    <div class="cat-head">
      <input data-field="name" value="${esc(cat.name)}" placeholder="Nom de la catégorie">
      <input data-field="max_budget" type="number" value="${esc(cat.max_budget ?? "")}" placeholder="Budget €">
    </div>
    <label class="muted small">Mots-clés<textarea data-field="keywords" rows="2">${esc(words(cat.keywords))}</textarea></label>
    <label class="muted small">Exclusions<textarea data-field="exclude_keywords" rows="1">${esc(words(cat.exclude_keywords))}</textarea></label>
    <label class="muted small">Signaux positifs<textarea data-field="include_keywords" rows="1">${esc(words(cat.include_keywords))}</textarea></label>
    <div class="row">
      <label class="field"><input type="checkbox" data-field="require_include" ${cat.require_include ? "checked" : ""}> signal obligatoire</label>
      <button class="danger" data-remove-category>Supprimer</button>
    </div>
  </div>`;
}

async function loadSettings() {
  $("#settings-error").hidden = true;
  try {
    settings = await api("/api/settings");
  } catch (e) {
    $("#settings-error").textContent = e.message;
    $("#settings-error").hidden = false;
    return;
  }
  renderSites();
  $("#categories").innerHTML = settings.categories.map(categoryCard).join("");
  const f = settings.filters, n = settings.notifier, a = settings.analyzer, l = settings.location;
  $("#g-exclude").value = words(f.global_exclude_keywords);
  $("#g-signals").value = words(f.deal_signals);
  $("#g-buyer").value = words(f.buyer_ad_keywords);
  $("#g-minprice").value = f.min_price ?? "";
  $("#g-france").checked = l.france_only !== false;
  $("#g-overseas").checked = l.exclude_overseas !== false;
  $("#g-uncat").checked = f.drop_uncategorized !== false;
  $("#g-noprice").checked = f.keep_unknown_price !== false;
  $("#n-margin").value = n.min_margin ?? "";
  $("#n-ease").value = n.min_ease ?? "";
  $("#a-batch").value = a.batch_size ?? 20;
  $("#a-desc").value = a.max_description_chars ?? 400;
  $("#s-retention").value = (settings.storage || {}).seen_retention_days ?? 90;
  $("#s-pause").value = (settings.network || {}).access_denied_pause_hours ?? 6;
  $("#n-min").value = n.min_score ?? 7;
  $("#n-watch").value = n.watch_score ?? "";
  const types = n.deal_types || ["reparation", "revente_directe"];
  $("#n-repair").checked = types.includes("reparation");
  $("#n-direct").checked = types.includes("revente_directe");
  if (a.model && ![...$("#a-model").options].some((o) => o.value === a.model)) {
    $("#a-model").insertAdjacentHTML("beforeend", `<option value="${esc(a.model)}">${esc(a.model)}</option>`);
  }
  $("#a-model").value = a.model || "claude-haiku-4-5";
  $("#a-cap").value = a.max_daily_cost_usd ?? "";
}

$("#sites").addEventListener("click", (e) => {
  if (e.target.matches("[data-remove]")) e.target.closest("tr").remove();
  if (e.target.matches("[data-add-search]")) {
    const card = e.target.closest("[data-site]");
    card.querySelector("tbody").insertAdjacentHTML("beforeend", searchRow(card.dataset.site, {}));
  }
});
$("#categories").addEventListener("click", (e) => {
  if (e.target.matches("[data-remove-category]")) e.target.closest(".category").remove();
});
$("#add-category").addEventListener("click", () => $("#categories").insertAdjacentHTML("beforeend", categoryCard({ name: "" })));

function collectSettings() {
  const sites = {};
  document.querySelectorAll("#sites [data-site]").forEach((card) => {
    sites[card.dataset.site] = {
      enabled: card.querySelector("[data-enabled]").checked,
      interval: [...card.querySelectorAll("[data-interval]")].map((i) => i.value).filter((v) => v !== "").map((m) => m * 60),
      searches: [...card.querySelectorAll("tbody tr")].map((tr) =>
        Object.fromEntries([...tr.querySelectorAll("[data-field]")].map((i) => [i.dataset.field, i.value]))),
    };
  });
  const categories = [...document.querySelectorAll("#categories .category")].map((card) => {
    const get = (f) => card.querySelector(`[data-field="${f}"]`);
    return {
      name: get("name").value, max_budget: get("max_budget").value, keywords: get("keywords").value,
      exclude_keywords: get("exclude_keywords").value, include_keywords: get("include_keywords").value,
      require_include: get("require_include").checked,
    };
  });
  const dealTypes = [];
  if ($("#n-repair").checked) dealTypes.push("reparation");
  if ($("#n-direct").checked) dealTypes.push("revente_directe");
  return {
    sites, categories,
    filters: { global_exclude_keywords: $("#g-exclude").value, deal_signals: $("#g-signals").value,
               buyer_ad_keywords: $("#g-buyer").value, min_price: $("#g-minprice").value,
               drop_uncategorized: $("#g-uncat").checked, keep_unknown_price: $("#g-noprice").checked },
    location: { france_only: $("#g-france").checked, exclude_overseas: $("#g-overseas").checked },
    notifier: { min_score: $("#n-min").value, watch_score: $("#n-watch").value, deal_types: dealTypes,
                min_margin: $("#n-margin").value, min_ease: $("#n-ease").value },
    analyzer: { model: $("#a-model").value, max_daily_cost_usd: $("#a-cap").value,
                batch_size: $("#a-batch").value, max_description_chars: $("#a-desc").value },
    storage: { seen_retention_days: $("#s-retention").value },
    network: { access_denied_pause_hours: $("#s-pause").value },
  };
}

$("#save").addEventListener("click", async () => {
  $("#save-status").textContent = "Enregistrement…";
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(collectSettings()) });
    $("#save-status").textContent = "✅ Enregistré dans config.yaml";
    settings = null;
    await loadSettings();
  } catch (e) {
    $("#save-status").textContent = "❌ " + e.message;
  }
});

restoreFilters();
try { showTab(localStorage.getItem("dealsniper-tab") || "ads"); } catch (e) { showTab("ads"); }
loadAds();
setInterval(loadAds, 60000);
