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
}
setInterval(() => {  // chrono et « buffering » animés entre deux rafraîchissements
  if (!progress || progress.done) return;
  spin = (spin + 1) % SPINNER.length;
  drawProgress();
}, 150);
$("#progress").addEventListener("click", (e) => {
  if (e.target.closest("[data-close-progress]")) { progressClosed = true; drawProgress(); return; }
  const row = e.target.closest(".prog-row.expandable");
  if (row) {
    const site = row.dataset.siteLog;
    openedSites.has(site) ? openedSites.delete(site) : openedSites.add(site);
    drawProgress();
  }
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
    renderAds(true);  // rafraîchissement : on reste sur la même page
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

function adCard(ad) {
  const a = ad.analysis || {};
  const image = ad.image
    ? `<img src="${esc(ad.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'placeholder',textContent:'📦'}))">`
    : `<span class="placeholder">📦</span>`;
  const buy = euros(ad.total_price ?? ad.price);
  const marginValue = a.marge_nette_estimee;
  const margin = marginValue == null ? "—" : `${marginValue >= 0 ? "+" : ""}${euros(marginValue)}`;
  const marginClass = marginValue == null ? "" : marginValue >= 0 ? "pos" : "neg";

  return `<article class="ad ${ad.status}" data-uid="${esc(ad.uid)}" ${ad.image == null ? "data-need-image" : ""}>
    <a class="thumb" href="${esc(ad.url)}" target="_blank" rel="noopener">${image}</a>
    <div class="body">
      <a class="title" href="${esc(ad.url)}" target="_blank" rel="noopener">${esc(ad.title)}</a>
      <div class="stats">
        <div><span>Achat</span><b>${buy}</b></div>
        <div><span>Revente</span><b>${a.valeur_revente_estimee != null ? euros(a.valeur_revente_estimee) : "—"}</b></div>
        <div class="${marginClass}"><span>Marge</span><b>${margin}</b></div>
      </div>
      ${ad.analysis ? scoreRow(ad) : ""}
      ${ad.reason ? `<div class="reason">✗ ${esc(ad.reason)}</div>` : ""}
      <div class="meta">
        <div class="site-line">${siteLogo(ad.source, 14)} ${esc(siteName(ad.source))}${ad.category ? ` · ${esc(ad.category)}` : ""}</div>
        <button class="info" data-info="${esc(ad.uid)}" title="Détails, panne, ville, questions à poser">i</button>
      </div>
    </div>
  </article>`;
}

// ---------- Détails d'une annonce (bouton « i ») ----------
const faultText = (a) => (!a.panne_probable ? "" : a.panne_probable.toLowerCase() === "aucune"
  ? "Aucune — revente en l'état" : a.panne_probable);

// Questions à poser au vendeur : générées ici selon la catégorie, sans appel à l'IA (gratuit).
function sellerQuestions(ad) {
  const a = ad.analysis || {};
  const cat = `${ad.category || ""} ${ad.title || ""}`.toLowerCase();
  const q = ["Pouvez-vous envoyer une photo de l'étiquette (modèle exact) et de l'appareil allumé ?"];
  if (a.type_affaire === "revente_directe") {
    q.push("Pourquoi le vendez-vous ? Avez-vous la facture ?", "Y a-t-il des rayures, chocs ou défauts, même petits ?");
  } else {
    q.push("Depuis quand la panne est-elle apparue, et comment ?",
      "A-t-il pris l'eau, subi une chute, ou été ouvert / réparé ?");
  }
  if (/pc|portable|thinkpad|latitude|elitebook|macbook|surface|mini|thinkcentre|ordinateur/.test(cat)) {
    q.push("Y a-t-il un mot de passe BIOS ou un verrouillage (iCloud, compte Microsoft, MDM) ?",
      "Le chargeur est-il fourni ? Quel est l'état de la batterie ?");
  }
  if (/dyson|aspirateur|roborock|roomba/.test(cat)) {
    q.push("La batterie est-elle d'origine ? Combien de temps tient-elle ?", "Le chargeur et les accessoires sont-ils fournis ?");
  }
  if (/sonos|bose|audio|enceinte|marshall|jbl/.test(cat)) {
    q.push("L'appareil est-il bien retiré de votre compte (Sonos, Bose…) ?", "Le câble d'alimentation est-il fourni ?");
  }
  if (/tv|écran|ecran|moniteur|televiseur/.test(cat)) {
    q.push("Y a-t-il du son sans image ? La dalle a-t-elle un choc ou une fissure ?", "La télécommande et le pied sont-ils fournis ?");
  }
  if (/makita|dewalt|milwaukee|outil|bosch/.test(cat)) {
    q.push("La machine fonctionne-t-elle sur une batterie chargée ? Odeur de brûlé ou étincelles ?");
  }
  if (ad.price_mode === "enchere") q.push("Accepteriez-vous une vente immédiate avant la fin de l'enchère ?");
  return q;
}

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
  const seen = ad.seen_at ? new Date(ad.seen_at.endsWith("Z") || ad.seen_at.includes("+") ? ad.seen_at : ad.seen_at + "Z") : null;
  const buy = ad.shipping ? `${euros(ad.price)} + ${euros(ad.shipping)} de port` : euros(ad.total_price ?? ad.price);
  const desc = String(ad.description || "").trim();
  $("#details-body").innerHTML = `
    ${ad.image ? `<img class="d-photo" src="${esc(ad.image)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()">` : ""}
    <h3>${esc(ad.title)}</h3>
    <div class="d-grid">
      ${row("🩺 Panne probable", esc(faultText(a)))}
      ${row("📍 Ville", esc(cleanPlace(ad.location)))}
      ${row("🏷️ Type de vente", ad.price_mode === "enchere" ? "Enchère (prix provisoire)" : "Prix fixe")}
      ${row("💶 Achat", buy)}
      ${row("🔩 Pièce estimée", a.cout_piece_estime ? euros(a.cout_piece_estime) : "")}
      ${row("📈 Revente estimée", a.valeur_revente_estimee != null ? euros(a.valeur_revente_estimee) : "")}
      ${row("🧮 Marge nette", a.marge_nette_estimee != null ? `${a.marge_nette_estimee >= 0 ? "+" : ""}${euros(a.marge_nette_estimee)}` : "")}
      ${sellerRows(ad.seller, row)}
      ${row("✨ Signaux repérés", esc((ad.signals || []).join(", ")))}
      ${row("🛒 Site", `${siteLogo(ad.source, 14)} ${esc(siteName(ad.source))}${ad.category ? ` · ${esc(ad.category)}` : ""}`)}
      ${row("🕒 Repérée", seen && !isNaN(seen) ? seen.toLocaleString("fr-FR", { dateStyle: "medium", timeStyle: "short" }) : "")}
      ${row("✗ Écartée", esc(ad.reason || ""))}
    </div>
    ${desc ? `<details class="d-desc"><summary>Description du vendeur</summary><p>${esc(desc.slice(0, 1200))}${desc.length > 1200 ? "…" : ""}</p></details>` : ""}
    <div class="d-questions"><b>💬 Questions à poser au vendeur</b><ul>${sellerQuestions(ad).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>
    <a class="primary d-open" href="${esc(ad.url)}" target="_blank" rel="noopener">Voir l'annonce ↗</a>`;
  $("#details").showModal();
}

$("#ads").addEventListener("click", (e) => {
  const button = e.target.closest("[data-info]");
  if (button) showDetails(button.dataset.info);
});
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
  $("#summary").textContent = ads.length
    ? `${ads.length} annonce(s) sur ${allAds.length} · ${first + 1}–${Math.min(first + PAGE_SIZE, ads.length)} affichées`
    : `0 annonce sur ${allAds.length}`;
  $("#ads").innerHTML = ads.length
    ? ads.slice(first, first + PAGE_SIZE).map(adCard).join("")
    : allAds.length
      ? '<div class="empty">Aucune annonce ne correspond à ces filtres. <button class="link" data-reset-filters>Réinitialiser les filtres</button></div>'
      : '<div class="empty"><b>Aucune annonce pour l\'instant.</b><br>Cliquez sur <b>▶ Lancer un passage</b> en haut à droite pour lancer une première recherche.</div>';
  renderPager(pages);
  loadMissingImages();
}

