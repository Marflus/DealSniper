// Deal Sniper — interface locale. Annonces : /api/ads ; réglages : /api/settings.

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const euros = (v) => (v == null ? "?" : `${Number(v).toLocaleString("fr-FR", { maximumFractionDigits: 0 })} €`);
const FILTER_IDS = ["f-text", "f-category", "f-type", "f-score", "f-ease", "f-sale", "f-price", "f-margin", "f-near", "f-distance",
  "f-delivery", "f-seller", "f-days", "f-sort"];
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
let geoState = "";  // distances : ok / chargement / erreur / inactif
let preferences = {};  // goût appris par catégorie (favoris − masquées)
let status = "analyzed";
let selectedSites = new Set();  // vide = tous les sites
let settings = null;
let running = false;  // un passage (ou une recherche ponctuelle) est en cours
let searchQuery = "", searchPoll = null, searchResults = [];  // recherche ponctuelle

// Préférences du navigateur (onglet, thème, filtres…) : le stockage peut être indisponible
// (navigation privée, données bloquées) ; l'interface fonctionne alors sans mémoire.
const store = {
  get(key, fallback = null) {
    try { return localStorage.getItem("dealsniper-" + key) ?? fallback; } catch (e) { return fallback; }
  },
  set(key, value) {
    try {
      if (value == null || value === "") localStorage.removeItem("dealsniper-" + key);
      else localStorage.setItem("dealsniper-" + key, value);
    } catch (e) { /* stockage indisponible */ }
  },
};

function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), 3500);
}

// Copie dans le presse-papiers, avec repli pour l'accès en HTTP local (téléphone sur le Wi-Fi).
async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) { /* repli ci-dessous */ }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  ta.remove();
  return ok;
}

async function api(url, options = {}) {
  const resp = await fetch(url, { headers: { "Content-Type": "application/json" }, ...options });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const err = new Error(data.error || data.message || `HTTP ${resp.status}`);
    err.data = data;  // détails (ex. « manual » : saisie à la main proposée)
    throw err;
  }
  return data;
}

// ---------- Onglets ----------
// Tous les onglets sauf « Annonces » modifient config.yaml et partagent le bouton Enregistrer.
// Une seule barre de filtres : elle suit l'onglet (Annonces, ou résultats de la Recherche ponctuelle).
const toolbarHome = document.createComment("filtres");
document.querySelector(".toolbar").before(toolbarHome);
function placeToolbar(tab) {
  const bar = document.querySelector(".toolbar");
  if (tab === "search") $("#search-filters").prepend(bar);
  else if (tab === "ads") toolbarHome.after(bar);
}

function showTab(tab) {
  placeToolbar(tab);
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".page").forEach((p) => (p.hidden = p.id !== `tab-${tab}`));
  $("#savebar").hidden = ["ads", "search", "purchases", "stats", "analyze"].includes(tab);  // n'éditent pas config.yaml
  if (tab === "purchases") loadPurchases();
  else if (tab === "analyze") loadLinkHistory();
  else if (tab === "stats") loadStats();
  else if (!["ads", "search"].includes(tab) && !settings) loadSettings();
  if (tab === "saved") refreshLoops();
  store.set("tab", tab);
}
document.querySelectorAll(".tab").forEach((btn) => btn.addEventListener("click", () => showTab(btn.dataset.tab)));
$(".brand").addEventListener("click", () => showTab("ads"));  // cliquer le logo revient aux annonces

// Réglages : menu à gauche, une section visible à la fois.
function showSettingsSection(id) {
  document.querySelectorAll("#set-nav .set-item").forEach((b) => b.classList.toggle("on", b.dataset.sec === id));
  document.querySelectorAll(".set-sec").forEach((sec) => (sec.hidden = sec.id !== id));
  store.set("set", id);
}
$("#set-nav").addEventListener("click", (e) => {
  const item = e.target.closest("[data-sec]");
  if (item) showSettingsSection(item.dataset.sec);
});

// ---------- Thème clair / sombre ----------
function setTheme(theme) {
  if (theme) document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
  document.querySelectorAll("#theme-choice button").forEach((b) => b.classList.toggle("on", b.dataset.value === theme));
  store.set("theme", theme);
}
document.querySelectorAll("#theme-choice button").forEach((b) => b.addEventListener("click", () => setTheme(b.dataset.value)));
setTheme(document.documentElement.dataset.theme || "");

// ---------- Filtres (mémorisés dans le navigateur) ----------
function saveFilters() {
  const values = Object.fromEntries(FILTER_IDS.map((id) => [id, $("#" + id).value]));
  store.set("filters", JSON.stringify({ ...values, status, sites: [...selectedSites] }));
}

// Téléphone : filtres repliés derrière un bouton qui indique combien sont actifs.
function countActiveFilters() {
  const active = FILTER_IDS.filter((id) => id !== "f-sort" && id !== "f-days" && id !== "f-text" && $("#" + id).value).length
    + (selectedSites.size ? 1 : 0);
  $("#toggle-filters").textContent = active ? `⚙ Filtres (${active})` : "⚙ Filtres";
}
$("#toggle-filters").addEventListener("click", () => {
  const open = $(".toolbar").classList.toggle("open");
  $("#toggle-filters").setAttribute("aria-expanded", open);
});
document.querySelector(".toolbar").addEventListener("change", countActiveFilters);
document.querySelector(".toolbar").addEventListener("click", () => setTimeout(countActiveFilters));

