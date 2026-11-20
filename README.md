# 🎯 Deal Sniper

Deal Sniper surveille les petites annonces d'**électronique d'occasion en France** et vous prévient quand une
affaire vaut le coup :

- **à réparer** : PC vendu sans SSD, aspirateur Dyson à la batterie HS, TV sans image (rétroéclairage)… ;
- **à revendre en l'état** : objet fonctionnel vendu nettement sous sa cote.

Chaque annonce intéressante est notée par l'IA **Claude** (rentabilité, facilité de réparation, facilité de
vente) puis envoyée sur **Telegram** ou **Discord**. Une interface web permet de tout parcourir et régler.

```
🎯 Dell Latitude 7490 sans SSD
💶 Achat : 60 €
📈 Revente : 180 € (marge +77 €) · vente 8/10
🔧 Panne : SSD absent (facilité 9/10)
🔗 Voir l'annonce
```

---

## Sommaire

1. [Installation sous Windows](#1-installation-sous-windows)
2. [Les clés à obtenir](#2-les-clés-à-obtenir)
3. [Utilisation](#3-utilisation)
4. [Mettre à jour](#4-mettre-à-jour)
5. [Faire tourner sans son PC](#5-faire-tourner-sans-son-pc)
6. [Réglages](#6-réglages)
7. [Fonctionnement et coûts](#7-fonctionnement-et-coûts)
8. [Pour les développeurs](#8-pour-les-développeurs)

---

## 1. Installation sous Windows

**Prérequis** (une seule fois) :

- **Python 3.11 ou plus récent** : [python.org/downloads](https://www.python.org/downloads/). Pendant
  l'installation, cochez **« Add python.exe to PATH »**.
- **Git** : [git-scm.com/download/win](https://git-scm.com/download/win) (options par défaut).

**Installation** : ouvrez un terminal (touche Windows, tapez `cmd`, Entrée) puis :

```bat
cd %USERPROFILE%\Documents
git clone https://github.com/Marflus/DealSniper.git
cd DealSniper
copy .env.example .env
notepad .env
```

Remplissez les clés dans le Bloc-notes (voir [section 2](#2-les-clés-à-obtenir)), enregistrez, puis
**double-cliquez sur `start_ui.bat`** dans le dossier `DealSniper`.

Au premier lancement, le script crée l'environnement Python (`.venv`), installe les dépendances (une à deux
minutes), crée votre `config.yaml` et ouvre l'interface dans le navigateur. Les fois suivantes, il démarre en
quelques secondes.

> Pour **juste voir l'interface** (fausses données, aucune clé, aucun passage), double-cliquez sur **`demo.bat`**.
> N'exécutez pas `python ...` directement : passez toujours par un `.bat`, qui utilise le `.venv` où les
> dépendances sont installées (sinon `ModuleNotFoundError: No module named 'yaml'`).

> **Linux / Mac / Raspberry Pi** : mêmes étapes avec `cp .env.example .env`, puis
> `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python -m dealsniper ui --open`.

## 2. Les clés à obtenir

Elles se mettent dans le fichier **`.env`** (jamais envoyé sur GitHub). Après une modification, relancez
`start_ui.bat`. L'onglet **Réglages → État de l'installation** indique ce qui manque.

| Clé | Obligatoire ? | Où l'obtenir |
|---|---|---|
| `ANTHROPIC_API_KEY` | **oui** (notation des annonces) | [console.anthropic.com](https://console.anthropic.com/settings/keys) → *Create Key*. Ajoutez quelques dollars de crédit. |
| `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` | **oui** pour eBay | [developer.ebay.com](https://developer.ebay.com) → compte gratuit → *Application Keys* → jeu **Production** : *App ID* = CLIENT_ID, *Cert ID* = CLIENT_SECRET. Pour la question sur la suppression des comptes, choisissez l'exemption *« Not persisting eBay data »*. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | conseillé | Sur Telegram, écrivez à **@BotFather** → `/newbot` → il donne le token. Envoyez un message à votre bot, puis ouvrez `https://api.telegram.org/bot<TOKEN>/getUpdates` : le nombre après `"chat":{"id":` est le CHAT_ID. |
| `DISCORD_WEBHOOK_URL` | facultatif | Paramètres du salon → *Intégrations* → *Webhooks* → *Nouveau webhook* → *Copier l'URL*. |

Sans canal de notification, les bonnes affaires restent visibles dans l'interface.
Ne collez jamais ces clés dans un message ou un fichier public.

## 3. Utilisation

Double-cliquez sur **`start_ui.bat`**, puis cliquez sur **▶ Lancer un passage** (en haut à droite).
Gardez la fenêtre noire ouverte tant que vous utilisez l'interface : la fermer arrête Deal Sniper.

**Onglets**

| Onglet | Contenu |
|---|---|
| **Annonces** | toutes les annonces trouvées, 20 par page : photo, prix d'achat / revente / marge (« cote » quand la revente vient des prix réels), les 3 notes, distance. **☆** met en favori, **⊘** masque l'annonce, **i** ouvre le détail : panne, ville et distance, cote du marché et estimation de l'IA, coût de la pièce, vendeur (note et évaluations quand le site les donne), description, questions à poser au vendeur, liens de réparation (iFixit, pièce sur eBay / AliExpress), « Je l'ai acheté », et **Analyse du marché** (un modèle plus puissant + recherche web va chercher les prix de vente réels pour une fourchette fiable, à la demande, quelques centimes). Vues Notées / Écartées / Toutes / Favoris / Masquées ; filtres par texte, site, catégorie, notes, prix, marge, distance, période ; tri par date, note, marge, prix, facilité de vente ou distance ; **export CSV** pour Excel. Pendant un passage, une ligne par site montre l'avancement, la durée et le coût IA ; à la fin, le détail de chaque recherche reste affiché. |
| **Achats** | vos achats (ajoutés depuis une annonce ou à la main) : prix payé, réparation, prix de revente réel. Bénéfice, rentabilité, stock. Dès 3 ventes, Deal Sniper compare vos ventes réelles à ses estimations et ajuste sa prudence tout seul ; chaque vente réelle enrichit aussi la cote du modèle. |
| **Recherches** | ce que chaque site cherche. La carte **🌐 Tous les sites** contient les recherches communes, lancées partout ; chaque site peut en ajouter. |
| **Catégories** | familles d'objets (PC portables, mini-PC, Dyson…) : mots-clés du titre, budget maximum, exclusions, signaux positifs. Une annonce hors catégorie est écartée gratuitement. |
| **Filtres** | mots interdits (oxydé, iCloud…), annonces d'acheteurs, déjà vendues ou réservées, France uniquement, prix minimum, **votre code postal** (pour les distances). |
| **Réglages** | seuils de notification, budget IA quotidien et modèle, état de l'installation, thème clair / sombre, envoi d'une notification de test. |

Tout est enregistré dans votre `config.yaml` (bouton **Enregistrer** ou **Ctrl+S**) et appliqué au passage suivant.

**Les 3 notes** (sur 10) :

- ⭐ **Note** : rentabilité globale. ≥ 7 = message immédiat ; 5-6 = récapitulatif « à vérifier ».
- 🔧 **Réparation** : 10 = rien à faire ou pièce à brancher ; 7-9 = pièce standard et tournevis ; 1-3 = soudure ou panne inconnue.
- 🔥 **Vente** : 10 = modèle très recherché qui part en quelques jours ; 1-3 = ancien ou peu demandé.

**En ligne de commande** (depuis le dossier, après un premier `start_ui.bat`) :

```bat
.venv\Scripts\python -m dealsniper ui --demo     &:: interface remplie de fausses données (aucun passage, base séparée)
.venv\Scripts\python -m dealsniper run           &:: un passage, lignes de progression puis détail des recherches
.venv\Scripts\python -m dealsniper watch         &:: surveillance continue, chaque site à son rythme
.venv\Scripts\python -m dealsniper test-notif    &:: fausse annonce sur Telegram / Discord
.venv\Scripts\python -m dealsniper recap         &:: récapitulatif des dernières 24 h (--hours N)
.venv\Scripts\python -m dealsniper reset         &:: oublie les annonces déjà vues
.venv\Scripts\python -m dealsniper --sites ebay run   &:: limite le passage à certains sites
```

Ajoutez `-v` pour des journaux détaillés.

## 4. Mettre à jour

Double-cliquez sur **`update.bat`** : il ferme l'interface, récupère la dernière version (`git pull`) et la relance.

Votre `config.yaml` n'est jamais touché par les mises à jour (il est ignoré par git). Les nouveautés de la
configuration d'exemple (`config.example.yaml`) ne s'y ajoutent donc pas d'elles-mêmes : pour repartir de la
dernière version : **Réglages → Fonctionnement → ↺ Réinitialiser** (votre configuration actuelle est d'abord
sauvegardée dans un fichier `config.<date>.bak.yaml`).

## 5. Faire tourner sans son PC

**GitHub Actions** (gratuit, rien à installer) : le workflow `.github/workflows/dealsniper.yml` fait un passage
**eBay** toutes les 30 minutes de 6 h à 23 h et garde la mémoire des annonces vues entre deux passages.

1. Sur GitHub : *Settings → Secrets and variables → Actions → New repository secret*, une entrée par clé du `.env`.
2. *Actions → Deal Sniper → Run workflow* pour un lancement manuel (`run`, `test-notif` ou `recap`).

GitHub retarde ou saute parfois des passages planifiés. Seul eBay y tourne : les autres sites demandent un navigateur.

**Raspberry Pi / serveur Linux** (24 h/24, plus réactif) : installez comme en section 1, puis

```bash
sudo cp deploy/dealsniper.service /etc/systemd/system/   # adaptez User et chemins dans le fichier
sudo systemctl daemon-reload && sudo systemctl enable --now dealsniper
journalctl -u dealsniper -f                               # suivre l'activité
```

Supprimez alors le bloc `schedule:` du workflow GitHub pour ne pas analyser deux fois les mêmes annonces.

## 6. Réglages

Tout se règle depuis l'interface ; `config.yaml` reste lisible et commenté si vous préférez l'éditer à la main.

| Section | Rôle |
|---|---|
| `searches` | recherches communes à tous les sites (`condition` : `parts` = pour pièces, `used` = occasion, `any` ; seul eBay l'utilise) |
| `sites` | un bloc par site : activé, rythme en surveillance continue, recherches propres, `fetch_details` |
| `categories` | mots-clés, budget maximum, exclusions et signaux par famille d'objets |
| `filters`, `location` | mots interdits, vendu / réservé, acheteurs, prix minimum, France uniquement, `home` (votre code postal) |
| `analyzer` | modèle de notation, modèle de l'analyse approfondie (`deep_model`), plafond quotidien, prudence (`resale_haircut`, `auto_calibrate`), seuil de la cote (`market_min_margin`) |
| `notifier` | note minimale pour un message, récapitulatif « à vérifier », marge / facilités minimales |
| `network` | délai entre deux requêtes vers un même site, pause après un refus |

## 7. Fonctionnement et coûts

À chaque passage, pour chaque site (en parallèle) :

1. **recherche** des annonces récentes ;
2. seules les **nouvelles** sont traitées (les annonces déjà vues sont mémorisées dans `data/dealsniper.db`) ;
3. **filtre gratuit** : doublon d'une annonce déjà vue sur un autre site, catégorie, budget, France, mots interdits,
   déjà vendue… ;
4. **cote du marché** (gratuit) : le modèle est reconnu dans le titre (« thinkpad t480 », « dyson v10 »…) et comparé
   au prix médian des annonces de ce modèle **en état de marche** vues ces 60 derniers jours. S'il n'y en a pas assez,
   un échantillon d'occasions fonctionnelles est demandé à l'API eBay (gardé 7 jours). Une annonce dont la marge est
   impossible même sans réparation est écartée **sans appel à l'IA** ;
5. **fiche détaillée** des annonces retenues, puis nouveau filtre sur la description ;
6. **notation par Claude** par lots. Garde-fous en Python : la revente retenue est la plus basse entre la cote et
   l'estimation de l'IA, rabotée de 10 % (ou selon vos ventes réelles), et la marge est recalculée
   (revente − prix − pièce − 10 % de frais) ;
7. **notification** des meilleures annonces.

**Coût IA** : environ 0,0001 $ par annonce notée avec Claude Haiku 5.5 (modèle par défaut, réflexion désactivée).
La cote du marché évite en plus d'envoyer à l'IA les annonces sans marge possible. Un plafond quotidien (1 $ par
défaut) bloque toute surprise : une fois atteint, les annonces attendent le lendemain.

**Distances** : avec votre code postal (onglet Filtres), chaque annonce affiche sa distance. Les coordonnées des
communes viennent de l'API officielle geo.api.gouv.fr, téléchargées une fois dans `data/communes.json`.

**Respect des sites** : requêtes espacées et identifiées (`DealSniper/3.0`), API officielle pour eBay, pause
automatique de 6 h si un site refuse l'accès.

**Sites lus par navigateur** (`leboncoin.py`, `vinted.py`, `facebook.py`) : ces connecteurs pilotent un
navigateur et sont maintenus à part. Ils peuvent être bloqués à tout moment par les sites et ne fonctionnent pas
sur GitHub Actions. Ils ont besoin du navigateur de Playwright :
`.venv\Scripts\python -m playwright install chromium`.

Les estimations de l'IA restent indicatives : vérifiez toujours l'annonce et le vendeur avant d'acheter.

## 8. Pour les développeurs

```
DealSniper/
├── start_ui.bat / update.bat / demo.bat   lancer / mettre à jour / aperçu démo (Windows)
├── config.example.yaml         configuration de référence (copiée en config.yaml, personnel)
├── .env.example                modèle des clés (copié en .env, personnel)
├── dealsniper/
│   ├── cli.py                  commandes : run, watch, ui, test-notif, recap, reset
│   ├── engine.py               un passage : recherche → filtre → fiche → IA → notification
│   ├── progress.py             lignes de progression (terminal + interface)
│   ├── filters.py              filtre gratuit
│   ├── analyzer.py             notation par Claude (lots, schéma JSON, coût, garde-fous)
│   ├── market.py               cote du marché : modèle reconnu, prix réels, doublons
│   ├── purchases.py            bénéfices et calibrage des estimations sur vos ventes réelles
│   ├── geo.py                  distances (communes de geo.api.gouv.fr)
│   ├── deep.py                 analyse de marché approfondie à la demande (gros modèle + web)
│   ├── demo.py                 fausses données pour l'aperçu (ui --demo)
│   ├── notifier.py             Telegram / Discord
│   ├── db.py                   SQLite : annonces vues, coûts, pauses
│   ├── http.py                 client HTTP poli (délais par site, robots.txt, 429, 403)
│   ├── config.py, models.py    configuration, modèle d'annonce
│   ├── sites/                  un fichier par site (base.py = interface, _template.py = modèle)
│   ├── utils/                  prix, texte, localisation, URL
│   └── web/                    interface : app.py (API Flask), preview.py (photos), static/
├── tests/                      pytest
├── deploy/dealsniper.service   service systemd
└── .github/workflows/          passage planifié + tests
```

**Ajouter un site** : copiez `dealsniper/sites/_template.py`, écrivez `list_recent` (titre, prix, URL, photo)
et éventuellement `fetch_detail`, appelez `self.report(requete, nb_annonces)` après chaque recherche, ajoutez-le
dans `SITE_MODULES` (`dealsniper/sites/__init__.py`) et un bloc à son nom sous `sites:` dans la configuration.
Préférez une API officielle ; pour une page HTML, `http.get(url, check_robots=True)`.

**Vérifications** :

```bash
pip install pytest ruff
ruff check dealsniper tests
python -m pytest -q
```

`tests/test_ui_smoke.py` ouvre l'interface dans un navigateur et vérifie qu'aucun onglet ne déclenche d'erreur
JavaScript (ignoré si Playwright et son navigateur ne sont pas installés).

L'interface n'a pas de mot de passe : ne l'exposez pas sur Internet (`--host 0.0.0.0` uniquement sur votre Wi-Fi).
