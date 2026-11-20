// Deal Sniper — interface locale. Annonces : /api/ads ; réglages : /api/settings.

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const euros = (v) => (v == null ? "?" : `${Number(v).toLocaleString("fr-FR", { maximumFractionDigits: 0 })} €`);
const FILTER_IDS = ["f-text", "f-category", "f-type", "f-score", "f-ease", "f-sale", "f-price", "f-margin", "f-days", "f-sort"];
const SEARCH_FIELDS = { common: ["query", "condition", "max_price"], ebay: ["query", "condition", "max_price"], leboncoin: ["query", "category", "max_price"],
                        default: ["query", "max_price"] };
const LBC_CATEGORIES = { "": "Toutes catégories", 15: "Informatique", 43: "Consoles & jeux vidéo", 16: "Image & son", 21: "Bricolage" };
const FIELD_LABELS = { query: "Recherche", condition: "État", max_price: "Prix max", category: "Catégorie" };
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
  $("#budget-today").textContent = cap ? `${dollars(spent)} / ${dollars(cap)}` : `${dollars(spent)} (sans limite)`;
  $("#budget-fill-2").style.width = $("#budget-fill").style.width;
  $("#budget-fill-2").className = $("#budget-fill").className;
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

// ---------- Progression d'un passage (chrono + coût IA) ----------
const SPINNER = "|/-\\";
let progress = null, progressAt = 0, progressClosed = false, spin = 0;

function duration(seconds) {
  const s = Math.floor(seconds);
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
  return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;
}
const iaCost = (usd) => `${Number(usd || 0).toLocaleString("fr-FR", { minimumFractionDigits: 4, maximumFractionDigits: 4 })} $`;

function renderProgress(data) {
  if (data && !data.done) progressClosed = false;  // nouveau passage : on réaffiche
  progress = data;
  progressAt = Date.now();
  drawProgress();
}

function drawProgress() {
  const box = $("#progress");
  const p = progress;
  if (!p || !(p.sites || []).length || (p.done && progressClosed)) { box.hidden = true; return; }
  box.hidden = false;
  const live = (secs, done) => duration(done ? secs : secs + (Date.now() - progressAt) / 1000);
  const rows = p.sites.map((l) => `<div class="prog-row ${l.done ? "done" : ""}">
      <b>${esc(l.site)}</b><span class="prog-text">${esc(l.text)}</span>
      <span class="prog-time">⏱ ${live(l.elapsed, l.done)}</span>
      <span class="prog-cost">${l.cost ? iaCost(l.cost) : "—"}</span>
      <span class="spin">${l.done ? "✓" : SPINNER[spin]}</span></div>`).join("");
  box.innerHTML = `<div class="prog-head">
      <span>${p.done ? "✅ Dernier passage terminé" : "⏳ Passage en cours…"}</span>
      ${p.done ? '<button class="icon" data-close-progress title="Masquer">✕</button>' : ""}</div>
    ${rows}
    <div class="prog-row total"><b>Total</b><span></span><span class="prog-time">⏱ ${live(p.elapsed, p.done)}</span>
      <span class="prog-cost">${iaCost(p.cost)}</span><span></span></div>`;
}
setInterval(() => {  // chrono et « buffering » animés entre deux rafraîchissements
  if (!progress || progress.done) return;
  spin = (spin + 1) % SPINNER.length;
  drawProgress();
}, 150);
$("#progress").addEventListener("click", (e) => {
  if (e.target.closest("[data-close-progress]")) { progressClosed = true; drawProgress(); }
});

// ---------- Annonces ----------
let runPoll = null;
async function loadAds() {
  try {
    const data = await api(`/api/ads?status=${encodeURIComponent(status)}&days=${$("#f-days").value}`);
    allAds = data.ads;
    renderBudget(data.cost_today, data.cost_cap);
    $("#run").disabled = data.running;
    $("#run").textContent = data.running ? "⏳ Passage en cours…" : "▶ Lancer un passage";
    if (data.error) toast("Dernier passage en erreur : " + data.error);
    renderProgress(data.progress);
    if (data.running && !runPoll) runPoll = setInterval(loadAds, 1500);  // suivi rapide pendant un passage
    if (!data.running && runPoll) { clearInterval(runPoll); runPoll = null; toast("Passage terminé"); }
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
  const minSale = Number($("#f-sale").value || 0);
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
    if (minSale && (a.facilite_vente ?? 0) < minSale) return false;
    if (maxPrice != null && (ad.total_price ?? Infinity) > maxPrice) return false;
    if (minMargin != null && (a.marge_nette_estimee ?? -Infinity) < minMargin) return false;
    return true;
  });

  const margin = (ad) => (ad.analysis || {}).marge_nette_estimee ?? -1e9;
  const sorters = {
    recent: (x, y) => (y.seen_at || "").localeCompare(x.seen_at || ""),
    score: (x, y) => (y.score ?? -1) - (x.score ?? -1),
    margin: (x, y) => margin(y) - margin(x),
    sale: (x, y) => ((y.analysis || {}).facilite_vente ?? -1) - ((x.analysis || {}).facilite_vente ?? -1) || margin(y) - margin(x),
    price: (x, y) => (x.total_price ?? 1e9) - (y.total_price ?? 1e9),
  };
  return ads.sort(sorters[$("#f-sort").value] || sorters.recent);
}