function restoreFilters() {
  try {
    const values = JSON.parse(store.get("filters", "{}"));
    FILTER_IDS.forEach((id) => { if (values[id] != null && values[id] !== "0") $("#" + id).value = values[id]; });
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
const openedSites = new Set();  // lignes dépliées (détail des recherches)

function duration(seconds) {
  const s = Math.floor(seconds);
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
  return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;
}
const iaCost = (usd) => `${Number(usd || 0).toLocaleString("fr-FR", { minimumFractionDigits: 4, maximumFractionDigits: 4 })} $`;

function renderProgress(data) {
  if (data && !data.done) progressClosed = false;  // nouveau passage : on réaffiche
  if (data && data.done && progress && !progress.done) data.sites.forEach((l) => openedSites.add(l.site));  // fin : résumé déplié
  progress = data;
  progressAt = Date.now();
  drawProgress();
}

function drawProgress() {
  const box = $("#progress");
  const p = progress;
  const mirror = $("#search-live");  // même affichage animé dans l'onglet Recherche
  mirror.hidden = !(searchPoll && p && (p.sites || []).length);
  if (!p || !(p.sites || []).length || (p.done && progressClosed)) { box.hidden = true; return; }
  box.hidden = false;
  const live = (secs, done) => duration(done ? secs : secs + (Date.now() - progressAt) / 1000);
  const rows = p.sites.map((l) => {
    const searches = l.searches || [];
    const open = openedSites.has(l.site);
    const detail = open && searches.length ? `<div class="prog-detail">${searches.map((x) =>
      `<span class="${x.found ? "" : "zero"}">${esc(x.query)} <b>${x.found}</b></span>`).join("")}</div>` : "";
    return `<div class="prog-row ${l.done ? "done" : ""} ${l.text.startsWith("ignoré") ? "skipped" : ""} ${searches.length ? "expandable" : ""}" data-site-log="${esc(l.site)}"
        title="${searches.length ? "Cliquer pour voir le détail des recherches" : ""}">
      <b>${searches.length ? (open ? "▾ " : "▸ ") : ""}${esc(l.site)}</b><span class="prog-text">${esc(l.text)}</span>
      <span class="prog-time">⏱ ${live(l.elapsed, l.done)}</span>
      <span class="prog-cost">${l.cost ? iaCost(l.cost) : "—"}</span>
      <span class="spin">${l.text.startsWith("ignoré") ? "⚠" : l.done ? "✓" : SPINNER[spin]}</span></div>${detail}`;
  }).join("");
  box.innerHTML = `<div class="prog-head">
      <span>${p.done ? "✅ Dernier passage terminé" : "⏳ Passage en cours…"}</span>
      ${p.done ? '<button class="icon" data-close-progress title="Masquer">✕</button>' : ""}</div>
    ${rows}
    <div class="prog-row total"><b>Total</b><span></span><span class="prog-time">⏱ ${live(p.elapsed, p.done)}</span>
      <span class="prog-cost">${iaCost(p.cost)}</span><span></span></div>`;
  if (!mirror.hidden) mirror.innerHTML = box.innerHTML;
}
setInterval(() => {  // chrono et « buffering » animés entre deux rafraîchissements
  if (!progress || progress.done) return;
  spin = (spin + 1) % SPINNER.length;
  drawProgress();
}, 150);
["#progress", "#search-live"].forEach((sel) => $(sel).addEventListener("click", (e) => {
  if (e.target.closest("[data-close-progress]")) { progressClosed = true; drawProgress(); return; }
  const row = e.target.closest(".prog-row.expandable");
  if (row) {
    const site = row.dataset.siteLog;
    openedSites.has(site) ? openedSites.delete(site) : openedSites.add(site);
    drawProgress();
  }
}));

// Zone : centre des distances (ville ou code postal), sinon le domicile réglé dans Réglages.
const nearParam = () => ($("#f-near").value.trim() ? `&near=${encodeURIComponent($("#f-near").value.trim())}` : "");

// Champs de la recherche ponctuelle reliés aux filtres (remise, zone) : une seule valeur, deux endroits.
function syncMirrors() {  // après une restauration ou une réinitialisation des filtres (sans événement « change »)
  document.querySelectorAll("[data-mirror]").forEach((el) => { el.value = $("#" + el.dataset.mirror).value; });
}
document.querySelectorAll("[data-mirror]").forEach((el) => {
  const target = $("#" + el.dataset.mirror);
  el.value = target.value;
  el.addEventListener("change", () => {
    target.value = el.value;
    // Le filtre écoute « input » (champ texte) ou « change » (liste) : on envoie l'événement qu'il attend.
    target.dispatchEvent(new Event(target.tagName === "INPUT" ? "input" : "change", { bubbles: true }));
  });
  ["input", "change"].forEach((type) => target.addEventListener(type, () => { el.value = target.value; }));
});

// ---------- Annonces ----------
let runPoll = null, runTicks = 0, lastError = null;

// État du passage (budget, bouton Lancer/Annuler, erreur, progression) : commun à /api/ads et /api/progress.
function applyStatus(data) {
  renderBudget(data.cost_today, data.cost_cap);
  setRunning(data.running);
  if (data.error && data.error !== lastError) toast("Dernier passage en erreur : " + data.error);
  lastError = data.error;
  renderProgress(data.progress);
}

// Pendant un passage : suivi léger toutes les 1,5 s ; la liste complète des annonces toutes les 15 s seulement.
async function pollProgress() {
  const data = await api("/api/progress").catch(() => null);
  if (!data) return;
  applyStatus(data);
  if (data.running) {
    if (++runTicks % 10 === 0) loadAds();
    return;
  }
  clearInterval(runPoll);
  runPoll = null;
  runTicks = 0;
  toast("Passage terminé");
  loadAds();
}
function followRun() {
  if (!runPoll) runPoll = setInterval(pollProgress, 1500);
}

async function loadAds() {
  try {
    const serverStatus = status === "fav" || status === "hidden" ? "" : status;  // favoris / masquées : tri local
    // Favoris : gardés indéfiniment, donc affichés quelle que soit la période choisie (lus à part en base,
    // pour ne pas être noyés sous les annonces récentes).
    const only = { fav: "favorite", hidden: "hidden" }[status] || "";
    const days = status === "fav" ? 3650 : $("#f-days").value;
    const data = await api(`/api/ads?status=${encodeURIComponent(serverStatus)}&days=${days}&only=${only}${nearParam()}`);
    geoState = data.geo;
    allAds = data.ads;
    applyStatus(data);
    if (data.running && !searchPoll) followRun();  // la recherche ponctuelle a son propre suivi
    renderSiteChips(new Set(allAds.map((a) => a.source).filter(Boolean)));
    fillSelect("#f-category", new Set(allAds.map((a) => a.category).filter(Boolean)));
    renderAds(true);  // rafraîchissement : on reste sur la même page
  } catch (e) {
    $("#ads").innerHTML = `<div class="empty">Impossible de charger les annonces : ${esc(e.message)}</div>`;
  }
}

function filteredAds(source = allAds) {
  const text = $("#f-text").value.trim().toLowerCase();
  const category = $("#f-category").value, type = $("#f-type").value;
  const minScore = Number($("#f-score").value || 0);
  const minEase = Number($("#f-ease").value || 0);
  const minSale = Number($("#f-sale").value || 0);
  const maxPrice = $("#f-price").value === "" ? null : Number($("#f-price").value);
  const minMargin = $("#f-margin").value === "" ? null : Number($("#f-margin").value);
  const maxDistance = $("#f-distance").value === "" ? null : Number($("#f-distance").value);
  const deliveryMode = $("#f-delivery").value;
  const sellerType = $("#f-seller").value;

  const ads = source.filter((ad) => {
    const a = ad.analysis || {};
    if (status === "fav" ? !ad.favorite : status === "hidden" ? !ad.hidden : ad.hidden) return false;
    if (maxDistance != null && (ad.distance_km ?? Infinity) > maxDistance) return false;
    if (deliveryMode && delivery(ad).mode !== deliveryMode) return false;
    if (sellerType && ad.seller?.type && ad.seller.type !== sellerType) return false;  // type inconnu : gardée
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
  const pref = (ad) => Math.max(-3, Math.min(3, preferences[ad.category] || 0));  // goût appris, borné
  const reco = (ad) => (ad.score ?? 0) + 0.6 * pref(ad);  // note ajustée par vos favoris / masquées
  const sorters = {
    reco: (x, y) => reco(y) - reco(x) || margin(y) - margin(x),
    recent: (x, y) => (y.seen_at || "").localeCompare(x.seen_at || ""),
    score: (x, y) => (y.score ?? -1) - (x.score ?? -1),
    margin: (x, y) => margin(y) - margin(x),
    sale: (x, y) => ((y.analysis || {}).facilite_vente ?? -1) - ((x.analysis || {}).facilite_vente ?? -1) || margin(y) - margin(x),
    price: (x, y) => (x.total_price ?? 1e9) - (y.total_price ?? 1e9),
    distance: (x, y) => (x.distance_km ?? 1e9) - (y.distance_km ?? 1e9),
  };
  const order = sorters[$("#f-sort").value] || sorters.recent;
  // Annonces vérifiées « plus en ligne » toujours en bas, quel que soit le tri (vendues : rien à acheter).
  return ads.sort((x, y) => (!!x.gone - !!y.gone) || order(x, y));
}

// Les 3 notes, sous les prix : rentabilité (note IA), facilité de réparation, facilité de vente.
function scoreRow(ad) {
  const a = ad.analysis || {};
  const level = (v) => (v == null ? "none" : v >= 7 ? "good" : v >= 5 ? "mid" : "low");
  const cell = (value, icon, label, title) => `<div class="sc ${level(value)}" title="${title}">
      <span>${icon} ${label}</span><b>${value == null ? "—" : `${value}<small>/10</small>`}</b></div>`;
  const repair = a.type_affaire === "revente_directe" ? null : a.facilite_reparation;
  return `<div class="scores">
    ${cell(ad.score, "⭐", "Note", "Note de rentabilité donnée par l'IA")}
    ${cell(repair, "🔧", "Réparation", a.type_affaire === "revente_directe" ? "Revente directe : rien à réparer" : "Facilité de réparation (10 = rien ou presque à faire)")}
    ${cell(a.facilite_vente, "🔥", "Vente", "Facilité de vente (10 = très recherché, part en quelques jours)")}
  </div>`;
}

// « Laon 02000 02000 » → « Laon 02000 »
const cleanPlace = (place) => String(place || "").split(/\s+/).filter((w, i, all) => all.indexOf(w) === i).join(" ");

// Mode de remise : eBay fournit les frais de port (envoi possible) ; sinon on ne sait pas (souvent sur place).
function delivery(ad) {
  if (ad.shipping != null) return { mode: "ship", icon: "🚚", label: ad.shipping > 0 ? `Envoi ${euros(ad.shipping)}` : "Envoi possible" };
  // Vinted : envoi toujours possible, mais le prix du port n'est pas donné dans la liste.
  if (ad.shippable || ad.source === "vinted") return { mode: "ship", icon: "🚚", label: "Envoi possible (port en plus)" };
  return { mode: "pickup", icon: "🤝", label: "Remise en main propre" };
}

// Signaux de risque calculés localement (gratuit) : vendeur, prix trop beau, enchère, réparation difficile.
function riskFlags(ad) {
  const a = ad.analysis || {}, s = ad.seller || {}, flags = [];
  if (s.positive_pct != null && s.positive_pct < 95) flags.push(`Vendeur ${String(s.positive_pct).replace(".", ",")} % d'avis positifs`);
  if (s.reviews != null && s.reviews < 5) flags.push("Vendeur avec très peu d'évaluations");
  if (ad.market && ad.market.median && ad.total_price != null && ad.total_price < ad.market.median * 0.4)
    flags.push("Prix très bas vs la cote — vérifier que ce n'est pas une arnaque");
  if (ad.price_mode === "enchere") flags.push("Enchère : le prix final peut grimper");
  if ((ad.signals || []).includes("livraison à vérifier")) flags.push("Livraison non précisée sur Facebook : demandez au vendeur s'il envoie");
  if (a.facilite_reparation != null && a.facilite_reparation <= 3) flags.push("Réparation difficile (soudure / panne incertaine)");
  return flags;
}

function adCard(ad) {
  const a = ad.analysis || {};
  const image = ad.image
    ? `<img src="${esc(ad.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'placeholder',textContent:'📦'}))">`
    : `<span class="placeholder">📦</span>`;
  const buy = euros(ad.total_price ?? ad.price);
  const marginValue = a.marge_nette_estimee;
  const margin = marginValue == null ? "—" : `${marginValue >= 0 ? "+" : ""}${euros(marginValue)}`;
  const marginClass = marginValue == null ? "" : marginValue >= 0 ? "pos" : "neg";

  return `<article class="ad ${ad.status} ${ad.gone ? "gone" : ""}" data-uid="${esc(ad.uid)}" ${ad.image == null ? "data-need-image" : ""}>
    <a class="thumb" href="${esc(ad.url)}" target="_blank" rel="noopener">${image}</a>
    <div class="body">
      <a class="title" href="${esc(ad.url)}" target="_blank" rel="noopener">${ad.gone ? `<span class="gone-badge" title="Vérifiée le ${esc(frDate(ad.checked_at))} : vendue ou supprimée">❌ Plus en ligne</span> ` : ""}${ad.watched ? '<span class="watch-badge" title="Correspond à une veille">🔭</span> ' : ""}${brandOf(ad) ? `<span class="brand-badge" title="Marque recherchée : ${esc(brandOf(ad))}">⭐</span> ` : ""}${ad.price_drop ? `<span class="drop-badge" title="Prix passé de ${esc(euros(ad.price_drop.from))} à ${esc(euros(ad.price_drop.to))}">⬇ −${esc(euros(ad.price_drop.from - ad.price_drop.to))}</span> ` : ""}${esc(ad.title)}</a>
      <div class="stats">
        <div><span>Achat</span><b>${buy}</b></div>
        <div title="${a.source_revente === "cote" ? "D'après la cote réelle du modèle (prix des annonces en état de marche)" : "Estimation de l'IA, rabotée par prudence"}">
          <span>Revente${a.source_revente === "cote" ? ' <i class="src">cote</i>' : a.source_revente === "analyse approfondie" ? ' <i class="src">vérifiée</i>' : a.source_revente === "repère" ? ' <i class="src">repère</i>' : ""}</span><b>${a.valeur_revente_estimee != null ? euros(a.valeur_revente_estimee) : "—"}</b></div>
        <div class="${marginClass}"><span>Marge</span><b>${margin}</b></div>
      </div>
      ${ad.analysis ? scoreRow(ad) : ""}
      ${ad.reason ? `<div class="reason">✗ ${esc(ad.reason)}</div>` : ""}
      ${(() => { const r = riskFlags(ad); return r.length ? `<div class="risk" title="${esc(r.join(" · "))}">⚠️ ${r.length} point${r.length > 1 ? "s" : ""} de vigilance</div>` : ""; })()}
      <div class="meta">
        <div class="site-line">${ad.distance_km != null ? `📍 ${ad.distance_km} km · ` : ""}<span title="${esc(delivery(ad).label)}">${delivery(ad).icon}</span> ${ad.seller?.type === "pro" ? '<span class="pro-badge" title="Vendeur professionnel">PRO</span> ' : ""}${siteLogo(ad.source, 14)} ${esc(siteName(ad.source))}${ad.category ? ` · ${esc(ad.category)}` : ""}</div>
        <div class="card-actions">
          <button class="mini ${ad.favorite ? "on" : ""}" data-fav="${esc(ad.uid)}" title="${ad.favorite ? "Retirer des favoris" : "Mettre en favori"}">${ad.favorite ? "★" : "☆"}</button>
          <button class="mini" data-hide="${esc(ad.uid)}" title="${ad.hidden ? "Ne plus masquer" : "Masquer cette annonce"}">${ad.hidden ? "↺" : "⊘"}</button>
          <button class="info" data-info="${esc(ad.uid)}" title="Détails, estimation, vendeur, analyse approfondie">i</button>
        </div>
      </div>
    </div>
  </article>`;
}

// ---------- Détails d'une annonce (bouton « i ») ----------
const faultText = (a) => (!a.panne_probable ? "" : a.panne_probable.toLowerCase() === "aucune"
  ? "Aucune — revente en l'état" : a.panne_probable);

// Vendeur : ce que le site fournit (eBay : note + évaluations ; leboncoin : nom + particulier / pro).
function sellerRows(seller, row) {
  const s = seller || {};
  const who = [s.name, s.type === "pro" ? "Professionnel" : s.type === "particulier" ? "Particulier" : ""].filter(Boolean).join(" · ");
  const rating = s.positive_pct != null ? `${String(s.positive_pct).replace(".", ",")} % d'avis positifs` : "";
  const sales = s.reviews != null ? `${Number(s.reviews).toLocaleString("fr-FR")} (≈ nombre de ventes)` : "";
  if (!who && !rating && !sales) return row("👤 Vendeur", '<span class="muted">non fourni par ce site</span>');
  return row("👤 Vendeur", esc(who)) + row("⭐ Note du vendeur", esc(rating)) + row("🧾 Évaluations", esc(sales))
    + row("📅 Membre depuis", esc(s.since ? String(s.since).slice(0, 7) : ""));
}

function showDetails(uid) {
  const ad = allAds.find((x) => x.uid === uid);
  if (!ad) return;
  const a = ad.analysis || {};
  const row = (label, value) => (value ? `<div class="d-row"><span>${label}</span><b>${value}</b></div>` : "");
  const card = (title, rows) => (rows.trim() ? `<section class="d-card"><h4>${title}</h4>${rows}</section>` : "");
  const seen = ad.seen_at ? new Date(ad.seen_at.endsWith("Z") || ad.seen_at.includes("+") ? ad.seen_at : ad.seen_at + "Z") : null;
  const desc = String(ad.description || "").trim();
  const margin = a.marge_nette_estimee;
  const tile = (label, value, cls = "", sub = "") =>
    `<div class="d-tile ${cls}"><span>${label}</span><b>${value}</b>${sub ? `<small>${sub}</small>` : ""}</div>`;
  const risks = riskFlags(ad);
  const score = ad.score != null ? `<span class="d-score s${Math.round(ad.score)}">${ad.score}/10</span>` : "";

  $("#details-body").innerHTML = `
    <header class="d-head">
      <div class="d-badges">${siteLogo(ad.source, 16)} <span>${esc(siteName(ad.source))}</span>
        ${ad.category ? `<span class="d-pill">${esc(ad.category)}</span>` : ""}
        ${a.type_affaire ? `<span class="d-pill">${a.type_affaire === "revente_directe" ? "Revente directe" : "À réparer"}</span>` : ""}
        ${brandOf(ad) ? `<span class="d-pill gold">⭐ ${esc(brandOf(ad))}</span>` : ""}
        ${ad.watched ? '<span class="d-pill">🔭 Veille</span>' : ""}</div>
      <h3>${esc(ad.title)} ${score}</h3>
    </header>
    <div class="d-layout">
      <aside class="d-side">
        ${ad.image ? `<img class="d-photo" src="${esc(ad.image)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()">`
          : '<div class="d-photo empty">📦</div>'}
        <div class="d-tiles">
          ${tile("Achat", euros(ad.total_price ?? ad.price), "", ad.shipping ? `dont ${euros(ad.shipping)} de port` : ad.price_mode === "enchere" ? "enchère en cours" : "")}
          ${tile("Revente", a.valeur_revente_estimee != null ? euros(a.valeur_revente_estimee) : "—", "", a.source_revente === "cote" ? "d'après la cote" : a.source_revente === "analyse approfondie" ? "analyse approfondie" : a.source_revente === "repère" ? "prix de référence −10 %" : "prudence −10 %")}
          ${tile("Marge nette", margin == null ? "—" : `${margin >= 0 ? "+" : ""}${euros(margin)}`, margin == null ? "" : margin >= 0 ? "pos" : "neg")}
          ${tile("Notes", `${a.facilite_reparation ?? "—"} · ${a.facilite_vente ?? "—"}`, "", "réparation · vente /10")}
        </div>
        <div class="d-actions">
          <button class="ghost" data-fav="${esc(ad.uid)}">${ad.favorite ? "★ En favori" : "☆ Favori"}</button>
          <button class="ghost" data-buy="${esc(ad.uid)}">🛒 Acheté</button>
          <button class="ghost" data-nego="${esc(ad.uid)}" title="Préparer et copier un message de négociation">💬 Négocier</button>
        </div>
        <a class="primary d-open" href="${esc(ad.url)}" target="_blank" rel="noopener">Voir l'annonce ↗</a>
      </aside>
      <div class="d-main">
        ${risks.length ? `<div class="d-risk"><b>⚠️ Points de vigilance</b><ul>${risks.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}
        <div class="d-cards">
          ${card("🧮 Estimation", `
            ${row("🩺 Panne probable", esc(faultText(a)))}
            ${row("🔩 Pièce estimée", a.cout_piece_estime ? euros(a.cout_piece_estime) : "")}
            ${row("📊 Cote du marché", ad.market ? `${euros(ad.market.median)} <span class="muted">(${ad.market.count} ${ad.market.source === "ebay" ? "occasions eBay" : "annonces"})</span>` : "")}
            ${row("🤖 Avis brut de l'IA", a.valeur_revente_ia != null ? euros(a.valeur_revente_ia) : "")}
            ${ad.price_drop ? row("⬇ Baisse de prix", `${euros(ad.price_drop.from)} → ${euros(ad.price_drop.to)}`) : ""}
            ${row("✨ Signaux", esc((ad.signals || []).join(", ")))}
            ${row("✗ Écartée", esc(ad.reason || ""))}`)}
          ${card("📍 Annonce", `
            ${row("Ville", esc(cleanPlace(ad.location)) + (ad.distance_km != null ? ` <span class="muted">· ${ad.distance_km} km</span>` : ""))}
            ${row("Remise", `${delivery(ad).icon} ${delivery(ad).label}`)}
            ${row("Vente", ad.price_mode === "enchere" ? "Enchère (prix provisoire)" : "Prix fixe")}
            ${row("En ligne ?", ad.checked_at ? (ad.gone ? `❌ disparue <span class="muted">(vérifié le ${esc(frDate(ad.checked_at))})</span>` : `✅ oui <span class="muted">(vérifié le ${esc(frDate(ad.checked_at))})</span>`) : "")}
            ${row("Repérée", seen && !isNaN(seen) ? seen.toLocaleString("fr-FR", { dateStyle: "medium", timeStyle: "short" }) : "")}`)}
          ${card("👤 Vendeur", sellerRows(ad.seller, row))}
        </div>
        <div id="d-nego-box">${ad._nego ? negotiationBox(ad.uid, ad._nego.message, ad._nego.prix_achat_max, ad.total_price ?? ad.price) : ""}</div>
        ${desc ? `<details class="d-desc"><summary>Description du vendeur</summary><p>${esc(desc.slice(0, 1500))}${desc.length > 1500 ? "…" : ""}</p></details>` : ""}
      </div>
    </div>
    ${deepBlock(ad)}`;
  if (!$("#details").open) $("#details").showModal();
  if (ad.market_deep) loadHistory(ad);
}

// Cote du modèle dans le temps (prix déjà en base, gratuit) : affichée dans l'analyse approfondie.
function loadHistory(ad) {
  const put = () => { const slot = $("#d-history"); if (slot) slot.innerHTML = ad.history_chart || ""; };
  if (ad.history_chart !== undefined) return put();
  api(`/api/ads/${encodeURIComponent(ad.uid)}/history`).then((data) => {
    ad.history_chart = historyChart(data.history) || "";
    put();
  }).catch(() => { ad.history_chart = ""; });
}

// Analyse approfondie (gros modèle + recherche web) : tout le « payant » est ici, à la demande —
// prix réels, graphique des ventes, verdict, message de négociation ; questions et liens de réparation à côté.
const CONF_LEVEL = { faible: "low", moyenne: "mid", "élevée": "good" };

// Verdict final de l'analyse approfondie, calculé ici (gratuit) : prix demandé vs prix d'achat maximum.
function deepVerdict(ad, d) {
  const price = ad.total_price ?? ad.price, max = d.prix_achat_max;
  const doubt = d.confiance === "faible" ? " Confiance faible : vérifiez avant." : "";
  if (max == null || price == null) return { cls: "maybe", icon: "🤔", title: "À vérifier", text: "Prix ou fourchette inconnus." };
  if (max <= 0) return { cls: "pass", icon: "⛔", title: "Ne pas acheter", text: `Même très bas, la revente ne laisse pas +${d.marge_visee ?? 30} € net.${doubt}` };
  if (ad.price_mode === "enchere") return { cls: "buy", icon: "🎯", title: `Enchérir jusqu'à ${euros(max)}`, text: `Au-delà, la marge visée n'est plus atteinte.${doubt}` };
  if (price <= max) return { cls: "buy", icon: "✅", title: "À acheter", text: `${euros(price)} demandés, maximum conseillé ${euros(max)} : la marge est là.${doubt}` };
  if (price <= max * 1.25) return { cls: "nego", icon: "🤝", title: `À négocier : proposez ${euros(max)}`, text: `${euros(price)} demandés, soit ${Math.round(100 * (1 - max / price))} % de trop.${doubt}` };
  return { cls: "pass", icon: "❌", title: "À laisser passer", text: `${euros(price)} demandés pour un maximum conseillé de ${euros(max)}.${doubt}` };
}

// Coût indicatif de l'analyse approfondie : moyenne des analyses déjà faites (tokens + recherches web).
function deepCostHint() {
  const costs = allAds.map((a) => a.market_deep?.cost_usd).filter((c) => c > 0);
  if (!costs.length) return "quelques centimes";
  const avg = costs.reduce((x, y) => x + y, 0) / costs.length;
  return `≈ ${avg.toLocaleString("fr-FR", { maximumFractionDigits: 2, minimumFractionDigits: 2 })} $`;
}

function deepBlock(ad) {
  const d = ad.market_deep;
  if (!d) {
    return `<section class="d-deep">
      <div class="deep-cta">
        <div><b>🔬 Analyse approfondie</b><p class="muted small">Prix réels vérifiés sur le web, photo examinée, graphique des ventes et verdict final.</p></div>
        <button class="deep-btn" data-deep="${esc(ad.uid)}"><span>Analyser</span><small>${deepCostHint()} · 20 s</small></button>
      </div></section>`;
  }
  const v = deepVerdict(ad, d);
  const range = [d.revente_basse, d.revente_haute].every((x) => x != null) ? `${euros(d.revente_basse)} – ${euros(d.revente_haute)}` : "—";
  const questions = (d.questions || []).length
    ? `<div class="d-questions"><b>💬 À demander au vendeur</b><ul>${d.questions.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`
    : `<button class="ghost" data-questions="${esc(ad.uid)}">💬 Questions à poser au vendeur <small class="muted">≈ 0,001 $</small></button>`;
  return `<section class="d-deep done">
    <div class="verdict ${v.cls}"><span class="v-icon">${v.icon}</span>
      <div><b>${esc(v.title)}</b><p>${esc(v.text)}</p></div>
      <div class="deep-meta"><span class="conf ${CONF_LEVEL[d.confiance] || "mid"}">confiance ${esc(d.confiance)}</span>
        <span>${d.cost_usd ? iaCost(d.cost_usd) : ""}</span><button class="link" data-deep="${esc(ad.uid)}" title="Refaire l'analyse">↻</button></div>
    </div>
    <div class="deep-grid">
      <div class="deep-facts">
        <div class="d-tiles three">
          <div class="d-tile"><span>Revente réelle</span><b>${range}</b>${d.revente_typique != null ? `<small>typique ${euros(d.revente_typique)}</small>` : ""}</div>
          <div class="d-tile ${d.prix_achat_max != null && d.prix_achat_max <= 0 ? "neg" : ""}"><span>Achat max</span><b>${d.prix_achat_max != null ? euros(d.prix_achat_max) : "—"}</b><small>pour +${d.marge_visee ?? 30} € net</small></div>
          <div class="d-tile"><span>Demandé</span><b>${euros(ad.total_price ?? ad.price)}</b><small>${ad.shipping ? "port compris" : "&nbsp;"}</small></div>
        </div>
        ${d.resume ? `<p class="deep-summary">${esc(d.resume)}</p>` : ""}
        ${d.pieges ? `<p class="deep-warn">⚠️ ${esc(d.pieges)}</p>` : ""}
        ${photoBlock(d)}
        ${(d.sources || []).length ? `<div class="deep-sources">${d.sources.map((x) => `<span>${esc(x)}</span>`).join("")}</div>` : ""}
      </div>
      <div class="deep-charts">${salesChart(d) || '<div class="chart-empty">Pas de vente datée trouvée pour le graphique</div>'}<div id="d-history"></div></div>
    </div>
    ${d.message_negociation && !ad._nego ? negotiationBox(ad.uid, d.message_negociation, d.prix_achat_max, ad.total_price ?? ad.price) : ""}
    <div class="deep-actions">${questions}${repairLinks(ad)}</div>
  </section>`;
}

// Message de négociation modifiable, prêt à copier (bouton « Négocier » ou analyse approfondie).
function negotiationBox(uid, message, maxBuy, asked) {
  // Prix demandé déjà sous le maximum : rien à négocier, ne pas suggérer un prix plus haut que l'annonce.
  const already = maxBuy != null && maxBuy > 0 && asked != null && asked <= maxBuy;
  const target = already ? ` <span class="muted small">(prix demandé déjà sous l'achat max de ${euros(maxBuy)})</span>`
    : maxBuy != null && maxBuy > 0 ? ` <span class="muted small">(prix conseillé ≤ ${euros(maxBuy)})</span>` : "";
  return `<div class="nego-box">
    <div class="nego-head"><b>✉️ Message de négociation${target}</b>
      <button type="button" class="ghost" data-copy-nego="${esc(uid)}">📋 Copier</button></div>
    <textarea data-nego-text="${esc(uid)}" rows="3">${esc(message)}</textarea>
  </div>`;
}

async function negotiateAd(uid) {
  const ad = allAds.find((x) => x.uid === uid);
  if (!ad) return;
  try {
    const data = await api(`/api/ads/${encodeURIComponent(uid)}/negotiation`);
    ad._nego = data;
    showDetails(uid);
    const copied = await copyText(data.message);
    toast(copied ? "📋 Message de négociation copié !" : "✉️ Message prêt dans la fiche");
  } catch (e) {
    toast("❌ " + e.message);
  }
}

async function copyNegotiation(btn) {
  const text = btn.closest(".nego-box")?.querySelector("[data-nego-text]")?.value.trim();
  if (!text) return;
  toast(await copyText(text) ? "📋 Message copié !" : "❌ Copie impossible : sélectionnez le texte à la main");
}

// Constat de l'IA sur la photo de l'annonce (état visible, défauts, cohérence avec le titre).
const PHOTO_STATE = { bon: ["good", "bon état visible"], moyen: ["mid", "état moyen"], mauvais: ["low", "mauvais état"],
  inconnu: ["mid", "photo peu informative"] };
function photoBlock(d) {
  if (!("photo" in d)) return "";  // analyse faite avant l'ajout de la photo
  if (!d.photo) return '<p class="muted small">📷 Pas de photo analysée (annonce sans photo exploitable).</p>';
  const [cls, label] = PHOTO_STATE[d.photo.etat] || PHOTO_STATE.inconnu;
  return `<div class="deep-photo"><div><b>📷 Ce que montre la photo</b> <span class="conf ${cls}">${label}</span></div>
    ${d.photo.constat ? `<p>${esc(d.photo.constat)}</p>` : ""}
    ${(d.photo.alertes || []).length ? `<ul>${d.photo.alertes.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}</div>`;
}

async function askQuestions(uid) {
  const ad = allAds.find((x) => x.uid === uid);
  const btn = $("#details-body [data-questions]");
  if (btn) { btn.disabled = true; btn.textContent = "⏳ Préparation des questions…"; }
  try {
    const data = await api(`/api/ads/${encodeURIComponent(uid)}/questions`, { method: "POST" });
    if (ad) ad.market_deep = { ...(ad.market_deep || {}), questions: data.questions };
    showDetails(uid);
  } catch (e) {
    toast("❌ " + e.message);
    if (btn) { btn.disabled = false; btn.textContent = "💬 Réessayer"; }
  }
}

// Nuage de points « prix × date de vente » (une série : les ventes observées sur le web).
// SVG inline, couleurs par les tokens du thème (clair/sombre), survol natif par point.
// Graphique commun : nuage de points « prix × date » (+ bande/typique et ligne optionnels).
function pointsChart(points, opts) {
  const pts = (points || []).filter((v) => v.prix != null && /^\d{4}-\d{2}/.test(v.date || ""));
  if (pts.length < 2) return "";
  const { caption = "", band = null, line = false, aria = caption, fmt = (v) => `${Math.round(v)} €`,
          dateFmt = { month: "short", year: "2-digit" } } = opts || {};
  const W = 320, H = 150, L = 38, R = 10, T = 10, B = 24;
  const toMs = (v) => Date.UTC(+v.date.slice(0, 4), +v.date.slice(5, 7) - 1, +(v.date.slice(8, 10) || 15));
  const xs = pts.map(toMs);
  const ys = pts.map((v) => v.prix).concat(band ? [band.low, band.high].filter((x) => x != null) : []);
  let [xMin, xMax] = [Math.min(...xs), Math.max(...xs)];
  if (xMin === xMax) { xMin -= 2.6e9; xMax += 2.6e9; }
  let [yMin, yMax] = [Math.min(...ys), Math.max(...ys)];
  const pad = (yMax - yMin) * 0.12 || 10; yMin = Math.max(0, yMin - pad); yMax += pad;
  const px = (ms) => L + ((ms - xMin) / (xMax - xMin || 1)) * (W - L - R);
  const py = (v) => T + (1 - (v - yMin) / (yMax - yMin || 1)) * (H - T - B);
  const euro = fmt;
  const moisAn = (ms) => new Date(ms).toLocaleDateString("fr-FR", dateFmt);

  let bandSvg = "";
  if (band && band.low != null && band.high != null) {
    bandSvg = `<rect class="chart-band" x="${L}" y="${py(band.high).toFixed(1)}" width="${W - L - R}"
      height="${(py(band.low) - py(band.high)).toFixed(1)}"/>`;
    if (band.typical != null) {
      bandSvg += `<line class="chart-line" x1="${L}" y1="${py(band.typical).toFixed(1)}" x2="${W - R}" y2="${py(band.typical).toFixed(1)}"/>`;
    }
  }
  let grid = "";
  for (let i = 0; i <= 2; i++) {
    const v = yMin + (i / 2) * (yMax - yMin), y = py(v).toFixed(1);
    grid += `<line class="chart-grid" x1="${L}" y1="${y}" x2="${W - R}" y2="${y}"/>
      <text class="chart-lbl" x="${L - 6}" y="${y}" text-anchor="end" dominant-baseline="middle">${euro(v)}</text>`;
  }
  const anchors = ["start", "middle", "end"];
  const xticks = [xMin, (xMin + xMax) / 2, xMax].map((ms, i) =>
    `<text class="chart-lbl" x="${px(ms).toFixed(1)}" y="${H - 7}" text-anchor="${anchors[i]}">${moisAn(ms)}</text>`).join("");
  const sorted = [...pts].sort((a2, b2) => toMs(a2) - toMs(b2));
  const polyline = line ? `<polyline class="chart-trend" points="${sorted.map((v) => `${px(toMs(v)).toFixed(1)},${py(v.prix).toFixed(1)}`).join(" ")}"/>` : "";
  const dots = pts.map((v) => `<circle class="chart-dot" cx="${px(toMs(v)).toFixed(1)}" cy="${py(v.prix).toFixed(1)}" r="4.5">
      <title>${moisAn(toMs(v))} · ${euro(v.prix)}${v.source ? ` · ${esc(v.source)}` : ""}</title></circle>`).join("");

  return `<figure class="deep-chart">
    <figcaption>${esc(caption)}</figcaption>
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(aria)}">${bandSvg}${grid}${xticks}${polyline}${dots}</svg>
  </figure>`;
}

function salesChart(d) {
  return pointsChart(d.ventes, {
    caption: `Ventes observées (${(d.ventes || []).length}) — prix réel par date`,
    band: { low: d.revente_basse, high: d.revente_haute, typical: d.revente_typique },
    aria: "Nuage de points des prix de vente par date",
  });
}

function historyChart(history) {
  return pointsChart(history, {
    caption: "Cote du modèle dans le temps (prix médian des annonces vues, par mois)",
    line: true, aria: "Évolution de la cote du modèle par mois",
  });
}

async function deepMarket(uid) {
  const ad = allAds.find((x) => x.uid === uid);
  if (!ad) return;
  const btn = $("#details-body").querySelector(`[data-deep]`);
  if (btn) { btn.disabled = true; btn.classList.add("loading"); btn.innerHTML = "<span>⏳ Analyse…</span><small>prix réels sur le web</small>"; }
  try {
    const data = await api(`/api/ads/${encodeURIComponent(uid)}/market-analysis`, { method: "POST" });
    ad.market_deep = data.market_deep;
    if (data.analysis) {  // la carte reprend la revente, la marge et la note du verdict
      ad.analysis = data.analysis;
      ad.score = data.analysis.score_rentabilite;
    }
    ad._nego = null;  // le message de l'analyse remplace celui préparé avant
    showDetails(uid);
    renderAds(true);
  } catch (e) {
    toast("❌ " + e.message);
    if (btn) { btn.disabled = false; btn.classList.remove("loading"); btn.innerHTML = "<span>↻ Réessayer</span>"; }
  }
}

function repairLinks(ad) {
  const a = ad.analysis || {};
  if (a.type_affaire === "revente_directe") return "";
  const model = (ad.market && ad.market.model) || ad.title.split(/[-–|,(]/)[0].trim().split(/\s+/).slice(0, 4).join(" ");
  const fault = String(a.panne_probable || "").toLowerCase();
  const part = ["batterie", "écran", "ecran", "clavier", "ssd", "chargeur", "ventilateur", "charnière", "nappe", "alimentation",
    "carte", "port", "moteur", "dalle"].find((w) => fault.includes(w)) || "";
  const q = encodeURIComponent(`${model} ${part}`.trim());
  return `<div class="d-links"><b>🔧 Réparer</b>
    <a href="https://www.ifixit.com/Search?query=${q}" target="_blank" rel="noopener">iFixit</a>
    <a href="https://www.ebay.fr/sch/i.html?_nkw=${q}" target="_blank" rel="noopener">Pièce eBay</a>
    <a href="https://fr.aliexpress.com/w/wholesale-${q}.html" target="_blank" rel="noopener">AliExpress</a></div>`;
}

async function markAd(uid, change) {
  const ad = allAds.find((x) => x.uid === uid);
  if (!ad) return;
  try {
    await api(`/api/ads/${encodeURIComponent(uid)}/mark`, { method: "POST", body: JSON.stringify(change) });
    Object.assign(ad, change);
    api("/api/preferences").then((d) => { preferences = d.categories || {}; }).catch(() => {});
    renderAds(true);
    if ($("#details").open) showDetails(uid);
    if (change.hidden) toast("Annonce masquée (voir « Masquées » pour la retrouver)");
  } catch (e) { toast("❌ " + e.message); }
}

async function buyAd(uid) {
  const ad = allAds.find((x) => x.uid === uid);
  const price = prompt("Prix payé (€) ?", ad ? (ad.total_price ?? ad.price ?? "") : "");
  if (price === null) return;
  try {
    await api("/api/purchases", { method: "POST", body: JSON.stringify({ uid, buy_price: price }) });
    toast("🛒 Ajouté à l'onglet Achats");
  } catch (e) { toast("❌ " + e.message); }
}

function onAdAction(e) {
  const t = e.target.closest("[data-info],[data-fav],[data-hide],[data-buy],[data-deep],[data-questions],[data-nego],[data-copy-nego]");
  if (!t) return;
  if (t.dataset.info) showDetails(t.dataset.info);
  else if (t.dataset.fav) markAd(t.dataset.fav, { favorite: !allAds.find((x) => x.uid === t.dataset.fav)?.favorite });
  else if (t.dataset.hide) markAd(t.dataset.hide, { hidden: !allAds.find((x) => x.uid === t.dataset.hide)?.hidden });
  else if (t.dataset.buy) buyAd(t.dataset.buy);
  else if (t.dataset.deep) deepMarket(t.dataset.deep);
  else if (t.dataset.questions) askQuestions(t.dataset.questions);
  else if (t.dataset.nego) negotiateAd(t.dataset.nego);
  else if (t.dataset.copyNego) copyNegotiation(t);
}
$("#ads").addEventListener("click", onAdAction);
$("#details-body").addEventListener("click", onAdAction);
$("#details").addEventListener("click", (e) => {
  // Fermeture : bouton ✕ ou clic à côté de la fenêtre (Échap fonctionne d'office).
  if (e.target.closest("[data-close-details]") || e.target === $("#details")) $("#details").close();
});

// ---------- Pages de 20 annonces ----------
const PAGE_SIZE = 20;
let page = 1;

function renderAds(keepPage = false) {
  const ads = filteredAds();
  const pages = Math.max(1, Math.ceil(ads.length / PAGE_SIZE));
  page = keepPage ? Math.min(page, pages) : 1;  // un filtre qui change ramène à la page 1
  const first = (page - 1) * PAGE_SIZE;
  const geoNote = geoState === "chargement" ? " · distances en cours de préparation…"
    : geoState === "erreur" ? " · distances indisponibles (pas de connexion à geo.api.gouv.fr)" : "";
  $("#summary").textContent = (ads.length
    ? `${ads.length} annonce(s) sur ${allAds.length} · ${first + 1}–${Math.min(first + PAGE_SIZE, ads.length)} affichées`
    : `0 annonce sur ${allAds.length}`) + geoNote;
  $("#ads").innerHTML = ads.length
    ? ads.slice(first, first + PAGE_SIZE).map(adCard).join("")
    : allAds.length
      ? '<div class="empty">Aucune annonce ne correspond à ces filtres. <button class="link" data-reset-filters>Réinitialiser les filtres</button></div>'
      : '<div class="empty"><b>Aucune annonce pour l\'instant.</b><br>Cliquez sur <b>▶ Lancer un passage</b> en haut à droite pour lancer une première recherche.</div>';
  renderPager(pages);
  loadMissingImages();
  if (searchResults.length) renderSearchResults();  // mêmes filtres sur les résultats de la recherche ponctuelle
}

// Annonces sans photo : on demande l'aperçu de leur lien (une à la fois, mémorisé côté serveur).
let imageQueue = Promise.resolve();
function loadMissingImages() { loadMissingImagesIn("#ads"); }
function loadMissingImagesIn(root) {
  document.querySelectorAll(`${root} [data-need-image]`).forEach((card) => {
    card.removeAttribute("data-need-image");
    const uid = card.dataset.uid;
    imageQueue = imageQueue.then(async () => {
      const data = await api(`/api/image/${encodeURIComponent(uid)}`).catch(() => ({}));
      const ad = allAds.find((a) => a.uid === uid);
      if (ad) ad.image = data.image || "";
      const live = document.querySelector(`${root} [data-uid="${CSS.escape(uid)}"] .thumb .placeholder`);
      if (data.image && live) {
        live.outerHTML = `<img src="${esc(data.image)}" alt="" loading="lazy" referrerpolicy="no-referrer"
          onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'placeholder',textContent:'📦'}))">`;
      }
    });
  });
}

