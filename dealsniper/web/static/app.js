// Deal Sniper — interface locale. Les annonces viennent de /api/ads, les réglages de /api/settings.

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const euros = (v) => (v == null ? "?" : `${Number(v).toLocaleString("fr-FR", { maximumFractionDigits: 2 })} €`);
const FILTER_IDS = ["f-text", "f-status", "f-site", "f-category", "f-type", "f-score", "f-price", "f-margin", "f-days", "f-sort", "f-notified"];
const SEARCH_FIELDS = { ebay: ["query", "condition", "max_price"], default: ["query", "category", "max_price"] };

let allAds = [];
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
document.querySelectorAll(".tab").forEach((btn) =>
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    $("#tab-ads").hidden = btn.dataset.tab !== "ads";
    $("#tab-settings").hidden = btn.dataset.tab !== "settings";
    if (btn.dataset.tab === "settings" && !settings) loadSettings();
  })
);

// ---------- Annonces ----------
function saveFilters() {
  try {
    const values = Object.fromEntries(FILTER_IDS.map((id) => [id, $("#" + id).type === "checkbox" ? $("#" + id).checked : $("#" + id).value]));
    localStorage.setItem("dealsniper-filters", JSON.stringify(values));
  } catch (e) { /* stockage indisponible : sans importance */ }
}

function restoreFilters() {
  try {
    const values = JSON.parse(localStorage.getItem("dealsniper-filters") || "{}");
    for (const [id, value] of Object.entries(values)) {
      const el = $("#" + id);
      if (!el) continue;
      if (el.type === "checkbox") el.checked = value; else el.value = value;
    }
  } catch (e) { /* idem */ }
}

function fillSelect(id, values) {
  const el = $(id);
  const current = el.value;
  const first = el.options[0].outerHTML;
  el.innerHTML = first + [...values].sort().map((v) => `<option>${esc(v)}</option>`).join("");
  if ([...values].includes(current)) el.value = current;
}

async function loadAds() {
  try {
    const data = await api(`/api/ads?status=${encodeURIComponent($("#f-status").value)}&days=${$("#f-days").value}`);
    allAds = data.ads;
    $("#cost").textContent = `Coût IA aujourd'hui : ${data.cost_today} $`;
    $("#run").disabled = data.running;
    $("#run").textContent = data.running ? "⏳ Passage en cours…" : "▶ Lancer un passage";
    if (data.error) toast("Dernier passage en erreur : " + data.error);
    fillSelect("#f-site", new Set(allAds.map((a) => a.source).filter(Boolean)));
    fillSelect("#f-category", new Set(allAds.map((a) => a.category).filter(Boolean)));
    renderAds();
  } catch (e) {
    $("#ads").innerHTML = `<div class="empty">Impossible de charger les annonces : ${esc(e.message)}</div>`;
  }
}

function filteredAds() {
  const text = $("#f-text").value.trim().toLowerCase();
  const site = $("#f-site").value, category = $("#f-category").value, type = $("#f-type").value;
  const minScore = Number($("#f-score").value || 0);
  const maxPrice = $("#f-price").value === "" ? null : Number($("#f-price").value);
  const minMargin = $("#f-margin").value === "" ? null : Number($("#f-margin").value);
  const notifiedOnly = $("#f-notified").checked;

  const ads = allAds.filter((ad) => {
    const a = ad.analysis || {};
    if (text && !`${ad.title} ${ad.description} ${ad.location} ${ad.category}`.toLowerCase().includes(text)) return false;
    if (site && ad.source !== site) return false;
    if (category && ad.category !== category) return false;
    if (type && a.type_affaire !== type) return false;
    if (minScore && (ad.score ?? 0) < minScore) return false;
    if (maxPrice != null && (ad.total_price ?? Infinity) > maxPrice) return false;
    if (minMargin != null && (a.marge_nette_estimee ?? -Infinity) < minMargin) return false;
    if (notifiedOnly && !ad.notified) return false;
    return true;
  });

  const sorters = {
    recent: (x, y) => (y.seen_at || "").localeCompare(x.seen_at || ""),
    score: (x, y) => (y.score ?? -1) - (x.score ?? -1),
    margin: (x, y) => ((y.analysis || {}).marge_nette_estimee ?? -1e9) - ((x.analysis || {}).marge_nette_estimee ?? -1e9),
    price: (x, y) => (x.total_price ?? 1e9) - (y.total_price ?? 1e9),
  };
  return ads.sort(sorters[$("#f-sort").value] || sorters.recent);
}