// Annonces sans photo : on demande l'aperçu de leur lien (une à la fois, mémorisé côté serveur).
let imageQueue = Promise.resolve();
function loadMissingImages() {
  document.querySelectorAll("#ads [data-need-image]").forEach((card) => {
    card.removeAttribute("data-need-image");
    const uid = card.dataset.uid;
    imageQueue = imageQueue.then(async () => {
      const data = await api(`/api/image/${encodeURIComponent(uid)}`).catch(() => ({}));
      const ad = allAds.find((a) => a.uid === uid);
      if (ad) ad.image = data.image || "";
      const live = document.querySelector(`#ads [data-uid="${CSS.escape(uid)}"] .thumb .placeholder`);
      if (data.image && live) {
        live.outerHTML = `<img src="${esc(data.image)}" alt="" loading="lazy" referrerpolicy="no-referrer"
          onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'placeholder',textContent:'📦'}))">`;
      }
    });
  });
}

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

FILTER_IDS.forEach((id) =>
  $("#" + id).addEventListener($("#" + id).tagName === "INPUT" ? "input" : "change", () => {
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
$("#reset-config").addEventListener("click", async () => {
  if (!confirm("Remplacer votre configuration (recherches, catégories, filtres, réglages) par la configuration d'exemple ?\n\nUne copie de l'actuelle est gardée dans le dossier du projet.")) return;
  try {
    const data = await api("/api/settings/reset", { method: "POST" });
    await loadSettings();
    loadStatus();
    toast(data.backup ? `✅ Configuration d'exemple chargée (ancienne gardée : ${data.backup})` : "✅ Configuration d'exemple chargée");
  } catch (e) { toast("❌ " + e.message); }
});
$("#discard").addEventListener("click", () => { if (confirm("Annuler les modifications non enregistrées ?")) loadSettings(); });

// ---------- État de l'installation ----------
async function loadStatus() {
  const s = await api("/api/status").catch(() => null);
  if (!s) return;
  const items = [
    { ok: s.anthropic, text: s.anthropic ? "Clé Anthropic présente : les annonces sont notées par l'IA"
      : "Clé Anthropic manquante : ajoutez ANTHROPIC_API_KEY dans .env, sinon aucune annonce n'est notée" },
    { ok: s.channels.length > 0, text: s.channels.length ? `Notifications : ${s.channels.join(" + ")}`
      : "Aucune notification : ajoutez TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID (ou DISCORD_WEBHOOK_URL) dans .env" },
    ...s.sites.map((site) => ({ ok: site.ok, text: site.ok ? `${site.label} : lancé à chaque passage` : `${site.label} ignoré : ${site.reason}` })),
  ];
  if (s.error) items.push({ ok: false, text: `config.yaml illisible : ${s.error}` });
  $("#status-list").innerHTML = items.map((i) => `<li class="${i.ok ? "ok" : "ko"}">${i.ok ? "✓" : "⚠"} ${esc(i.text)}</li>`).join("");

  // Bandeau sur l'onglet Annonces : seulement les problèmes, masquable.
  const problems = items.filter((i) => !i.ok);
  const key = problems.map((p) => p.text).join("|");
  let hidden = "";
  try { hidden = localStorage.getItem("dealsniper-setup-hidden") || ""; } catch (e) { /* stockage indisponible */ }
  $("#setup").hidden = !problems.length || hidden === key;
  $("#setup").innerHTML = `<div><b>À régler pour profiter de tout Deal Sniper</b>
      <ul>${problems.map((p) => `<li>${esc(p.text)}</li>`).join("")}</ul>
      <small>Après avoir modifié <code>.env</code>, relancez <code>start_ui.bat</code>.</small></div>
    <button class="icon" title="Masquer" data-hide-setup>✕</button>`;
  $("#setup [data-hide-setup]").addEventListener("click", () => {
    $("#setup").hidden = true;
    try { localStorage.setItem("dealsniper-setup-hidden", key); } catch (e) { /* idem */ }
  });
}

restoreFilters();
countActiveFilters();
loadStatus();
try { showTab(localStorage.getItem("dealsniper-tab") || "ads"); } catch (e) { showTab("ads"); }
loadAds();
setInterval(loadAds, 60000);
