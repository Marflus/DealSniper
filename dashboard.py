"""
dashboard.py — Petite interface web locale pour Deal Sniper.

    python dashboard.py            # puis ouvrir http://127.0.0.1:5000

Fonctions :
  * tableau des annonces analysées (tri par score, filtres catégorie / score / texte) ;
  * statistiques : coût API du jour et du mois, nombre d'annonces, bonnes affaires ;
  * bouton « Lancer un passage » (exécute main.run en arrière-plan) ;
  * bouton « Tester les notifications ».

L'interface n'écoute que sur 127.0.0.1 par défaut (aucune authentification :
ne l'exposez pas sur Internet). Variable DASHBOARD_HOST pour changer l'adresse.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template_string, request

import main as pipeline
from core import env, load_config, load_env, setup_logging
from storage import DealsStore, RunLog

app = Flask(__name__)
log = logging.getLogger("dealsniper.dashboard")

_state = {"running": False, "last": None, "error": None}
_state_lock = threading.Lock()


def _background_run(no_ai: bool) -> None:
    try:
        _state["last"] = pipeline.run(no_ai=no_ai)
        _state["error"] = None
    except Exception as exc:  # affiché dans l'interface
        log.exception("Échec du passage")
        _state["error"] = str(exc)
    finally:
        with _state_lock:
            _state["running"] = False


@app.get("/")
def index():
    config = load_config()
    deals = DealsStore().items
    runs = RunLog().items
    now = datetime.now(timezone.utc)
    today, month = now.date().isoformat(), now.strftime("%Y-%m")
    min_score = int(config["notifier"].get("min_score", 7))
    stats = {
        "total": len(deals),
        "good": sum(1 for d in deals if (d.get("analysis") or {}).get("score_rentabilite", 0) >= min_score),
        "cost_today": sum(r.get("cost_usd", 0) for r in runs if str(r.get("started_at", "")).startswith(today)),
        "cost_month": sum(r.get("cost_usd", 0) for r in runs if str(r.get("started_at", "")).startswith(month)),
        "last_run": runs[0] if runs else None,
    }
    categories = sorted({d.get("category") or "?" for d in deals})
    return render_template_string(
        TEMPLATE, deals=deals, stats=stats, categories=categories, min_score=min_score,
        model=config["analyzer"]["model"], state=_state,
    )


@app.post("/run")
def run_now():
    no_ai = request.args.get("no_ai") == "1"
    with _state_lock:
        if _state["running"]:
            return jsonify(ok=False, message="Un passage est déjà en cours."), 409
        _state["running"] = True
    threading.Thread(target=_background_run, args=(no_ai,), daemon=True).start()
    return jsonify(ok=True, message="Passage lancé…")


@app.get("/status")
def status():
    return jsonify(running=_state["running"], last=_state["last"], error=_state["error"])


@app.post("/test-notif")
def test_notif():
    code = pipeline.test_notifications()
    return jsonify(ok=code == 0, message="Notification envoyée ✅" if code == 0 else "Échec : vérifiez .env ❌")


TEMPLATE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Deal Sniper</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --fg:#1d2330; --muted:#6b7280; --line:#e5e7eb;
          --accent:#2563eb; --good:#16a34a; --mid:#d97706; --bad:#9ca3af; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0f1218; --card:#181c25; --fg:#e7e9ee; --muted:#9aa3b2; --line:#2a3040; --accent:#60a5fa; }
  }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--fg); }
  header { padding:16px 20px; display:flex; flex-wrap:wrap; gap:12px; align-items:center; justify-content:space-between; }
  h1 { margin:0; font-size:20px; } h1 small { color:var(--muted); font-weight:400; font-size:13px; }
  main { padding:0 20px 40px; max-width:1300px; margin:0 auto; }
  .stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin-bottom:16px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }
  .card b { display:block; font-size:22px; } .card span { color:var(--muted); font-size:12px; }
  .bar { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px; }
  input, select, button { font:inherit; padding:7px 10px; border-radius:8px; border:1px solid var(--line); background:var(--card); color:var(--fg); }
  button { cursor:pointer; } button.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
  button:disabled { opacity:.6; cursor:wait; }
  .table-wrap { overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:10px; }
  table { width:100%; border-collapse:collapse; min-width:900px; }
  th, td { padding:9px 10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }
  th { font-size:12px; color:var(--muted); cursor:pointer; user-select:none; white-space:nowrap; }
  td.num { text-align:right; white-space:nowrap; }
  .score { display:inline-block; min-width:34px; text-align:center; font-weight:700; border-radius:6px; padding:2px 6px; color:#fff; }
  .s-good { background:var(--good); } .s-mid { background:var(--mid); } .s-bad { background:var(--bad); }
  a { color:var(--accent); text-decoration:none; } a:hover { text-decoration:underline; }
  .muted { color:var(--muted); font-size:12px; }
  .tag { font-size:11px; border:1px solid var(--line); border-radius:999px; padding:1px 7px; margin-right:4px; color:var(--muted); }
  #toast { position:fixed; bottom:16px; right:16px; background:var(--fg); color:var(--bg); padding:10px 14px; border-radius:8px; display:none; }
  .empty { padding:40px; text-align:center; color:var(--muted); }
</style>
</head>
<body>
<header>
  <h1>🎯 Deal Sniper <small>modèle : {{ model }} · seuil : {{ min_score }}/10</small></h1>
  <div class="bar" style="margin:0">
    <button id="btn-run" class="primary" onclick="runNow(false)" {% if state.running %}disabled{% endif %}>▶ Lancer un passage</button>
    <button onclick="runNow(true)">Collecte sans IA</button>
    <button onclick="testNotif()">🔔 Tester les notifications</button>
  </div>
</header>
<main>
  <div class="stats">
    <div class="card"><b>{{ stats.total }}</b><span>annonces analysées</span></div>
    <div class="card"><b>{{ stats.good }}</b><span>bonnes affaires (≥ {{ min_score }})</span></div>
    <div class="card"><b>{{ "%.4f"|format(stats.cost_today) }} $</b><span>coût API aujourd'hui</span></div>
    <div class="card"><b>{{ "%.3f"|format(stats.cost_month) }} $</b><span>coût API ce mois</span></div>
    <div class="card"><b style="font-size:16px">{{ (stats.last_run.started_at[:16].replace('T',' ') + ' UTC') if stats.last_run else '—' }}</b>
      <span>dernier passage{% if stats.last_run %} · {{ stats.last_run.get('collected',0) }} collectées, {{ stats.last_run.get('notified',0) }} notifiées{% endif %}</span></div>
  </div>

  <div class="bar">
    <input id="q" type="search" placeholder="Rechercher…" oninput="applyFilters()">
    <select id="cat" onchange="applyFilters()"><option value="">Toutes catégories</option>
      {% for c in categories %}<option>{{ c }}</option>{% endfor %}</select>
    <select id="minscore" onchange="applyFilters()">
      <option value="0">Tous les scores</option>
      {% for s in [5,6,7,8,9] %}<option value="{{ s }}" {% if s == min_score %}selected{% endif %}>Score ≥ {{ s }}</option>{% endfor %}
    </select>
  </div>

  <div class="table-wrap">
  {% if deals %}
  <table id="deals">
    <thead><tr>
      <th onclick="sortBy(0,true)">Score</th><th onclick="sortBy(1)">Annonce</th>
      <th onclick="sortBy(2,true)">Prix total</th><th>Panne probable</th>
      <th onclick="sortBy(4,true)">Pièce</th><th onclick="sortBy(5,true)">Revente</th>
      <th onclick="sortBy(6,true)">Marge</th><th onclick="sortBy(7)">Date</th>
    </tr></thead>
    <tbody>
    {% for d in deals %}{% set a = d.analysis or {} %}{% set s = a.get('score_rentabilite', 0) %}
      <tr data-cat="{{ d.category or '?' }}" data-score="{{ s }}">
        <td data-v="{{ s }}"><span class="score {{ 's-good' if s >= min_score else ('s-mid' if s >= 5 else 's-bad') }}">{{ s }}</span></td>
        <td data-v="{{ d.title }}"><a href="{{ d.url }}" target="_blank" rel="noopener">{{ d.title }}</a>
          <div class="muted"><span class="tag">{{ d.category }}</span><span class="tag">{{ d.source }}</span>
          {% if d.get('notified') %}<span class="tag">🔔 notifiée</span>{% endif %}
          {{ a.get('justification_courte','') }}</div></td>
        <td class="num" data-v="{{ d.total_price if d.total_price is not none else 99999 }}">
          {{ ('%.0f €'|format(d.total_price)) if d.total_price is not none else '?' }}
          {% if d.shipping %}<div class="muted">dont {{ '%.0f'|format(d.shipping) }} € port</div>{% endif %}</td>
        <td>{{ a.get('panne_probable','') }}</td>
        <td class="num" data-v="{{ a.get('cout_piece_estime',0) }}">{{ '%.0f €'|format(a.get('cout_piece_estime',0)) }}</td>
        <td class="num" data-v="{{ a.get('valeur_revente_estimee',0) }}">{{ '%.0f €'|format(a.get('valeur_revente_estimee',0)) }}</td>
        <td class="num" data-v="{{ a.get('marge_nette_estimee',0) }}"><b>{{ '%+.0f €'|format(a.get('marge_nette_estimee',0)) }}</b></td>
        <td class="muted" data-v="{{ d.collected_at }}">{{ d.collected_at[:16].replace('T',' ') }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% else %}
    <div class="empty">Aucune annonce analysée pour l'instant. Lancez un passage ↗</div>
  {% endif %}
  </div>
</main>
<div id="toast"></div>
<script>
function toast(msg){ const t=document.getElementById('toast'); t.textContent=msg; t.style.display='block'; setTimeout(()=>t.style.display='none',4000); }
function applyFilters(){
  const q=document.getElementById('q').value.toLowerCase(), cat=document.getElementById('cat').value,
        min=+document.getElementById('minscore').value;
  document.querySelectorAll('#deals tbody tr').forEach(tr=>{
    const ok=(!cat||tr.dataset.cat===cat)&&(+tr.dataset.score>=min)&&(!q||tr.textContent.toLowerCase().includes(q));
    tr.style.display=ok?'':'none';
  });
}
let sortState={};
function sortBy(col,numeric){
  const tb=document.querySelector('#deals tbody'); if(!tb) return;
  const dir=sortState[col]=!sortState[col];
  [...tb.rows].sort((a,b)=>{ let x=a.cells[col].dataset.v, y=b.cells[col].dataset.v;
      if(numeric){x=+x;y=+y;} return (x>y?1:x<y?-1:0)*(dir?-1:1); }).forEach(r=>tb.appendChild(r));
}
async function runNow(noAi){
  const btn=document.getElementById('btn-run'); btn.disabled=true; btn.textContent='⏳ En cours…';
  const r=await fetch('/run'+(noAi?'?no_ai=1':''),{method:'POST'}); const j=await r.json(); toast(j.message);
  const poll=setInterval(async()=>{ const s=await (await fetch('/status')).json();
    if(!s.running){ clearInterval(poll); if(s.error) toast('Erreur : '+s.error); location.reload(); } },2000);
}
async function testNotif(){ const j=await (await fetch('/test-notif',{method:'POST'})).json(); toast(j.message); }
applyFilters();
{% if state.running %}runNow.poll=setInterval(async()=>{const s=await (await fetch('/status')).json(); if(!s.running) location.reload();},2000);{% endif %}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    load_env()
    setup_logging()
    host = env("DASHBOARD_HOST", "127.0.0.1")
    port = int(env("DASHBOARD_PORT", "5000"))
    print(f"Deal Sniper : interface sur http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
