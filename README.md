# 🎯 Deal Sniper

Veille automatique de **bonnes affaires électroniques en France** : objets **à réparer** (PC sans SSD, console qui
surchauffe, TV au rétroéclairage HS…) ou **sous leur cote** (revente directe).

Deal Sniper interroge des sites (un fichier par site ; aujourd'hui l'API officielle eBay), écarte gratuitement ce qui ne vous intéresse pas, fait noter
le reste par **Claude** et vous prévient sur **Telegram / Discord**.

## Comment ça marche

```
 sites/ebay.py ─┐
 sites/…       ─┼─► engine.py ─► filtre ─► fiche détaillée ─► filtre ─► Claude ─► Telegram / Discord
                ─┘   (commun)    (gratuit)   (si nouvelle)              (note /10)
                         └──────────── data/dealsniper.db (annonces déjà vues, coûts, pauses) ───┘
```

Pour chaque site, à chaque passage :
1. **liste** des annonces récentes ;
2. on garde les **nouvelles** (jamais vues) ;
3. **filtre gratuit** : catégorie, budget, France uniquement, mots interdits (« oxydé », « iCloud »…) ;
4. **fiche détaillée**, seulement pour les nouvelles retenues, puis nouveau filtre sur la description ;
5. **Claude** note de 1 à 10, estime la marge, donne une question à poser au vendeur ;
6. **notification** : un message par annonce ≥ 7/10, un récapitulatif 👀 pour celles à 5-6/10.

```
Dell Latitude E5530 sans système d'exploitation
💶 Achat : 20 €
📈 Revente : 180 € (marge +143 €)
🔧 Panne : système d'exploitation absent
🔗 Voir l'annonce
```

Un site qui refuse l'accès (HTTP 401/403, robots.txt) est mis en pause 6 h et vous êtes prévenu : rien n'est
contourné. Les requêtes sont espacées et identifiées honnêtement (`DealSniper/3.0`).

## Arborescence

```
DealSniper/
├── config.yaml               ← tous les réglages (sites, recherches, budgets, mots-clés, seuils)
├── .env.example              ← modèle des secrets (à copier en .env)
├── dealsniper/
│   ├── cli.py                commandes : run, watch, ui, test-notif, recap, reset
│   ├── engine.py             moteur commun (les 6 étapes ci-dessus)
│   ├── sites/                ← UN FICHIER PAR SITE
│   │   ├── base.py           interface commune (list_recent / fetch_detail)
│   │   ├── ebay.py           eBay, API officielle
│   │   ├── _template.py      modèle à copier pour ajouter un site
│   │   └── __init__.py       liste des sites (SITE_MODULES)
│   ├── filters.py            filtre gratuit
│   ├── analyzer.py           notation par Claude
│   ├── notifier.py           Telegram / Discord
│   ├── db.py                 base SQLite
│   ├── http.py               client HTTP (délais, robots.txt, 429, 403)
│   ├── config.py, models.py
│   ├── web/                  interface locale (app.py + static/)
│   └── utils/                prix, texte, URL, localisation
├── tests/                    python -m pytest
├── deploy/dealsniper.service service systemd (Raspberry Pi)
└── .github/workflows/        passage planifié sur GitHub (sans serveur) + tests
```

## Ajouter un site