// Pastilles des deux notes secondaires : facilité de réparation et facilité de vente (demande).
function scoreBadges(a) {
  const badge = (value, icon, label, title) => {
    if (value == null) return "";
    const level = value >= 7 ? "pos" : value >= 4 ? "mid" : "neg";
    return `<span class="ease ${level}" title="${title}">${icon} ${label} ${value}/10</span>`;
  };
  const repair = a.type_affaire === "revente_directe" ? ""
    : badge(a.facilite_reparation, "🔧", "réparation", "Facilité de réparation : 10 = rien ou presque à faire");
  const sale = badge(a.facilite_vente, "🔥", "vente", "Facilité de vente : 10 = modèle très recherché, part en quelques jours");
  return repair || sale ? `<div class="badges">${repair}${sale}</div>` : "";
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
      ${fault ? `<div class="fault">🩺 ${esc(fault)}</div>` : ""}
      ${scoreBadges(a)}
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
    loadAds();
  } catch (e) { toast(e.message); }
});

$("#test-notif").addEventListener("click", async () => {
  const data = await api("/api/test-notif", { method: "POST" }).catch(() => ({ ok: false }));
  toast(data.ok ? "Notification envoyée ✅" : "Échec : vérifiez TELEGRAM_* / DISCORD_* dans .env ❌");
});

// ---------- Réglages : composants ----------

// Étiquettes de mots-clés : Entrée ou virgule pour ajouter, ✕ ou Retour arrière pour retirer.
function tagHTML(word) {
  return `<span class="tag"><span>${esc(word)}</span><button type="button" data-untag title="Retirer">✕</button></span>`;
}
function setTags(el, list) {
  if (typeof el === "string") el = $(el);
  el.innerHTML = (list || []).map(tagHTML).join("") + `<input class="tag-input" placeholder="${el.dataset.placeholder || "Ajouter…"}">`;
}
const getTags = (el) => [...(typeof el === "string" ? $(el) : el).querySelectorAll(".tag > span")].map((t) => t.textContent);
function addTags(box, text) {
  const existing = new Set(getTags(box).map((w) => w.toLowerCase()));
  const input = box.querySelector(".tag-input");
  text.split(",").map((w) => w.trim()).filter(Boolean).forEach((w) => {
    if (existing.has(w.toLowerCase())) return;
    existing.add(w.toLowerCase());
    input.insertAdjacentHTML("beforebegin", tagHTML(w));
  });
  input.value = "";
  markDirty();
}
document.addEventListener("keydown", (e) => {
  if (!e.target.matches(".tag-input")) return;
  const box = e.target.closest(".tags");
  if ((e.key === "Enter" || e.key === ",") && e.target.value.trim()) { e.preventDefault(); addTags(box, e.target.value); }
  else if (e.key === "Backspace" && !e.target.value) { box.querySelectorAll(".tag").length && [...box.querySelectorAll(".tag")].pop().remove(); markDirty(); }
});
document.addEventListener("focusout", (e) => { if (e.target.matches?.(".tag-input") && e.target.value.trim()) addTags(e.target.closest(".tags"), e.target.value); });
document.addEventListener("paste", (e) => {
  if (!e.target.matches(".tag-input")) return;
  e.preventDefault();
  addTags(e.target.closest(".tags"), e.clipboardData.getData("text").replace(/\n/g, ","));
});
document.addEventListener("click", (e) => {
  if (e.target.matches("[data-untag]")) { e.target.closest(".tag").remove(); markDirty(); }
  else if (e.target.matches(".tags")) e.target.querySelector(".tag-input")?.focus();
});