// Minuscules sans accents, pour comparer des titres.
const normalizeFr = (s) => String(s || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");

function renderPager(pages) {
  const pager = $("#pager");
  pager.hidden = pages <= 1;
  if (pages <= 1) return;
  // Numéros autour de la page courante : 1 … 4 5 [6] 7 8 … 20
  const nums = [...new Set([1, page - 2, page - 1, page, page + 1, page + 2, pages])].filter((n) => n >= 1 && n <= pages);
  const parts = [];
  nums.forEach((n, i) => {
    if (i && n - nums[i - 1] > 1) parts.push('<span class="gap">…</span>');
    parts.push(`<button data-page="${n}" class="${n === page ? "on" : ""}" ${n === page ? 'aria-current="page"' : ""}>${n}</button>`);
  });
  pager.innerHTML = `<button data-page="${page - 1}" ${page === 1 ? "disabled" : ""}>‹ Précédente</button>
    <span class="nums">${parts.join("")}</span>
    <span class="where">Page ${page} sur ${pages}</span>
    <button data-page="${page + 1}" ${page === pages ? "disabled" : ""}>Suivante ›</button>`;
}

$("#ads").addEventListener("click", (e) => { if (e.target.matches("[data-reset-filters]")) $("#reset-filters").click(); });

$("#pager").addEventListener("click", (e) => {
  const button = e.target.closest("[data-page]");
  if (!button || button.disabled) return;
  page = Number(button.dataset.page);
  renderAds(true);
  $("#summary").scrollIntoView({ behavior: "smooth", block: "start" });
});

// Période et zone changent ce que renvoie le serveur (annonces, distances) : rechargement ;
// la zone est un champ texte, donc on attend la fin de la saisie. Le reste se filtre sur place.
let nearTimer = null;
FILTER_IDS.forEach((id) =>
  $("#" + id).addEventListener($("#" + id).tagName === "INPUT" ? "input" : "change", () => {
    saveFilters();
    if (id === "f-days") loadAds();
    else if (id === "f-near") { clearTimeout(nearTimer); nearTimer = setTimeout(loadAds, 600); }
    else renderAds();
  })
);
document.querySelectorAll("#f-status button").forEach((b) => b.addEventListener("click", () => setStatus(b.dataset.value)));
$("#reset-filters").addEventListener("click", () => {
  FILTER_IDS.forEach((id) => { const el = $("#" + id); el.value = el.tagName === "SELECT" ? el.options[0].value : ""; });
  $("#f-days").value = "7";
  selectedSites.clear();
  syncMirrors();
  setStatus("analyzed");
});

// Pendant un passage, « Lancer » devient « Annuler » (en haut et dans l'onglet Recherche).
function setRunning(on) {
  running = !!on;
  $("#run").classList.toggle("danger", running);
  $("#run").textContent = running ? "■ Annuler le passage" : "▶ Lancer un passage";
  $("#qs-run").classList.toggle("danger", running);
  $("#qs-run").textContent = running ? "■ Annuler la recherche" : "Analyser maintenant";
}
async function cancelRun() {
  $("#run").textContent = $("#qs-run").textContent = "⏳ Annulation…";
  try { await api("/api/cancel", { method: "POST" }); toast("Annulation demandée : arrêt au prochain point de contrôle"); }
  catch (e) { toast(e.message); }
}

$("#run").addEventListener("click", async () => {
  if (running) return cancelRun();
  try {
    await api("/api/run", { method: "POST" });
    toast("Passage lancé…");
    loadAds();
  } catch (e) { toast(e.message); }
});

// ---------- Recherche ponctuelle (onglet dédié) ----------

// Critères de la recherche ponctuelle tels que saisis dans l'onglet Recherche.
function quickSearchCriteria() {
  return {
    query: $("#qs-query").value.trim(), min_price: $("#qs-min").value, max_price: $("#qs-price").value,
    condition: $("#qs-condition").value, exclude: $("#qs-exclude").value, max_ads: $("#qs-max").value,
    sites: [...document.querySelectorAll("#qs-sites .chip.on")].map((b) => b.dataset.site),
    delivery: $("#qs-delivery").value, seller: $("#qs-seller").value, near: $("#qs-near").value, km: $("#qs-km").value,
  };
}

// Lance une recherche ponctuelle (un terme, ou les modèles d'une recherche enregistrée) et suit son avancement.
async function launchSearch(payload, label) {
  if (running) return toast("Un passage est déjà en cours.");
  searchQuery = label;
  searchResults = [];
  setRunning(true);
  showTab("search");
  $("#search-report").hidden = true;
  $("#search-filters").hidden = true;
  $("#search-results").innerHTML = "";
  try {
    await api("/api/search-now", { method: "POST", body: JSON.stringify(payload) });
    if (!searchPoll) searchPoll = setInterval(pollSearch, 1200);
    pollSearch();
  } catch (e) {
    toast(e.message);
    setRunning(false);
  }
}

$("#quick-search").addEventListener("submit", (e) => {
  e.preventDefault();
  if (running) return cancelRun();
  const criteria = quickSearchCriteria();
  if (criteria.query) launchSearch(criteria, criteria.query);
});

// Choix des sites de la recherche ponctuelle : ceux qui sont prêts (aucun coché = tous).
function renderSearchSites(sites) {
  const ready = sites.filter((s) => s.ok && s.name);
  $("#qs-sites").innerHTML = ready.length
    ? ready.map((s) => `<button type="button" class="chip" data-site="${esc(s.name)}">${siteLogo(s.name, 14)} ${esc(s.label)}</button>`).join("")
    : `<span class="muted">Aucun site prêt</span>`;
}
$("#qs-sites").addEventListener("click", (e) => {
  const chip = e.target.closest("[data-site]");
  if (chip) chip.classList.toggle("on");
});

async function pollSearch() {
  let data;
  try { data = await api("/api/progress"); } catch (e) { return; }
  const p = data.progress;
  applyStatus(data);  // progression animée (chrono, « buffering »), comme dans l'onglet Annonces
  if (data.running) return;
  // Terminé : compte rendu + résultats.
  if (searchPoll) { clearInterval(searchPoll); searchPoll = null; }
  $("#search-live").hidden = true;
  if (data.error) {  // ex. un autre passage tournait déjà : pas de faux « terminé »
    $("#search-report").hidden = false;
    $("#search-report").innerHTML = `<div class="panel search-report"><div class="report-head">⚠️ Recherche « ${esc(searchQuery)} » : erreur</div>
      <p class="small">${esc(data.error)}</p></div>`;
    return;
  }
  const found = await api(`/api/search-results?${nearParam().slice(1)}`).catch(() => ({ ads: [] }));
  renderSearchReport(p, found.ads || []);
  loadAds();  // l'onglet Annonces profite aussi des nouvelles notes
}

function renderSearchReport(p, ads) {
  // Toutes les annonces listées par CETTE recherche : notées (cartes) et écartées (avec la raison).
  const results = ads.filter((a) => a.status === "analyzed").sort((x, y) => (y.score ?? -1) - (x.score ?? -1));
  const rejected = ads.filter((a) => a.status === "rejected");
  const reasons = Object.entries(rejected.reduce((m, a) => {
    const r = String(a.reason || "?").replace(/\s*\(.*\)$/, "").replace(/ : .*$/, "");
    m[r] = (m[r] || 0) + 1; return m;
  }, {})).sort((x, y) => y[1] - x[1]);
  results.forEach((r) => { if (!allAds.find((a) => a.uid === r.uid)) allAds.push(r); });  // pour le « i », favoris…
  searchResults = results.map((r) => allAds.find((a) => a.uid === r.uid) || r);
  const found = ads.length;
  const kpi = (label, value) => `<div class="kpi"><span>${label}</span><b>${value}</b></div>`;
  const perSite = (p.sites || []).map((s) => `<div class="prog-row done">
      <b>${esc(s.site)}</b><span class="prog-text">${esc(s.text)}</span>
      <span class="prog-time">⏱ ${duration(s.elapsed)}</span><span class="prog-cost">${s.cost ? iaCost(s.cost) : "—"}</span><span></span></div>`).join("");
  $("#search-report").hidden = false;
  $("#search-report").innerHTML = `
    <div class="panel search-report">
      <div class="report-head">✅ Recherche « ${esc(searchQuery)} » terminée</div>
      <div class="kpis">
        ${kpi("Annonces trouvées", found || "—")}
        ${kpi("Notées", results.length)}
        ${kpi("Écartées", rejected.length)}
        ${kpi("Durée", duration(p.elapsed || 0))}
        ${kpi("Coût IA", iaCost(p.cost || 0))}
      </div>
      <div class="report-sites">${perSite || '<p class="muted small">Aucun site n\'a répondu.</p>'}</div>
      ${rejected.length ? `<details class="check-list"><summary>✗ Écartées avant l'IA (${rejected.length}) —
        ${reasons.slice(0, 4).map(([r, n]) => `${esc(r)} ${n}`).join(" · ")}</summary>
        <ul>${rejected.map((a) => `<li>${siteLogo(a.source, 14)} <a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a>
          <span class="muted small">${a.total_price != null ? euros(a.total_price) + " · " : ""}${esc(a.reason || "")}</span></li>`).join("")}</ul></details>` : ""}
    </div>`;
  renderSearchResults();
}

// Résultats de la recherche ponctuelle : filtres de l'onglet Annonces + mots exclus, en temps réel.
function renderSearchResults() {
  const excluded = $("#qs-exclude").value.split(",").map((w) => normalizeFr(w).trim()).filter(Boolean);
  const ads = filteredAds(searchResults)
    .filter((a) => !excluded.some((w) => normalizeFr(`${a.title} ${a.description || ""}`).includes(w)));
  $("#search-filters").hidden = !searchResults.length;
  // Filtres de l'onglet Annonces encore actifs : on dit lesquels cachent des résultats, avec de quoi les retirer.
  const hidden = searchResults.length - ads.length;
  const active = activeFilterLabels().concat(excluded.length ? [`mots exclus : ${excluded.join(", ")}`] : []);
  $("#search-count").innerHTML = `${ads.length} résultat(s) affiché(s) sur ${searchResults.length}`
    + (ads.length > 60 ? " · 60 premiers" : "")
    + (hidden > 0 && active.length ? ` · <b>${hidden} masqué(s) par vos filtres</b> : ${esc(active.join(" · "))}
      <button class="link" data-clear-search-filters>Tout afficher</button>` : "");
  $("#search-results").innerHTML = ads.length ? ads.slice(0, 60).map(adCard).join("")
    : searchResults.length ? '<div class="empty">Aucun résultat avec ces filtres. <button class="link" data-clear-search-filters>Tout afficher</button></div>' : "";
  loadMissingImagesIn("#search-results");
}
$("#qs-exclude").addEventListener("input", () => { if (searchResults.length) renderSearchResults(); });

// Libellés des filtres actifs (pour expliquer pourquoi des annonces sont cachées).
function activeFilterLabels() {
  const labels = [];
  FILTER_IDS.filter((id) => !["f-sort", "f-days", "f-near"].includes(id)).forEach((id) => {
    const el = $("#" + id);
    if (!el.value) return;
    const v = el.value;
    const text = {
      "f-text": `« ${v} »`, "f-score": `note ≥ ${v}`, "f-ease": `réparabilité ≥ ${v}`, "f-sale": `demande ≥ ${v}`,
      "f-price": `prix ≤ ${v} €`, "f-margin": `marge ≥ ${v} €`,
      "f-distance": `≤ ${v} km${$("#f-near").value.trim() ? ` de ${$("#f-near").value.trim()}` : ""}`,
    }[id];
    labels.push(text || el.options[el.selectedIndex].text.trim());  // listes : le libellé choisi
  });
  if (selectedSites.size) labels.push(`sites : ${[...selectedSites].map(siteName).join(", ")}`);
  if (status === "fav" || status === "hidden") labels.push(status === "fav" ? "favoris seulement" : "masquées seulement");
  return labels;
}
document.addEventListener("click", (e) => {
  if (!e.target.closest("[data-clear-search-filters]")) return;
  $("#qs-exclude").value = "";
  $("#reset-filters").click();  // remet aussi les champs reliés de la recherche ; réaffiche les résultats
});
$("#search-results").addEventListener("click", onAdAction);

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
  document.querySelectorAll("#saved-list [data-saved]").forEach(savedHint);  // nombre de modèles à jour
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

// Recherches par ordre alphabétique (puis par état : pour pièces, occasion…).
const sortSearches = (rows) => [...rows].sort((x, y) =>
  String(x.query || "").localeCompare(String(y.query || ""), "fr", { sensitivity: "base" })
  || String(x.condition || "").localeCompare(String(y.condition || "")));

function searchTable(key, rows) {
  const fields = SEARCH_FIELDS[key] || SEARCH_FIELDS.default;
  const label = (f) => (key === "common" && f === "condition" ? "État (eBay)" : FIELD_LABELS[f] || f);
  return `<div class="site-body" style="--cols: ${fields.map((f) => (f === "query" ? "minmax(0, 2fr)" : f === "max_price" ? "120px" : "minmax(0, 1fr)")).join(" ")} 38px">
        <div class="search-row head">${fields.map((f) => `<span>${label(f)}</span>`).join("")}<span></span></div>
        <div class="search-list">${sortSearches(rows).map((r) => searchRow(key, r)).join("")}</div>
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
  renderWatchlist();
  renderSaved();
  const f = settings.filters, n = settings.notifier, a = settings.analyzer, l = settings.location;
  setTags("#g-exclude", f.global_exclude_keywords);
  setTags("#g-signals", f.deal_signals);
  setTags("#g-brands", f.desirable_brands);
  setTags("#g-buyer", f.buyer_ad_keywords);
  setTags("#g-sold-title", f.sold_title_keywords);
  setTags("#g-sold-desc", f.sold_phrases);
  $("#g-minprice").value = f.min_price ?? "";
  $("#g-france").checked = l.france_only !== false;
  $("#g-home").value = l.home || "";
  $("#g-overseas").checked = l.exclude_overseas !== false;
  $("#g-uncat").checked = f.drop_uncategorized !== false;
  $("#g-noprice").checked = f.keep_unknown_price !== false;

  $("#n-min").value = n.min_score ?? 7;
  $("#n-instant").value = n.instant_min_score ?? 8;
  $("#n-enabled").checked = n.enabled !== false;
  $("#n-watch-on").checked = n.watch_score != null;
  $("#n-watch").value = n.watch_score ?? 5;
  $("#n-watch-row").classList.toggle("disabled", n.watch_score == null);
  $("#n-margin").value = n.min_margin ?? "";
  $("#n-ease").value = n.min_ease ?? "";
  $("#n-sale").value = n.min_sale ?? "";
  const types = n.deal_types || ["reparation", "revente_directe"];
  $("#n-repair").checked = types.includes("reparation");
  $("#n-direct").checked = types.includes("revente_directe");

  const model = a.model || "claude-haiku-5-5";
  if (!document.querySelector(`[name=a-model][value="${CSS.escape(model)}"]`)) {
    $("#a-model").insertAdjacentHTML("beforeend",
      `<label class="tile"><input type="radio" name="a-model" value="${esc(model)}"><span>🤖</span><b>${esc(model)}</b><small>défini dans config.yaml</small></label>`);
  }
  document.querySelector(`[name=a-model][value="${CSS.escape(model)}"]`).checked = true;
  $("#a-cap").value = a.max_daily_cost_usd ?? "";
  $("#a-enabled").checked = a.enabled !== false;
  document.querySelector("#set-budget .panel").classList.toggle("ai-off", a.enabled === false);
  $("#a-batch").value = a.batch_size ?? 20;
  $("#a-desc").value = a.max_description_chars ?? 400;
  $("#a-maxads").value = a.max_ads_per_run ?? "";
  updateCostEstimate();
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

function watchRow(w) {
  return `<div class="watch-row">
    <input data-wq value="${esc(w.query ?? "")}" placeholder="ex. thinkpad t480">
    <span class="affix"><input data-wp type="number" min="0" value="${esc(w.max_price ?? "")}" placeholder="prix max"><i>€</i></span>
    <button class="icon danger" data-remove-watch title="Supprimer">🗑</button>
  </div>`;
}
// ---------- Recherches enregistrées ----------
const LOOP_MIN = 30;  // minutes : pas plus souvent (rythme poli, risque de blocage)
const SS_CONDITIONS = { any: "Tous états", used: "Occasion (fonctionnel)", parts: "Pour pièces / HS" };
const ssOptions = (map, value) => Object.entries(map)
  .map(([v, label]) => `<option value="${v}" ${(value || "") === v ? "selected" : ""}>${label}</option>`).join("");

function savedCard(s) {
  const num = (key, ph, unit, step = "1") => `<span class="affix"><input data-s="${key}" type="number" min="0" step="${step}"
    value="${esc(s[key] ?? "")}" placeholder="${ph}"><i>${unit}</i></span>`;
  return `<article class="saved-card" data-saved>
    <div class="saved-head">
      <input class="saved-name" data-s="name" value="${esc(s.name || "")}" placeholder="Nom, ex. Mini-PC home lab">
      <span class="loop-badge" aria-hidden="true"><i>↻</i> En boucle</span>
      <button type="button" class="primary" data-run-saved>▶ Lancer</button>
      <button type="button" class="icon danger" data-remove-saved title="Supprimer cette recherche">🗑</button>
    </div>
    <div class="label">Modèles ou mots cherchés (chacun sur tous les sites)</div>
    <div class="tags blue" data-s="queries" data-placeholder="ex. thinkcentre m920q"></div>
    <div class="saved-criteria">
      <label>Prix ${num("min_price", "min", "€")} à ${num("max_price", "max", "€")}</label>
      <label>État <select data-s="condition">${ssOptions(SS_CONDITIONS, s.condition || "any")}</select></label>
      <label>Remise <select data-s="delivery">${ssOptions({ "": "Peu importe", ship: "Livrable obligatoire" }, s.delivery)}</select></label>
      <label>Vendeur <select data-s="seller">${ssOptions({ "": "Tous", particulier: "Particuliers", pro: "Pros" }, s.seller)}</select></label>
      <label>Zone <input data-s="near" value="${esc(s.near || "")}" placeholder="Ville ou CP"> ≤ ${num("km", "km", "km")}</label>
      <label title="Plafond d'annonces envoyées à l'IA">IA max ${num("max_ads", "—", "ann.")}</label>
    </div>
    <div class="label">Exclure (mots)</div>
    <div class="tags red" data-s="exclude" data-placeholder="ex. boite vide"></div>
    <div class="saved-loop">
      <label class="switch"><input type="checkbox" data-s="loop" ${s.loop ? "checked" : ""}><span></span></label>
      <b>En boucle</b>
      <label>toutes les <span class="affix"><input data-s="every" type="number" min="${LOOP_MIN}" step="5"
        value="${esc(s.every ?? 60)}"><i>min</i></span></label>
      <span class="muted small" data-loop-status></span>
      <div class="loop-bar" aria-hidden="true"><span></span></div>
    </div>
    <p class="muted small" data-s-hint></p>
  </article>`;
}

function savedHint(card) {
  const n = getTags(card.querySelector('[data-s="queries"]')).length;
  card.querySelector("[data-s-hint]").textContent = n
    ? `${n} modèle${n > 1 ? "s" : ""} : ${n} recherche${n > 1 ? "s" : ""} par site actif, au rythme poli habituel.`
    : "Ajoutez au moins un modèle (Entrée après chaque mot).";
}

function addSavedCard(s) {
  $("#saved-list").insertAdjacentHTML("beforeend", savedCard(s));
  const card = $("#saved-list").lastElementChild;
  setTags(card.querySelector('[data-s="queries"]'), s.queries);
  setTags(card.querySelector('[data-s="exclude"]'), s.exclude);
  savedHint(card);
  return card;
}

function renderSaved() {
  $("#saved-list").innerHTML = "";
  (settings.saved_searches || []).forEach(addSavedCard);
  refreshLoops();
}

function readSaved(card) {
  const v = (key) => card.querySelector(`[data-s="${key}"]`).value.trim();
  return {
    name: v("name"), queries: getTags(card.querySelector('[data-s="queries"]')),
    min_price: v("min_price"), max_price: v("max_price"), condition: v("condition"), delivery: v("delivery"),
    seller: v("seller"), near: v("near"), km: v("km"), max_ads: v("max_ads"),
    exclude: getTags(card.querySelector('[data-s="exclude"]')),
    loop: card.querySelector('[data-s="loop"]').checked, every: v("every"),
  };
}

// Recherches en boucle : « dernier scan il y a… · prochain dans… » sur chaque carte, tant que l'onglet est ouvert.
let loopInfo = null, loopFetchedAt = 0, loopTicker = null, loopTicks = 0;
const loopScanning = new Set();  // recherches en plein scan (pour l'effet « terminé » à la fin)
const fmtWait = (s) => s < 60 ? "moins d'une minute" : s < 3600 ? `${Math.round(s / 60)} min`
  : `${Math.floor(s / 3600)} h ${String(Math.round((s % 3600) / 60)).padStart(2, "0")}`;

function loopStatus(card) {
  const el = card.querySelector("[data-loop-status]");
  const bar = card.querySelector(".loop-bar > span");
  const on = card.querySelector('[data-s="loop"]').checked;
  const every = Math.max(LOOP_MIN, Number(card.querySelector('[data-s="every"]').value) || 60);
  const name = card.querySelector('[data-s="name"]').value.trim();
  const info = loopInfo && loopInfo.searches[name];
  const live = on && info && info.every === every && loopInfo.active;
  const scanning = live && loopInfo.running === name;
  card.classList.toggle("looping", on);
  card.classList.toggle("loop-live", !!live);
  card.classList.toggle("scanning", !!scanning);
  if (scanning) loopScanning.add(name);
  else if (loopScanning.delete(name)) {  // le scan vient de finir : petit éclat sur la carte
    card.classList.remove("loop-done");
    void card.offsetWidth;
    card.classList.add("loop-done");
  }
  if (!on) { el.textContent = ""; bar.style.width = "0"; return; }
  if (!info || info.every !== every) {
    el.textContent = "Enregistrez pour l'activer. Elle tournera tant que Deal Sniper reste ouvert.";
    bar.style.width = "0";
    return;
  }
  if (!loopInfo.active) { el.textContent = "À l'arrêt en mode démo : lancez start_ui.bat."; return; }
  if (scanning) { el.textContent = "Scan en cours… les nouvelles annonces arrivent dans l'onglet Annonces."; return; }
  const elapsed = (Date.now() - loopFetchedAt) / 1000;  // compte à rebours entre deux interrogations
  if (info.last_s === null) {
    el.textContent = "Premier scan dans moins d'une minute.";
    bar.style.width = "100%";
    return;
  }
  const next = Math.max(0, info.next_s - elapsed), since = info.last_s + elapsed;
  bar.style.width = `${Math.min(100, (since / (every * 60)) * 100).toFixed(2)}%`;
  el.textContent = next < 1 ? `Dernier scan il y a ${fmtWait(since)} · prochain imminent.`
    : `Dernier scan il y a ${fmtWait(since)} · prochain dans ${fmtWait(next)}.`;
}
function loopTabBadge() {
  const tab = $('.tab[data-tab="saved"]');
  const count = loopInfo && loopInfo.active ? Object.keys(loopInfo.searches || {}).length : 0;
  tab.classList.toggle("has-loop", count > 0);
  tab.classList.toggle("loop-scanning", !!(loopInfo && loopInfo.running));
  tab.title = count ? `${count} recherche${count > 1 ? "s" : ""} en boucle` : "";
}
async function refreshLoops() {
  const data = await api("/api/saved-loops").catch(() => null);
  if (data) { loopInfo = data; loopFetchedAt = Date.now(); }
  document.querySelectorAll("#saved-list [data-saved]").forEach(loopStatus);
  loopTabBadge();
}
// Chaque seconde : compte à rebours et barre de progression. Le serveur est interrogé toutes les 10 s
// quand l'onglet est affiché (toutes les minutes sinon, pour la pastille de l'onglet).
function loopTick() {
  const visible = !$("#tab-saved").hidden;
  loopTicks++;
  if (loopTicks % (visible ? 10 : 60) === 0) refreshLoops();
  else if (visible) document.querySelectorAll("#saved-list [data-saved].loop-live").forEach(loopStatus);
}
if (!loopTicker) loopTicker = setInterval(loopTick, 1000);
$("#saved-list").addEventListener("input", (e) => {
  if (e.target.matches('[data-s="every"], [data-s="name"], [data-s="loop"]')) loopStatus(e.target.closest("[data-saved]"));
});
$("#saved-list").addEventListener("change", (e) => {
  if (!e.target.matches('[data-s="every"]')) return;
  if (Number(e.target.value) && Number(e.target.value) < LOOP_MIN) {
    e.target.value = LOOP_MIN;
    toast(`Toutes les ${LOOP_MIN} min au plus souvent, pour ne pas vous faire bloquer par les sites`);
  }
  loopStatus(e.target.closest("[data-saved]"));
});

$("#add-saved").addEventListener("click", () => {
  addSavedCard({ queries: [], exclude: [] }).querySelector('[data-s="name"]').focus();
  markDirty();
});
$("#saved-list").addEventListener("click", (e) => {
  const card = e.target.closest("[data-saved]");
  if (!card) return;
  if (e.target.closest("[data-remove-saved]")) { card.remove(); markDirty(); return; }
  if (e.target.closest("[data-run-saved]")) {
    const s = readSaved(card);
    if (!s.queries.length) return toast("Ajoutez au moins un modèle à cette recherche");
    launchSearch({ ...s, exclude: s.exclude.join(", ") }, s.name || s.queries.join(", "));
  }
});

// Onglet Recherche → « ☆ Enregistrer » : la recherche en cours et ses critères deviennent une recherche enregistrée.
$("#qs-save").addEventListener("click", async () => {
  const c = quickSearchCriteria();
  if (!c.query) return toast("Tapez d'abord ce que vous cherchez");
  const name = prompt("Nom de la recherche enregistrée ?", c.query);
  if (name === null) return;
  if (!settings) await loadSettings();
  if (!settings) return;
  addSavedCard({ ...c, name: name.trim() || c.query, queries: [c.query],
    exclude: c.exclude.split(",").map((w) => w.trim()).filter(Boolean) });
  $("#save").click();  // enregistre dans config.yaml
  toast("☆ Recherche enregistrée (onglet Enregistrées)");
});

function renderWatchlist() {
  $("#watch-list").innerHTML = (settings.watchlist || []).map(watchRow).join("");
}
$("#add-watch").addEventListener("click", () => {
  $("#watch-list").insertAdjacentHTML("beforeend", watchRow({}));
  $("#watch-list .watch-row:last-child [data-wq]").focus();
  markDirty();
});
$("#watch-list").addEventListener("click", (e) => {
  if (e.target.closest("[data-remove-watch]")) { e.target.closest(".watch-row").remove(); markDirty(); }
});

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
  const watchlist = [...document.querySelectorAll("#watch-list .watch-row")].map((r) => ({
    query: r.querySelector("[data-wq]").value, max_price: r.querySelector("[data-wp]").value,
  }));
  return {
    searches: commonCard ? searchRows(commonCard) : settings.searches, sites, categories, watchlist,
    saved_searches: [...document.querySelectorAll("#saved-list [data-saved]")].map(readSaved),
    filters: { global_exclude_keywords: getTags("#g-exclude"), deal_signals: getTags("#g-signals"),
      desirable_brands: getTags("#g-brands"),
               buyer_ad_keywords: getTags("#g-buyer"), min_price: $("#g-minprice").value,
               sold_title_keywords: getTags("#g-sold-title"), sold_phrases: getTags("#g-sold-desc"),
               drop_uncategorized: $("#g-uncat").checked, keep_unknown_price: $("#g-noprice").checked },
    location: { france_only: $("#g-france").checked, exclude_overseas: $("#g-overseas").checked, home: $("#g-home").value },
    notifier: { enabled: $("#n-enabled").checked, min_score: $("#n-min").value, instant_min_score: $("#n-instant").value,
                watch_score: $("#n-watch-on").checked ? $("#n-watch").value : "",
                deal_types: dealTypes, min_margin: $("#n-margin").value, min_ease: $("#n-ease").value,
                min_sale: $("#n-sale").value },
    analyzer: { enabled: $("#a-enabled").checked, model: document.querySelector("[name=a-model]:checked")?.value,
                max_daily_cost_usd: $("#a-cap").value, max_ads_per_run: $("#a-maxads").value,
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
// Estimation du coût IA par passage selon le modèle et le max d'annonces.
const PER_AD_USD = { "claude-haiku-5-5": 0.0001, "claude-haiku-4-5": 0.001 };
function updateCostEstimate() {
  const model = document.querySelector("[name=a-model]:checked")?.value || "claude-haiku-5-5";
  const per = PER_AD_USD[model] ?? 0.0005;
  const max = Number($("#a-maxads").value);
  const perAd = `≈ ${per.toLocaleString("fr-FR", { minimumFractionDigits: 4 })} $ / annonce`;
  if (max > 0) {
    const run = (max * per).toLocaleString("fr-FR", { minimumFractionDigits: 4, maximumFractionDigits: 4 });
    $("#a-estimate").textContent = `Estimation : ${perAd} → jusqu'à ${run} $ par passage et par site (${max} annonces max).`;
  } else {
    $("#a-estimate").textContent = `Estimation : ${perAd}. Le plafond quotidien reste votre vraie sécurité.`;
  }
}
$("#a-maxads").addEventListener("input", updateCostEstimate);
document.querySelectorAll("[name=a-model]").forEach((r) => r.addEventListener("change", updateCostEstimate));