function adCard(ad) {
  const a = ad.analysis || {};
  const scoreClass = ad.score >= 7 ? "s-good" : ad.score >= 5 ? "s-mid" : "s-low";
  const score = ad.score != null ? `<span class="score ${scoreClass}">${ad.score}</span>` : "";
  const type = a.type_affaire === "revente_directe" ? "💎 Revente directe" : a.type_affaire ? "🔧 Réparation" : "";
  const shipping = ad.shipping ? ` <span class="muted">+ ${euros(ad.shipping)} port</span>` : "";
  const seller = ad.seller && ad.seller.positive_pct != null ? `<span class="tag">👤 ${ad.seller.positive_pct} % · ${ad.seller.reviews ?? "?"} avis</span>` : "";
  const when = ad.seen_at ? new Date(ad.seen_at).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" }) : "";
  return `<article class="ad ${ad.status}">
    <div class="top">${score}<a class="title" href="${esc(ad.url)}" target="_blank" rel="noopener">${esc(ad.title)}</a></div>
    <div><span class="price">${euros(ad.price)}</span>${shipping}${ad.price_mode === "enchere" ? ' <span class="tag">enchère</span>' : ""}</div>
    <div class="tags">
      <span class="tag">${esc(ad.source)}</span>${ad.category ? `<span class="tag">${esc(ad.category)}</span>` : ""}
      ${ad.location ? `<span class="tag">📍 ${esc(ad.location)}</span>` : ""}${type ? `<span class="tag">${type}</span>` : ""}
      ${a.confiance ? `<span class="tag">confiance ${esc(a.confiance)}</span>` : ""}${seller}${ad.notified ? '<span class="tag">🔔 notifiée</span>' : ""}
    </div>
    ${a.marge_nette_estimee != null ? `<div>💰 Marge ≈ <b>${euros(a.marge_nette_estimee)}</b> · revente ≈ ${euros(a.valeur_revente_estimee)}${a.panne_probable && a.panne_probable !== "aucune" ? ` · 🔧 ${esc(a.panne_probable)}` : ""}</div>` : ""}
    ${a.justification_courte ? `<div class="muted">${esc(a.justification_courte)}</div>` : ""}
    ${a.question_vendeur ? `<div>❓ ${esc(a.question_vendeur)}</div>` : ""}
    ${ad.reason ? `<div class="reason">✗ ${esc(ad.reason)}</div>` : ""}
    <div class="muted">${esc(when)}</div>
  </article>`;
}

function renderAds() {
  const ads = filteredAds();
  $("#summary").textContent = `${ads.length} annonce(s) affichée(s) sur ${allAds.length}`;
  $("#ads").innerHTML = ads.length ? ads.slice(0, 500).map(adCard).join("") : '<div class="empty">Aucune annonce ne correspond à ces filtres.</div>';
}

FILTER_IDS.forEach((id) =>
  $("#" + id).addEventListener(id === "f-text" ? "input" : "change", () => {
    saveFilters();
    if (id === "f-status" || id === "f-days") loadAds(); else renderAds();
  })
);

$("#run").addEventListener("click", async () => {
  try {
    await api("/api/run", { method: "POST" });
    toast("Passage lancé…");
    $("#run").disabled = true;
    const poll = setInterval(async () => {
      await loadAds();
      if (!$("#run").disabled) { clearInterval(poll); toast("Passage terminé"); }
    }, 5000);
  } catch (e) { toast(e.message); }
});