// Curseurs : valeur affichée à côté.
function syncRanges() {
  document.querySelectorAll(".range output").forEach((o) => { o.textContent = `${$("#" + o.dataset.for).value}/10`; });
}
document.addEventListener("input", (e) => { if (e.target.type === "range") syncRanges(); });

// Modifications non enregistrées.
let savedSnapshot = "";
function markDirty() {
  if (!settings) return;
  const dirty = JSON.stringify(collectSettings()) !== savedSnapshot;
  $("#savebar").classList.toggle("dirty", dirty);
  $("#save-status").textContent = dirty ? "● Modifications non enregistrées" : "Tout est enregistré";
  $("#discard").hidden = !dirty;
}
["input", "change"].forEach((type) =>
  document.querySelectorAll(".page:not(#tab-ads)").forEach((page) => page.addEventListener(type, () => setTimeout(markDirty))));
window.addEventListener("beforeunload", (e) => { if ($("#savebar").classList.contains("dirty")) e.preventDefault(); });
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "s" && !$("#savebar").hidden) { e.preventDefault(); $("#save").click(); }
});

// ---------- Recherches ----------
function searchRow(site, row) {
  const fields = SEARCH_FIELDS[site] || SEARCH_FIELDS.default;
  const known = new Set(fields);
  const extra = Object.fromEntries(Object.entries(row).filter(([k]) => !known.has(k)));  // ex. limit : conservé tel quel
  const cells = fields.map((f) => {
    if (f === "query") return `<input data-field="query" class="q" value="${esc(row.query ?? "")}" placeholder="ex. thinkpad t480">`;
    if (f === "condition") {
      const opts = Object.entries(CONDITIONS)
        .map(([value, label]) => `<option value="${value}" ${(row[f] || "any") === value ? "selected" : ""}>${label}</option>`).join("");
      return `<select data-field="condition">${opts}</select>`;
    }
    if (f === "category") {
      const value = String(row.category ?? "");
      const cats = value in LBC_CATEGORIES ? LBC_CATEGORIES : { ...LBC_CATEGORIES, [value]: `n° ${value}` };
      return `<select data-field="category">${Object.entries(cats)
        .map(([v, label]) => `<option value="${esc(v)}" ${v === value ? "selected" : ""}>${esc(label)}</option>`).join("")}</select>`;
    }
    if (f === "max_price") return `<span class="affix"><input data-field="max_price" type="number" min="0" value="${esc(row[f] ?? "")}" placeholder="max"><i>€</i></span>`;
    return `<input data-field="${f}" value="${esc(row[f] ?? "")}" placeholder="${FIELD_LABELS[f] || f}">`;
  });
  return `<div class="search-row" data-extra="${esc(JSON.stringify(extra))}">${cells.join("")}
    <button class="icon danger" data-remove title="Supprimer cette recherche">🗑</button></div>`;
}

function searchTable(key, rows) {
  const fields = SEARCH_FIELDS[key] || SEARCH_FIELDS.default;
  const label = (f) => (key === "common" && f === "condition" ? "État (eBay)" : FIELD_LABELS[f] || f);
  return `<div class="site-body" style="--cols: ${fields.map((f) => (f === "query" ? "minmax(0, 2fr)" : f === "max_price" ? "120px" : "minmax(0, 1fr)")).join(" ")} 38px">
        <div class="search-row head">${fields.map((f) => `<span>${label(f)}</span>`).join("")}<span></span></div>
        <div class="search-list">${rows.map((r) => searchRow(key, r)).join("")}</div>
        <button class="add" data-add-search>＋ Ajouter une recherche</button>
      </div>`;
}

const plural = (n, word) => `${n} ${word}${n > 1 ? "s" : ""}`;