$("#a-enabled").addEventListener("change", () =>
  document.querySelector("#set-budget .panel").classList.toggle("ai-off", !$("#a-enabled").checked));

// Enregistrer les clés dans .env (valeurs jamais relues depuis le serveur).
$("#save-secrets").addEventListener("click", async () => {
  const keys = ["ANTHROPIC_API_KEY", "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DISCORD_WEBHOOK_URL"];
  const body = {};
  keys.forEach((k) => { const v = $("#k-" + k).value.trim(); if (v) body[k] = v; });
  if (!Object.keys(body).length) { toast("Aucune clé saisie"); return; }
  $("#secrets-status").textContent = "Enregistrement…";
  try {
    const data = await api("/api/secrets", { method: "POST", body: JSON.stringify(body) });
    keys.forEach((k) => ($("#k-" + k).value = ""));  // on n'affiche jamais une clé enregistrée
    $("#secrets-status").textContent = `✅ ${data.updated.length} clé(s) enregistrée(s) dans .env`;
    loadStatus();
  } catch (e) { $("#secrets-status").textContent = "❌ " + e.message; }
});

$("#reset-config").addEventListener("click", async () => {
  if (!confirm("Remplacer votre configuration (recherches, catégories, filtres, réglages) par la configuration d'exemple ?\n\nUne copie de l'actuelle est gardée dans le dossier du projet.")) return;
  try {
    const data = await api("/api/settings/reset", { method: "POST" });
    await loadSettings();
    loadStatus();
    toast(data.backup ? `✅ Configuration d'exemple chargée (ancienne gardée : ${data.backup})` : "✅ Configuration d'exemple chargée");
  } catch (e) { toast("❌ " + e.message); }
});
// ---------- Sauvegarde et restauration (.zip) ----------
$("#backup-data").addEventListener("click", async () => {
  try {
    const resp = await fetch("/api/backup");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const blob = await resp.blob();
    const match = (resp.headers.get("Content-Disposition") || "").match(/filename="?([^"]+)"?/i);
    const filename = match ? match[1] : `dealsniper-backup-${new Date().toISOString().slice(0, 10)}.zip`;
    const link = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: filename });
    link.click();
    URL.revokeObjectURL(link.href);
    toast(`💾 Sauvegarde téléchargée (${filename})`);
  } catch (e) {
    toast("❌ Impossible de créer la sauvegarde : " + e.message);
  }
});