1. `cp dealsniper/sites/_template.py dealsniper/sites/mon_site.py`
2. Dans ce fichier : changez `name = "mon_site"`, écrivez `list_recent` (la liste des annonces : titre, prix,
   URL) et, si utile, `fetch_detail` (la description d'une annonce).
3. Ajoutez-le dans `SITE_MODULES` (`dealsniper/sites/__init__.py`).
4. Ajoutez un bloc `mon_site:` sous `sites:` dans `config.yaml`.

Tout le reste (déjà vu, filtres, Claude, notifications, pauses) est automatique. Préférez une API ou un flux RSS
quand le site en propose un ; pour une page HTML, utilisez `http.get(url, check_robots=True)`.

## Installation

```bash
git clone https://github.com/Marflus/DealSniper.git && cd DealSniper
python3 -m venv .venv && source .venv/bin/activate      # Windows : .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env                                     # puis remplissez les secrets
python -m dealsniper test-notif                          # vérifie Telegram / Discord
python -m dealsniper run                                 # un passage
```

**Secrets** (`.env`, ou *Settings → Secrets and variables → Actions* sur GitHub) :
`ANTHROPIC_API_KEY`, `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
(et/ou `DISCORD_WEBHOOK_URL`).

**Clés eBay** : compte gratuit sur [developer.ebay.com](https://developer.ebay.com) → *Application Keys* →
jeu de clés **Production** (App ID = `EBAY_CLIENT_ID`, Cert ID = `EBAY_CLIENT_SECRET`). Pour la notification de
suppression de compte, choisissez l'exemption *« Not persisting eBay data »*.

**Telegram** : @BotFather → `/newbot` → token ; envoyez un message à votre bot puis ouvrez
`https://api.telegram.org/bot<TOKEN>/getUpdates` pour lire votre `chat.id`.

## Interface

```bash
python -m dealsniper ui                  # puis http://127.0.0.1:5000
python -m dealsniper ui --host 0.0.0.0   # accessible depuis le téléphone sur le même Wi-Fi
```

- **Annonces** : cartes avec photo, note, barre Achat / Revente / Marge, panne suspectée, puis lieu, type de vente,
  site et catégorie. Filtres : texte, notées / écartées / toutes, site, catégorie, type, note, prix, marge, période ;
  tri par date, note, marge ou prix.
- **Recherches & filtres** : recherches de chaque site, catégories et budgets, mots interdits, seuils de
  notification, modèle et plafond de dépense. « Enregistrer » écrit dans `config.yaml` en gardant les commentaires.
- Boutons **Lancer un passage** et **Test notif**.

L'interface n'a pas de mot de passe : ne l'exposez pas sur Internet.

## Commandes

```bash
python -m dealsniper run            # un passage de chaque site (commande par défaut)
python -m dealsniper watch          # surveillance continue, chaque site à son rythme (serveur / Raspberry Pi)
python -m dealsniper ui             # interface web locale
python -m dealsniper test-notif     # fausse annonce sur Telegram / Discord
python -m dealsniper recap          # annonces notées ces 24 dernières heures (--hours N)
python -m dealsniper reset          # oublie les annonces déjà vues
python -m dealsniper -v run         # logs détaillés (au lieu des lignes de progression)
```

Pendant un passage, le terminal (et l'interface web) affiche une ligne par site, mise à jour en direct :

```
eBay : Recherche "thinkpad" — 25 annonces /
Leboncoin : 50 annonces · 6 nouvelles · 2 analysées · 0 notifiées
```

Un connecteur alimente cette ligne en appelant `self.report(requete, nb_annonces)` après chaque recherche.
Chaque annonce notée reçoit aussi une **facilité de réparation** de 1 à 10 (10 = aucune réparation ou pièce
qui se change sans outil, 1 = soudure ou panne inconnue), visible dans l'interface et sur Telegram.

## Où le faire tourner

**GitHub Actions** (rien à installer) : `.github/workflows/dealsniper.yml` lance `run` toutes les 30 min de 6 h
à 23 h ; la base est conservée dans le cache GitHub. Lancement manuel : *Actions → Deal Sniper → Run workflow*
(modes `run`, `test-notif`, `recap`). GitHub retarde ou saute parfois des passages planifiés.

**Raspberry Pi / serveur Linux** (plus réactif) :
```bash
sudo cp deploy/dealsniper.service /etc/systemd/system/    # adaptez User et chemins
sudo systemctl daemon-reload && sudo systemctl enable --now dealsniper
journalctl -u dealsniper -f
```
Dans ce cas, supprimez le bloc `schedule:` du workflow GitHub pour ne pas analyser deux fois.

## Réglages principaux (`config.yaml`)

| Section | Rôle |
|---|---|
| `sites` | un bloc par site : `enabled`, `interval`, et ses réglages (recherches eBay…) |
| `network` | identification, délai entre requêtes, pause après un refus |
| `location` | France uniquement, DOM-TOM exclus |
| `filters`, `categories` | mots interdits, budgets par catégorie, signaux positifs |
| `analyzer` | modèle Claude, taille des lots, plafond de dépense (1 $/jour par défaut) |
| `notifier` | seuils : 7/10 = bonne affaire, 5/10 = à vérifier |

**Coût** : environ 0,001 $ par annonce analysée avec `claude-haiku-4-5` (10 fois moins avec `claude-haiku-5-5`).

## Sites sans API

leboncoin, Vinted et Facebook Marketplace n'ont pas d'API publique et bloquent les robots. Deal Sniper ne les lit
pas ; pour eux, utilisez les **notifications instantanées des applis** sur des recherches précises avec prix max.

Vérifiez toujours une annonce et le vendeur avant d'acheter : les estimations de l'IA sont indicatives.