function renderSites() {
  const common = settings.searches || [];
  const commonCard = `<div class="site-card common" data-common-card data-site="common">
      <div class="site-head">
        <button class="collapse" data-collapse title="Replier">▾</button>
        <span class="site-logo">🌐</span>
        <div class="site-name"><b>Tous les sites</b><small><span data-count>${plural(common.length, "recherche")}</span>
          communes, lancées sur chaque site activé — l'état ne sert qu'à eBay</small></div>
      </div>
      ${searchTable("common", common)}
    </div>
    <h3 class="sites-title">Recherches propres à chaque site <span class="muted">(en plus des communes)</span></h3>`;
  $("#sites").innerHTML = commonCard + Object.entries(settings.sites).map(([name, site]) => {
    const minutes = (site.interval || ["", ""]).map((v) => (v === "" ? "" : Math.round(v / 60)));
    const count = (site.searches || []).length;
    return `<div class="site-card ${site.enabled === false ? "off" : ""}" data-site="${esc(name)}">
      <div class="site-head">
        <button class="collapse" data-collapse title="Replier">▾</button>
        <span class="site-logo">${siteLogo(name, 26)}</span>
        <div class="site-name"><b>${esc(siteName(name))}</b><small><span data-count>${plural(count, "recherche")}</span>
          <span data-common-note>${site.common_searches === false ? "· sans les communes" : `+ ${common.length} communes`}</span></small></div>
        <label class="interval" title="Rythme en surveillance continue (commande watch)">⏱ toutes les
          <input type="number" min="1" data-interval="0" value="${esc(minutes[0])}"> –
          <input type="number" min="1" data-interval="1" value="${esc(minutes[1])}"> min</label>
        <label class="mini-switch" title="Lancer aussi les recherches communes sur ce site">🌐
          <span class="switch"><input type="checkbox" data-use-common ${site.common_searches !== false ? "checked" : ""}><span></span></span></label>
        <label class="switch" title="Activer / désactiver ce site"><input type="checkbox" data-enabled ${site.enabled !== false ? "checked" : ""}><span></span></label>
      </div>
      ${searchTable(name, site.searches || [])}
    </div>`;
  }).join("");
}

function updateSiteCount(card) {
  const n = card.querySelectorAll(".search-list .search-row").length;
  card.querySelector("[data-count]").textContent = `${n} recherche${n > 1 ? "s" : ""}`;
}

$("#sites").addEventListener("click", (e) => {
  const card = e.target.closest("[data-site]");
  if (e.target.closest("[data-remove]")) {
    e.target.closest(".search-row").remove();
    updateSiteCount(card); markDirty();
  } else if (e.target.closest("[data-add-search]")) {
    card.classList.remove("collapsed");
    card.querySelector(".search-list").insertAdjacentHTML("beforeend", searchRow(card.dataset.site, {}));
    card.querySelector(".search-list .search-row:last-child .q").focus();
    updateSiteCount(card); markDirty();
  } else if (e.target.closest("[data-collapse]")) {
    card.classList.toggle("collapsed");
  }
});
$("#sites").addEventListener("change", (e) => {
  if (e.target.matches("[data-enabled]")) e.target.closest("[data-site]").classList.toggle("off", !e.target.checked);
  if (e.target.matches("[data-use-common]")) {
    const n = document.querySelectorAll("[data-common-card] .search-list .search-row").length;
    e.target.closest("[data-site]").querySelector("[data-common-note]").textContent =
      e.target.checked ? `+ ${n} communes` : "· sans les communes";
  }
});
$("#search-filter").addEventListener("input", () => {
  const q = $("#search-filter").value.trim().toLowerCase();
  document.querySelectorAll("#sites .search-list .search-row").forEach((row) => {
    row.hidden = q && !row.querySelector(".q").value.toLowerCase().includes(q);
  });
});

// ---------- Catégories (liste à gauche, détail à droite) ----------
let selectedCategory = 0;

function categoryEditor(cat, i) {
  return `<div class="panel cat-editor" data-index="${i}" ${i === selectedCategory ? "" : "hidden"}>
    <div class="cat-title">
      <input data-field="name" class="cat-name" value="${esc(cat.name)}" placeholder="Nom de la catégorie">
      <label class="budget-field">Budget max<span class="affix"><input data-field="max_budget" type="number" min="0" value="${esc(cat.max_budget ?? "")}" placeholder="—"><i>€</i></span></label>
      <button class="icon danger" data-remove-category title="Supprimer la catégorie">🗑</button>
    </div>
    <div class="label">🔎 Mots-clés <span class="muted">— le titre doit en contenir un pour entrer dans la catégorie</span></div>
    <div class="tags blue" data-field="keywords" data-placeholder="ex. thinkpad"></div>
    <div class="label">🚫 Exclusions <span class="muted">— l'annonce est écartée</span></div>
    <div class="tags red" data-field="exclude_keywords" data-placeholder="ex. coque seule"></div>
    <div class="label">🔧 Signaux positifs <span class="muted">— panne simple ou bonne affaire, mis en avant</span></div>
    <div class="tags green" data-field="include_keywords" data-placeholder="ex. sans ssd"></div>
    <div class="setting">
      <div><b>Exiger un signal positif</b><small>Sans aucun signal, l'annonce est écartée (plus strict, moins de coût IA)</small></div>
      <label class="switch"><input type="checkbox" data-field="require_include" ${cat.require_include ? "checked" : ""}><span></span></label>
    </div>
  </div>`;
}