$("#restore-data").addEventListener("click", () => $("#restore-file").click());

$("#restore-file").addEventListener("change", async (e) => {
  const file = e.target.files?.[0];
  e.target.value = "";
  if (!file) return;
  if (!confirm(`Restaurer « ${file.name} » ?\n\nVotre base et/ou configuration actuelles seront sauvegardées automatiquement avant remplacement.`)) return;
  const form = new FormData();
  form.append("file", file);
  try {
    const resp = await fetch("/api/restore", { method: "POST", body: form });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
    settings = null;
    await Promise.all([loadSettings(), loadAds(), loadStatus()]);
    toast(`✅ Restauré : ${(data.restored || []).join(", ")}`);
  } catch (err) {
    toast("❌ Restauration échouée : " + err.message);
  }
});

$("#discard").addEventListener("click", () => { if (confirm("Annuler les modifications non enregistrées ?")) loadSettings(); });

// ---------- Vérification : les annonces affichées sont-elles toujours en ligne ? ----------
let checkPoll = null;
const SITE_REASON = { leboncoin: "leboncoin bloque ces vérifications", facebook: "Facebook demande une connexion" };
const shownAds = () => (document.querySelector("#tab-search:not([hidden])") ? filteredAds(searchResults) : filteredAds());

$("#check-ads").addEventListener("click", async () => {
  if (checkPoll) {  // 2e clic : arrêter
    await api("/api/check/cancel", { method: "POST" }).catch(() => {});
    return;
  }
  const ads = shownAds();
  if (!ads.length) return toast("Aucune annonce affichée à vérifier");
  if (ads.length > 500) toast("Seules les 500 premières annonces affichées seront vérifiées");
  try {
    await api("/api/check", { method: "POST", body: JSON.stringify({ uids: ads.slice(0, 500).map((a) => a.uid) }) });
  } catch (e) { return toast(e.message); }
  $("#check-ads").textContent = "■ Arrêter la vérification";
  $("#check-ads").classList.add("danger");
  checkPoll = setInterval(pollCheck, 1000);
  pollCheck();
});