$("#test-notif").addEventListener("click", async () => {
  const data = await api("/api/test-notif", { method: "POST" }).catch((e) => ({ ok: false, error: e.message }));
  toast(data.ok ? "Notification envoyée ✅" : "Échec : vérifiez .env ❌");
});

// ---------- Réglages ----------
const words = (list) => (list || []).join(", ");

function searchRow(siteName, row) {
  const fields = SEARCH_FIELDS[siteName] || SEARCH_FIELDS.default;
  const cells = fields.map((f) => {
    if (f === "condition") {
      const opts = ["any", "parts", "used"].map((o) => `<option ${row[f] === o ? "selected" : ""}>${o}</option>`).join("");
      return `<td><select data-field="condition">${opts}</select></td>`;
    }
    const type = f === "max_price" ? "number" : "text";
    return `<td><input data-field="${f}" type="${type}" value="${esc(row[f] ?? "")}" placeholder="${f}"></td>`;
  });
  return `<tr>${cells.join("")}<td><button class="danger" data-remove>✕</button></td></tr>`;
}

function renderSites() {
  $("#sites").innerHTML = Object.entries(settings.sites).map(([name, site]) => {
    const fields = SEARCH_FIELDS[name] || SEARCH_FIELDS.default;
    const interval = site.interval || ["", ""];
    return `<div class="card" data-site="${esc(name)}">
      <div class="card-head">
        <strong>${esc(name)}</strong>
        <label class="inline"><input type="checkbox" data-enabled ${site.enabled !== false ? "checked" : ""}> activé</label>
        <label class="inline">intervalle <input type="number" data-interval="0" value="${esc(interval[0])}"> à
          <input type="number" data-interval="1" value="${esc(interval[1])}"> s</label>
      </div>
      <table><thead><tr>${fields.map((f) => `<th>${f}</th>`).join("")}<th></th></tr></thead>
        <tbody>${(site.searches || []).map((r) => searchRow(name, r)).join("")}</tbody></table>
      <button data-add-search>+ Recherche</button>
    </div>`;
  }).join("");
}

function categoryCard(cat) {
  return `<div class="card category">
    <div class="cat-grid">
      <input data-field="name" value="${esc(cat.name)}" placeholder="Nom">
      <input data-field="max_budget" type="number" value="${esc(cat.max_budget ?? "")}" placeholder="Budget €">
    </div>
    <label>Mots-clés<textarea data-field="keywords" rows="2">${esc(words(cat.keywords))}</textarea></label>
    <label>Exclusions<textarea data-field="exclude_keywords" rows="1">${esc(words(cat.exclude_keywords))}</textarea></label>
    <label>Signaux positifs<textarea data-field="include_keywords" rows="1">${esc(words(cat.include_keywords))}</textarea></label>
    <label class="inline"><input type="checkbox" data-field="require_include" ${cat.require_include ? "checked" : ""}> signal obligatoire</label>
    <button class="danger" data-remove-category>Supprimer</button>
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
  $("#n-min").value = n.min_score ?? 7;
  $("#n-watch").value = n.watch_score ?? 5;
  const types = n.deal_types || ["reparation", "revente_directe"];
  $("#n-repair").checked = types.includes("reparation");
  $("#n-direct").checked = types.includes("revente_directe");
  $("#a-model").value = a.model || "";
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
      interval: [...card.querySelectorAll("[data-interval]")].map((i) => i.value).filter((v) => v !== ""),
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
               buyer_ad_keywords: $("#g-buyer").value, min_price: $("#g-minprice").value },
    location: { france_only: $("#g-france").checked, exclude_overseas: $("#g-overseas").checked },
    notifier: { min_score: $("#n-min").value, watch_score: $("#n-watch").value, deal_types: dealTypes },
    analyzer: { model: $("#a-model").value, max_daily_cost_usd: $("#a-cap").value },
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
loadAds();
setInterval(loadAds, 60000);