function renderCategoryNav() {
  const editors = [...document.querySelectorAll("#categories .cat-editor")];
  $("#cat-nav").innerHTML = editors.map((ed, i) => {
    const name = ed.querySelector("[data-field=name]").value || "Sans nom";
    const budget = ed.querySelector("[data-field=max_budget]").value;
    const n = getTags(ed.querySelector("[data-field=keywords]")).length;
    return `<button class="cat-item ${i === selectedCategory ? "on" : ""}" data-select="${i}">
      <b>${esc(name)}</b><small>${n} mot${n > 1 ? "s" : ""}-clé${n > 1 ? "s" : ""}${budget ? ` · ≤ ${esc(budget)} €` : ""}</small></button>`;
  }).join("") + `<button class="add" id="add-category">＋ Nouvelle catégorie</button>`;
}

function selectCategory(i) {
  selectedCategory = i;
  document.querySelectorAll("#categories .cat-editor").forEach((ed, j) => (ed.hidden = j !== i));
  renderCategoryNav();
}

function renderCategories() {
  $("#categories").innerHTML = settings.categories.map(categoryEditor).join("");
  document.querySelectorAll("#categories .cat-editor").forEach((ed, i) => {
    ed.querySelectorAll(".tags").forEach((box) => setTags(box, settings.categories[i][box.dataset.field]));
  });
  selectedCategory = Math.min(selectedCategory, Math.max(0, settings.categories.length - 1));
  renderCategoryNav();
}

$("#cat-nav").addEventListener("click", (e) => {
  const item = e.target.closest("[data-select]");
  if (item) selectCategory(Number(item.dataset.select));
  if (e.target.closest("#add-category")) {
    const i = document.querySelectorAll("#categories .cat-editor").length;
    $("#categories").insertAdjacentHTML("beforeend", categoryEditor({ name: "" }, -1));
    $("#categories").lastElementChild.querySelectorAll(".tags").forEach((box) => setTags(box, []));
    selectCategory(i);
    $("#categories").lastElementChild.querySelector(".cat-name").focus();
    markDirty();
  }
});
$("#categories").addEventListener("click", (e) => {
  if (!e.target.closest("[data-remove-category]")) return;
  const ed = e.target.closest(".cat-editor");
  const name = ed.querySelector("[data-field=name]").value || "cette catégorie";
  if (!confirm(`Supprimer « ${name} » ?`)) return;
  ed.remove();
  selectCategory(Math.max(0, selectedCategory - 1));
  markDirty();
});
$("#categories").addEventListener("input", () => renderCategoryNav());
$("#categories").addEventListener("click", (e) => { if (e.target.matches("[data-untag]")) setTimeout(renderCategoryNav); });

// ---------- Chargement / enregistrement ----------
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
  renderCategories();
  const f = settings.filters, n = settings.notifier, a = settings.analyzer, l = settings.location;
  setTags("#g-exclude", f.global_exclude_keywords);
  setTags("#g-signals", f.deal_signals);
  setTags("#g-buyer", f.buyer_ad_keywords);
  setTags("#g-sold-title", f.sold_title_keywords);
  setTags("#g-sold-desc", f.sold_phrases);
  $("#g-minprice").value = f.min_price ?? "";
  $("#g-france").checked = l.france_only !== false;
  $("#g-overseas").checked = l.exclude_overseas !== false;
  $("#g-uncat").checked = f.drop_uncategorized !== false;
  $("#g-noprice").checked = f.keep_unknown_price !== false;

  $("#n-min").value = n.min_score ?? 7;
  $("#n-watch-on").checked = n.watch_score != null;
  $("#n-watch").value = n.watch_score ?? 5;
  $("#n-watch-row").classList.toggle("disabled", n.watch_score == null);
  $("#n-margin").value = n.min_margin ?? "";
  $("#n-ease").value = n.min_ease ?? "";
  $("#n-sale").value = n.min_sale ?? "";
  const types = n.deal_types || ["reparation", "revente_directe"];
  $("#n-repair").checked = types.includes("reparation");
  $("#n-direct").checked = types.includes("revente_directe");

  const model = a.model || "claude-haiku-4-5";
  if (!document.querySelector(`[name=a-model][value="${CSS.escape(model)}"]`)) {
    $("#a-model").insertAdjacentHTML("beforeend",
      `<label class="tile"><input type="radio" name="a-model" value="${esc(model)}"><span>🤖</span><b>${esc(model)}</b><small>défini dans config.yaml</small></label>`);
  }
  document.querySelector(`[name=a-model][value="${CSS.escape(model)}"]`).checked = true;
  $("#a-cap").value = a.max_daily_cost_usd ?? "";
  $("#a-batch").value = a.batch_size ?? 20;
  $("#a-desc").value = a.max_description_chars ?? 400;
  $("#s-retention").value = (settings.storage || {}).seen_retention_days ?? 90;
  $("#s-pause").value = (settings.network || {}).access_denied_pause_hours ?? 6;
  syncRanges();
  savedSnapshot = JSON.stringify(collectSettings());
  markDirty();
}