async function pollCheck() {
  const data = await api("/api/check").catch(() => null);
  if (!data || !data.report) return;
  renderCheck(data.report, data.running);
  if (data.running) return;
  clearInterval(checkPoll); checkPoll = null;
  $("#check-ads").textContent = "🔄 Encore en ligne ?";
  $("#check-ads").classList.remove("danger");
  loadAds();  // marque les disparues sur les cartes
}

function renderCheck(r, isRunning) {
  const box = $("#check-panel");
  box.hidden = false;
  const gone = r.gone || [], unknown = r.unknown || [];
  const pct = r.total ? Math.round((100 * r.done) / r.total) : 0;
  if (isRunning) {
    // Restant par site : un site encore long (rythme poli de ~10 s par annonce) explique un compteur lent.
    const pending = Object.entries(r.pending || {}).filter(([, n]) => n > 0)
      .map(([site, n]) => `${esc(siteName(site))} : ${n} restante${n > 1 ? "s" : ""}`).join(" · ");
    const eta = r.eta_s > 0 ? ` · fin dans ~${r.eta_s >= 90 ? `${Math.round(r.eta_s / 60)} min` : `${r.eta_s} s`}` : "";
    box.innerHTML = `<div class="check-head">🔄 Vérification en cours… <b>${r.done}/${r.total}</b>
        <span class="muted small">· ${gone.length} disparue(s) pour l'instant${eta}</span></div>
      <div class="check-bar"><span style="width:${pct}%"></span></div>
      ${pending ? `<p class="muted small" style="margin:6px 0 0">${pending} — une requête toutes les ~10 s par site, pour ne pas se faire bloquer</p>` : ""}`;
    return;
  }
  const line = (x) => `<li>${siteLogo(x.source, 14)} <a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a>
    ${x.note ? `<span class="muted small">— ${esc(x.note)}</span>` : ""}</li>`;
  const bySite = unknown.reduce((m, x) => ((m[x.source] = (m[x.source] || 0) + 1), m), {});
  box.innerHTML = `<div class="check-head">${r.error ? "⚠️ Vérification interrompue" : r.cancelled ? "■ Vérification arrêtée" : "✅ Vérification terminée"}
      <button class="icon" data-close-check title="Fermer">✕</button></div>
    ${r.error ? `<p class="small">${esc(r.error)}</p>` : ""}
    <div class="check-kpis">
      <div><b>${r.done}</b><span>vérifiées</span></div>
      <div class="ok"><b>${r.active}</b><span>toujours en ligne</span></div>
      <div class="ko"><b>${gone.length}</b><span>disparues</span></div>
      <div><b>${unknown.length}</b><span>non vérifiables</span></div>
    </div>
    ${gone.length ? `<details class="check-list" open><summary>❌ Annonces disparues (${gone.length})</summary><ul>${gone.map(line).join("")}</ul>
      <button class="ghost" data-hide-gone>Masquer ces ${gone.length} annonce(s)</button></details>` : ""}
    ${unknown.length ? `<details class="check-list"><summary>❔ Non vérifiables (${unknown.length}) —
      ${Object.entries(bySite).map(([s, n]) => `${esc(siteName(s))} ${n}${SITE_REASON[s] ? ` : ${SITE_REASON[s]}` : ""}`).join(" · ")}</summary>
      <ul>${unknown.map(line).join("")}</ul></details>` : ""}`;
  box.dataset.gone = JSON.stringify(gone.map((x) => x.uid));
}