$("#n-watch-on").addEventListener("change", () => $("#n-watch-row").classList.toggle("disabled", !$("#n-watch-on").checked));

function searchRows(card) {
  return [...card.querySelectorAll(".search-list .search-row")].map((row) => ({
    ...JSON.parse(row.dataset.extra || "{}"),
    ...Object.fromEntries([...row.querySelectorAll("[data-field]")].map((i) => [i.dataset.field, i.value])),
  }));
}

function collectSettings() {
  const sites = {};
  const commonCard = $("[data-common-card]");
  document.querySelectorAll("#sites [data-site]:not([data-common-card])").forEach((card) => {
    sites[card.dataset.site] = {
      enabled: card.querySelector("[data-enabled]").checked,
      common_searches: card.querySelector("[data-use-common]").checked,
      interval: [...card.querySelectorAll("[data-interval]")].map((i) => i.value).filter((v) => v !== "").map((m) => m * 60),
      searches: searchRows(card),
    };
  });
  const categories = [...document.querySelectorAll("#categories .cat-editor")].map((ed) => {
    const get = (f) => ed.querySelector(`[data-field="${f}"]`);
    return {
      name: get("name").value, max_budget: get("max_budget").value,
      keywords: getTags(get("keywords")), exclude_keywords: getTags(get("exclude_keywords")),
      include_keywords: getTags(get("include_keywords")), require_include: get("require_include").checked,
    };
  });
  const dealTypes = [];
  if ($("#n-repair").checked) dealTypes.push("reparation");
  if ($("#n-direct").checked) dealTypes.push("revente_directe");
  return {
    searches: commonCard ? searchRows(commonCard) : settings.searches, sites, categories,
    filters: { global_exclude_keywords: getTags("#g-exclude"), deal_signals: getTags("#g-signals"),
               buyer_ad_keywords: getTags("#g-buyer"), min_price: $("#g-minprice").value,
               sold_title_keywords: getTags("#g-sold-title"), sold_phrases: getTags("#g-sold-desc"),
               drop_uncategorized: $("#g-uncat").checked, keep_unknown_price: $("#g-noprice").checked },
    location: { france_only: $("#g-france").checked, exclude_overseas: $("#g-overseas").checked },
    notifier: { min_score: $("#n-min").value, watch_score: $("#n-watch-on").checked ? $("#n-watch").value : "",
                deal_types: dealTypes, min_margin: $("#n-margin").value, min_ease: $("#n-ease").value,
                min_sale: $("#n-sale").value },
    analyzer: { model: document.querySelector("[name=a-model]:checked")?.value, max_daily_cost_usd: $("#a-cap").value,
                batch_size: $("#a-batch").value, max_description_chars: $("#a-desc").value },
    storage: { seen_retention_days: $("#s-retention").value },
    network: { access_denied_pause_hours: $("#s-pause").value },
  };
}

$("#save").addEventListener("click", async () => {
  $("#save-status").textContent = "Enregistrement…";
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(collectSettings()) });
    await loadSettings();
    toast("✅ Réglages enregistrés");
  } catch (e) {
    $("#save-status").textContent = "❌ " + e.message;
  }
});
$("#discard").addEventListener("click", () => { if (confirm("Annuler les modifications non enregistrées ?")) loadSettings(); });

restoreFilters();
try { showTab(localStorage.getItem("dealsniper-tab") || "ads"); } catch (e) { showTab("ads"); }
loadAds();
setInterval(loadAds, 60000);