$("#check-panel").addEventListener("click", async (e) => {
  if (e.target.closest("[data-close-check]")) { $("#check-panel").hidden = true; return; }
  if (e.target.closest("[data-hide-gone]")) {
    const uids = JSON.parse($("#check-panel").dataset.gone || "[]");
    await api("/api/ads/hide", { method: "POST", body: JSON.stringify({ uids }) }).catch((err) => toast(err.message));
    toast(`${uids.length} annonce(s) masquée(s)`);
    loadAds();
  }
});

// ---------- Export CSV (toutes les annonces filtrées, pour Excel) ----------
$("#export-csv").addEventListener("click", () => {
  const ads = filteredAds();
  const cols = [
    ["Repérée", (ad) => (ad.seen_at || "").slice(0, 16).replace("T", " ")],
    ["Site", (ad) => siteName(ad.source)], ["Titre", (ad) => ad.title], ["Catégorie", (ad) => ad.category],
    ["Prix (€)", (ad) => ad.total_price ?? ad.price], ["Revente (€)", (ad) => (ad.analysis || {}).valeur_revente_estimee],
    ["Marge (€)", (ad) => (ad.analysis || {}).marge_nette_estimee], ["Cote (€)", (ad) => ad.market && ad.market.median],
    ["Note", (ad) => ad.score], ["Réparation", (ad) => (ad.analysis || {}).facilite_reparation],
    ["Vente", (ad) => (ad.analysis || {}).facilite_vente], ["Panne", (ad) => (ad.analysis || {}).panne_probable],
    ["Ville", (ad) => cleanPlace(ad.location)], ["Distance (km)", (ad) => ad.distance_km],
    ["Statut", (ad) => (ad.status === "rejected" ? `écartée : ${ad.reason || ""}` : "notée")], ["Lien", (ad) => ad.url],
  ];
  // Excel en français : séparateur « ; », décimales avec virgule, BOM pour les accents.
  const cell = (v) => {
    if (v == null) return "";
    const text = typeof v === "number" ? String(v).replace(".", ",") : String(v);
    return /[";\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  const csv = [cols.map((c) => c[0]).join(";"), ...ads.map((ad) => cols.map((c) => cell(c[1](ad))).join(";"))].join("\r\n");
  const link = Object.assign(document.createElement("a"), {
    href: URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" })),
    download: `dealsniper-${new Date().toISOString().slice(0, 10)}.csv`,
  });
  link.click();
  URL.revokeObjectURL(link.href);
  toast(`⬇ ${ads.length} annonce(s) exportée(s)`);
});

// ---------- Achats ----------
const frDate = (iso) => (iso ? String(iso).slice(0, 10).split("-").reverse().join("/") : "");
const money = (v) => (v == null ? "—" : `${Number(v).toLocaleString("fr-FR", { maximumFractionDigits: 2 })} €`);
const hourly = (v) => (v == null ? "—" : `${Number(v).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} €/h`);

async function loadPurchases() {
  let data;
  try { data = await api("/api/purchases"); } catch (e) { toast("❌ " + e.message); return; }
  const s = data.summary, c = data.calibration;
  const kpi = (label, value, cls = "") => `<div class="kpi ${cls}"><span>${label}</span><b>${value}</b></div>`;
  $("#p-summary").innerHTML = kpi("Achats", `${s.count} <small>(${s.in_stock} en stock)</small>`)
    + kpi("Investi", money(s.invested)) + kpi("Revendu", money(s.revenue))
    + kpi("Bénéfice", `${s.profit >= 0 ? "+" : ""}${money(s.profit)}`, s.profit >= 0 ? "pos" : "neg")
    + kpi("Rentabilité", s.roi_pct == null ? "—" : `${s.roi_pct.toLocaleString("fr-FR")} %`)
    + kpi("Rentabilité horaire", s.hourly_rate == null ? "—"
      : `${hourly(s.hourly_rate)}${s.sold_hours ? ` <small>(${Number(s.sold_hours).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} h)</small>` : ""}`,
      s.hourly_rate == null ? "" : s.hourly_rate >= 0 ? "pos" : "neg");
  const t = data.tax;
  const barCls = t.level === "depasse" ? "full" : t.level === "proche" ? "warn" : "";
  $("#p-tax").className = `tax-box ${t.level}`;
  $("#p-tax").innerHTML = `
    <div class="tax-head">🧾 Suivi fiscal ${t.year} <span class="muted">— indicatif, pas un conseil fiscal</span></div>
    <div class="tax-kpis">
      <div><span>Ventes</span><b>${t.sales} <small>/ ${t.sales_threshold}</small></b></div>
      <div><span>Chiffre d'affaires</span><b>${money(t.revenue)} <small>/ ${money(t.revenue_threshold)}</small></b></div>
      <div><span>Bénéfice ${t.year}</span><b>${money(t.profit)}</b></div>
    </div>
    <div class="budget-bar big"><div class="${barCls}" style="width:${t.reach_pct}%"></div></div>
    <p class="tax-note">${esc(t.note)}</p>`;
  $("#p-calibration").innerHTML = c.ratio == null
    ? `🎯 <b>Calibrage des estimations</b> : ${c.sales}/${c.needed} ventes avec estimation. Dès ${c.needed}, Deal Sniper compare
       vos prix de revente réels à ses estimations et ajuste sa prudence tout seul.`
    : `🎯 <b>Calibrage</b> : vos ventes se font à <b>${Math.round(c.ratio * 100)} %</b> de l'estimation (${c.sales} ventes).
       Prudence appliquée : × ${String(c.haircut).replace(".", ",")} au lieu de × ${String(c.base).replace(".", ",")}.`;
  const field = (p, key, label, type = "number", step = "0.01") => `<label class="pf"><span>${label}</span>
      <input data-pid="${p.id}" data-key="${key}" type="${type}" ${type === "number" ? `step="${step}" min="0"` : ""} value="${esc(p[key] ?? "")}"></label>`;
  $("#p-list").innerHTML = data.purchases.length ? data.purchases.map((p) => {
    const cost = (p.buy_price || 0) + (p.repair_cost || 0);
    const profit = p.sold_price != null ? p.sold_price - cost : null;
    return `<article class="purchase ${p.sold_price != null ? "sold" : ""}">
      <div class="p-head">
        <div><b>${p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a>` : esc(p.title)}</b>
          <small>${p.source ? `${esc(siteName(p.source))} · ` : ""}acheté le ${esc(frDate(p.bought_at))}${p.sold_at ? ` · revendu le ${esc(frDate(p.sold_at))}` : ""}</small></div>
        <span class="p-profit ${profit == null ? "" : profit >= 0 ? "pos" : "neg"}">${profit == null ? "En stock" : `${profit >= 0 ? "+" : ""}${money(profit)}`}${p.hourly_rate != null ? ` <small>· ${hourly(p.hourly_rate)}</small>` : ""}</span>
        <button class="icon danger" data-del="${p.id}" title="Supprimer cet achat">🗑</button>
      </div>
      <div class="p-fields">
        ${field(p, "buy_price", "Prix payé (€)")}${field(p, "repair_cost", "Réparation (€)")}
        ${field(p, "sold_price", "Revendu (€)")}${field(p, "hours_spent", "Temps (h)", "number", "0.25")}
        <label class="pf"><span>Estimation</span><b>${money(p.estimated_resale)}</b></label>
        ${field(p, "notes", "Notes", "text")}
      </div>
    </article>`;
  }).join("") : '<div class="empty">Aucun achat suivi pour l\'instant.</div>';
}

$("#p-list").addEventListener("change", async (e) => {
  const input = e.target.closest("[data-pid]");
  if (!input) return;
  try {
    await api(`/api/purchases/${input.dataset.pid}`, { method: "PUT", body: JSON.stringify({ [input.dataset.key]: input.value }) });
    loadPurchases();
  } catch (err) { toast("❌ " + err.message); }
});
$("#p-list").addEventListener("click", async (e) => {
  const del = e.target.closest("[data-del]");
  if (!del || !confirm("Supprimer cet achat ?")) return;
  await api(`/api/purchases/${del.dataset.del}`, { method: "DELETE" }).catch(() => null);
  loadPurchases();
});
$("#p-add").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = new FormData(e.target);
  try {
    await api("/api/purchases", { method: "POST", body: JSON.stringify(Object.fromEntries(form)) });
    e.target.reset();
    loadPurchases();
  } catch (err) { toast("❌ " + err.message); }
});

// Marque recherchée repérée par le filtre (signal « marque recherchée : apple »), sinon "".
function brandOf(ad) {
  const s = (ad.signals || []).find((x) => x.startsWith("marque recherchée"));
  return s ? s.split(":").slice(1).join(":").trim() : "";
}

// « il y a 5 min », « il y a 2 h », « hier »…
function timeAgo(iso) {
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  const sec = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (sec < 90) return "à l'instant";
  if (sec < 5400) return `il y a ${Math.round(sec / 60)} min`;
  if (sec < 86400) return `il y a ${Math.round(sec / 3600)} h`;
  return `il y a ${Math.round(sec / 86400)} j`;
}

// ---------- Bilan / tableau de bord ----------
async function loadStats() {
  let data;
  try { data = await api(`/api/stats?days=${$("#st-days").value}`); } catch (e) { toast("❌ " + e.message); return; }
  const kpi = (label, value, cls = "") => `<div class="kpi ${cls}"><span>${label}</span><b>${value}</b></div>`;
  $("#st-kpis").innerHTML = kpi("Annonces notées", data.total)
    + kpi("Marge moyenne", data.avg_margin == null ? "—" : `${data.avg_margin >= 0 ? "+" : ""}${data.avg_margin} €`,
          data.avg_margin >= 0 ? "pos" : "")
    + kpi("Meilleure marge", data.best_margin == null ? "—" : `+${data.best_margin} €`, "pos")
    + kpi("Note moyenne", data.avg_score == null ? "—" : `${String(data.avg_score).replace(".", ",")}/10`);
  $("#st-activity").innerHTML = data.activity.length >= 2
    ? pointsChart(data.activity, { caption: "Annonces notées par jour", line: true, fmt: (v) => Math.round(v),
        dateFmt: { day: "2-digit", month: "short" }, aria: "Activité par jour" })
    : '<p class="muted small">Pas encore assez de données.</p>';
  $("#st-cats").innerHTML = data.categories.length
    ? `<table class="stat-table"><thead><tr><th>Catégorie</th><th>Nb</th><th>Marge moy.</th><th>Note</th></tr></thead>
       <tbody>${data.categories.map((c) => `<tr><td>${esc(c.name)}</td><td>${c.count}</td>
         <td class="${c.avg_margin >= 0 ? "pos" : ""}">${c.avg_margin == null ? "—" : `${c.avg_margin} €`}</td>
         <td>${c.avg_score == null ? "—" : `${String(c.avg_score).replace(".", ",")}`}</td></tr>`).join("")}</tbody></table>`
    : '<p class="muted small">Aucune annonce notée sur la période.</p>';
}
$("#st-days").addEventListener("change", loadStats);

// ---------- État de l'installation ----------
async function loadStatus() {
  const s = await api("/api/status").catch(() => null);
  if (!s) return;
  $("#demo-badge").hidden = !s.demo;
  renderSearchSites(s.sites || []);
  const items = [
    s.ai_enabled === false
      ? { ok: true, text: "Mode sans IA : tri et estimation par la cote du marché (aucune clé Anthropic requise)" }
      : { ok: s.anthropic, text: s.anthropic ? "Clé Anthropic présente : les annonces sont notées par l'IA"
          : "Clé Anthropic manquante : collez-la ci-dessous, ou passez en mode sans IA (Réglages → Budget IA)", short: "Clé IA manquante" },
    { ok: s.channels.length > 0, text: s.channels.length ? `Notifications : ${s.channels.join(" + ")}`
      : "Aucune notification : ajoutez TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID (ou DISCORD_WEBHOOK_URL) dans .env", short: "Pas de notifications" },
    ...s.sites.map((site) => {
      if (!site.ok) return { ok: false, text: `${site.label} ignoré : ${site.reason}`, short: `${site.label} inactif`, why: site.reason };
      const last = site.last_ok ? `dernière récolte ${timeAgo(site.last_ok)}${site.last_count != null ? ` · ${site.last_count} annonces` : ""}` : "jamais récolté encore";
      const broken = site.last_error ? ` — ⚠️ dernière erreur : ${site.last_error}` : "";
      const isPaused = site.paused_until && new Date(site.paused_until) > new Date();
      const pausedText = isPaused ? ` [en pause jusqu'à ${new Date(site.paused_until).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}]` : "";
      return { ok: !site.last_error && !isPaused, text: `${site.label} : ${last}${broken}${pausedText}`, short: isPaused ? `${site.label} en pause` : `${site.label} en erreur`, why: site.last_error, site: site.name, isPaused };
    }),
  ];
  if (s.error) items.push({ ok: false, text: `config.yaml illisible : ${s.error}` });
  $("#status-list").innerHTML = items.map((i) => `<li class="${i.ok ? "ok" : "ko"}">${i.ok ? "✓" : "⚠"} ${esc(i.text)}${i.isPaused ? ` <button class="link small" data-unpause-site="${esc(i.site)}">Réessayer</button>` : ""}</li>`).join("");
  $("#status-list").querySelectorAll("[data-unpause-site]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      const ok = confirm("Lever la pause maintenant ?\n\nRetenter juste après un blocage le prolonge souvent. "
        + "Faites-le seulement après avoir validé la vérification dans la fenêtre Chrome (headless: false).");
      if (!ok) return;
      await api(`/api/sites/${encodeURIComponent(btn.dataset.unpauseSite)}/unpause`, { method: "POST" });
      loadStatus();
    });
  });

  // Bandeau sur l'onglet Annonces : seulement les problèmes, masquable.
  const problems = items.filter((i) => !i.ok);
  const key = problems.map((p) => p.text).join("|");
  $("#setup").hidden = !problems.length || store.get("setup-hidden", "") === key;
  // Bandeau compact : une pastille par problème (détail au survol), lien vers Réglages.
  $("#setup").innerHTML = `<span class="setup-title">⚠ À régler</span>
    ${problems.map((p) => `<span class="setup-chip" title="${esc(p.text)}">${esc(p.short || p.text)}</span>`).join("")}
    <button class="link" data-goto-keys>Réglages →</button>
    <button class="icon" title="Masquer" data-hide-setup>✕</button>`;
  $("#setup [data-goto-keys]").addEventListener("click", () => { showTab("settings"); showSettingsSection("set-status"); });
  $("#setup [data-hide-setup]").addEventListener("click", () => {
    $("#setup").hidden = true;
    store.set("setup-hidden", key);
  });
}

// Page rechargée (téléphone, F5) : le compte rendu de la dernière recherche ponctuelle revient, ou son suivi
// reprend si elle tourne encore.
async function restoreLastSearch() {
  const data = await api(`/api/search-results?${nearParam().slice(1)}`).catch(() => null);
  if (!data || !data.query) return;
  searchQuery = data.query;
  if (!$("#qs-query").value && (data.queries || []).length <= 1) $("#qs-query").value = data.query;
  if (data.running) {
    setRunning(true);
    if (!searchPoll) searchPoll = setInterval(pollSearch, 1200);
    return;
  }
  if (!(data.ads || []).length) return;
  const prog = await api("/api/progress").catch(() => ({ progress: {} }));
  renderSearchReport(prog.progress || {}, data.ads);
}

// ---------- Analyse d'un lien (Claude Opus 5.5) ----------
let linkHistory = [], linkTimer = null;
const LINK_STEPS = [
  [0, "Lecture de l'annonce"], [4, "Examen des photos"], [12, "Recherche des ventes réelles sur le web"],
  [35, "Comparaison des prix et des pièges"], [60, "Rédaction du bilan"],
];
const SCAM = { faible: ["good", "Risque d'arnaque faible"], moyen: ["mid", "Risque d'arnaque moyen"],
  "élevé": ["low", "Risque d'arnaque élevé"] };

function linkProgress(on) {
  const box = $("#link-progress");
  clearInterval(linkTimer);
  box.hidden = !on;
  if (!on) return;
  const start = Date.now();
  const draw = () => {
    const s = (Date.now() - start) / 1000;
    const current = LINK_STEPS.filter(([at]) => s >= at).length - 1;
    box.innerHTML = `<div class="lp-head"><span class="lp-spinner"></span><b>Analyse en cours</b>
      <span class="muted small">${Math.floor(s)} s · souvent 30 à 90 s</span></div>
      <ol>${LINK_STEPS.map(([, label], i) => `<li class="${i < current ? "done" : i === current ? "now" : ""}">${label}</li>`).join("")}</ol>
      <div class="lp-bar"><span style="width:${Math.min(95, (s / 90) * 100).toFixed(1)}%"></span></div>`;
  };
  draw();
  linkTimer = setInterval(draw, 500);
}

function noteRing(note) {
  if (note == null) return "";
  const cls = note >= 7 ? "good" : note >= 5 ? "mid" : "low";
  const deg = Math.round((note / 10) * 360);
  return `<div class="note-ring ${cls}" style="--deg:${deg}deg" title="Note globale de l'affaire"><b>${String(note).replace(".", ",")}</b><small>/10</small></div>`;
}
const listBlock = (title, items, cls = "") => (items || []).length
  ? `<div class="lr-list ${cls}"><b>${title}</b><ul>${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : "";

function linkReport(rec, open = true) {
  const ad = rec.ad, d = rec.result;
  const v = deepVerdict(ad, d);
  const [scamCls, scamLabel] = SCAM[d.arnaque?.niveau] || SCAM.moyen;
  const range = [d.revente_basse, d.revente_haute].every((x) => x != null) ? `${euros(d.revente_basse)} – ${euros(d.revente_haute)}` : "—";
  const photos = (ad.photos || []).length ? ad.photos : ad.image ? [ad.image] : [];
  const when = new Date(rec.at).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
  return `<article class="link-report ${open ? "" : "collapsed"}" data-rid="${esc(rec.id)}">
    <header class="lr-head">
      ${photos[0] ? `<img class="lr-photo" src="${esc(photos[0])}" alt="" loading="lazy" referrerpolicy="no-referrer">` : '<div class="lr-photo empty">📦</div>'}
      <div class="lr-title">
        <span class="muted small">${esc(ad.source || "")} · ${when}${ad.location ? ` · ${esc(ad.location)}` : ""}</span>
        <b>${esc(ad.title)}</b>
        ${d.modele_exact ? `<span class="small">Modèle identifié : ${esc(d.modele_exact)}</span>` : ""}
        ${ad.available === false ? '<span class="conf low">indiquée vendue / indisponible</span>' : ""}
      </div>
      <div class="lr-price"><b>${euros(ad.total_price ?? ad.price)}</b>${ad.url ? `<a href="${esc(ad.url)}" target="_blank" rel="noopener">Voir l'annonce ↗</a>` : ""}</div>
      ${noteRing(d.note)}
      <button type="button" class="link lr-toggle" data-lr-toggle title="Afficher / masquer">${open ? "▴" : "▾"}</button>
    </header>
    <div class="lr-body">
      <div class="verdict ${v.cls}"><span class="v-icon">${v.icon}</span>
        <div><b>${esc(v.title)}</b><p>${esc(v.text)}</p></div>
        <div class="deep-meta"><span class="conf ${CONF_LEVEL[d.confiance] || "mid"}">confiance ${esc(d.confiance)}</span>
          <span class="conf ${scamCls}">${scamLabel}</span><span>${d.cost_usd ? iaCost(d.cost_usd) : ""}</span></div>
      </div>
      ${photos.length > 1 ? `<div class="lr-gallery">${photos.map((u) => `<img src="${esc(u)}" alt="" loading="lazy" referrerpolicy="no-referrer">`).join("")}</div>` : ""}
      <div class="deep-grid">
        <div class="deep-facts">
          <div class="d-tiles three">
            <div class="d-tile"><span>Revente réelle</span><b>${range}</b>${d.revente_typique != null ? `<small>typique ${euros(d.revente_typique)}</small>` : ""}</div>
            <div class="d-tile ${d.prix_achat_max != null && d.prix_achat_max <= 0 ? "neg" : ""}"><span>Achat max</span><b>${d.prix_achat_max != null ? euros(d.prix_achat_max) : "—"}</b><small>pour +${d.marge_visee ?? 30} € net</small></div>
            <div class="d-tile"><span>Demandé</span><b>${euros(ad.total_price ?? ad.price)}</b><small>&nbsp;</small></div>
          </div>
          ${d.resume ? `<p class="deep-summary">${esc(d.resume)}</p>` : ""}
          <div class="lr-two">${listBlock("👍 Points forts", d.points_forts, "pos")}${listBlock("👎 Points faibles", d.points_faibles, "neg")}</div>
          ${(d.arnaque?.raisons || []).length ? listBlock(`🛡️ ${scamLabel}`, d.arnaque.raisons, scamCls) : ""}
          ${photoBlock(d)}
          ${(d.sources || []).length ? `<div class="deep-sources">${d.sources.map((x) => `<span>${esc(x)}</span>`).join("")}</div>` : ""}
        </div>
        <div class="deep-charts">${salesChart(d) || '<div class="chart-empty">Pas de vente datée trouvée pour le graphique</div>'}
          ${listBlock("✅ À vérifier en main propre", d.a_verifier, "check")}
          ${listBlock("💬 À demander au vendeur", d.questions)}</div>
      </div>
      ${d.message_negociation ? negotiationBox(rec.id, d.message_negociation, d.prix_achat_max, ad.total_price ?? ad.price) : ""}
      <div class="lr-foot"><span class="muted small">Analysé par ${esc(d.model || "Claude")}${d.photos_analysees ? ` · ${d.photos_analysees} photo(s) examinée(s)` : ""}</span>
        <button type="button" class="lr-forget" data-lr-forget>Supprimer cette analyse</button></div>
    </div>
  </article>`;
}

function renderLinkHistory() {
  const rest = linkHistory.slice(1);
  $("#link-history-title").hidden = !rest.length;
  $("#link-history").innerHTML = rest.map((r) => linkReport(r, false)).join("");
  if (linkHistory[0] && !$("#link-result").innerHTML) $("#link-result").innerHTML = linkReport(linkHistory[0]);
}
async function loadLinkHistory() {
  const data = await api("/api/link-analyses").catch(() => null);
  if (!data) return;
  linkHistory = data.items || [];
  renderLinkHistory();
}

async function runLinkAnalysis(e) {
  e?.preventDefault();
  const manualOpen = !$("#link-manual").hidden;
  const payload = { url: $("#link-url").value.trim() };
  if (manualOpen && $("#lm-title").value.trim()) {
    Object.assign(payload, { title: $("#lm-title").value.trim(), price: $("#lm-price").value,
      description: $("#lm-description").value.trim(), image: $("#lm-image").value.trim() });
  }
  if (!payload.url && !payload.title) return toast("Collez le lien de l'annonce");
  const btn = $("#link-run");
  btn.disabled = true;
  $("#link-result").innerHTML = "";
  linkProgress(true);
  try {
    const data = await api("/api/link-analysis", { method: "POST", body: JSON.stringify(payload) });
    linkHistory = [data.item, ...linkHistory.filter((r) => r.id !== data.item.id)];
    $("#link-result").innerHTML = linkReport(data.item);
    renderLinkHistory();
    $("#link-result .link-report")?.classList.add("appear");
    api("/api/progress").then((p) => renderBudget(p.cost_today, p.cost_cap)).catch(() => {});  // budget IA à jour
  } catch (err) {
    toast("❌ " + err.message);
    if (err.data?.manual) {  // le site refuse la lecture : saisie manuelle proposée
      $("#link-manual").hidden = false;
      $("#link-manual-why").textContent = err.message;
      $("#lm-title").focus();
    }
  } finally {
    linkProgress(false);
    btn.disabled = false;
  }
}
$("#link-form").addEventListener("submit", runLinkAnalysis);
$("#link-manual-toggle").addEventListener("click", () => {
  $("#link-manual").hidden = !$("#link-manual").hidden;
  if (!$("#link-manual").hidden) $("#lm-title").focus();
});
$("#tab-analyze").addEventListener("click", async (e) => {
  const report = e.target.closest(".link-report");
  if (e.target.closest("[data-copy-nego]")) return copyNegotiation(e.target.closest("[data-copy-nego]"));
  if (!report) return;
  if (e.target.closest("[data-lr-toggle]") || (report.classList.contains("collapsed") && e.target.closest(".lr-head") && !e.target.closest("a"))) {
    report.classList.toggle("collapsed");
    report.querySelector("[data-lr-toggle]").textContent = report.classList.contains("collapsed") ? "▾" : "▴";
  } else if (e.target.closest("[data-lr-forget]")) {
    await api(`/api/link-analyses/${encodeURIComponent(report.dataset.rid)}`, { method: "DELETE" }).catch(() => null);
    linkHistory = linkHistory.filter((r) => r.id !== report.dataset.rid);
    report.remove();
    $("#link-history-title").hidden = !$("#link-history .link-report");
  }
});

restoreFilters();
syncMirrors();
countActiveFilters();
loadStatus();
api("/api/preferences").then((d) => { preferences = d.categories || {}; renderAds(true); }).catch(() => {});
showSettingsSection(store.get("set", "set-notif"));
showTab(store.get("tab", "ads"));
loadAds().then(restoreLastSearch).catch(() => {});
setInterval(loadAds, 60000);
refreshLoops();  // pastille de l'onglet Enregistrées si une boucle tourne
