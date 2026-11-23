# 🎯 Deal Sniper

Deal Sniper surveille les petites annonces d'**occasion en France** (surtout l'électronique, mais aussi le sport,
l'outillage…) et vous prévient quand une affaire vaut le coup :

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

> **Sur téléphone** : lancez l'interface avec `--host 0.0.0.0` ; la fenêtre affiche alors un lien
> `http://<ip-du-pc>:5000/?token=…` à ouvrir **une fois** sur le téléphone (même Wi-Fi). Le jeton est ensuite
> mémorisé ; sans lui, les autres appareils du réseau n'ont pas accès. Puis « Ajouter à l'écran d'accueil » :
> Deal Sniper s'installe comme une application plein écran.

> **Linux / Mac / Raspberry Pi** : mêmes étapes avec `cp .env.example .env`, puis
> `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python -m dealsniper ui --open`.

## 2. Les clés à obtenir

Elles se mettent dans le fichier **`.env`** (jamais envoyé sur GitHub). Après une modification, relancez
`start_ui.bat`. L'onglet **Réglages → État de l'installation** indique ce qui manque.

| Clé | Obligatoire ? | Où l'obtenir |
|---|---|---|
| `ANTHROPIC_API_KEY` | pour la notation IA (sinon **mode sans IA**) | [console.anthropic.com](https://console.anthropic.com/settings/keys) → *Create Key*. Ajoutez quelques dollars de crédit. |
| `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` | **oui** pour eBay | [developer.ebay.com](https://developer.ebay.com) → compte gratuit → *Application Keys* → jeu **Production** : *App ID* = CLIENT_ID, *Cert ID* = CLIENT_SECRET. Pour la question sur la suppression des comptes, choisissez l'exemption *« Not persisting eBay data »*. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | conseillé | Sur Telegram, écrivez à **@BotFather** → `/newbot` → il donne le token. Envoyez un message à votre bot, puis ouvrez `https://api.telegram.org/bot<TOKEN>/getUpdates` : le nombre après `"chat":{"id":` est le CHAT_ID. |
| `DISCORD_WEBHOOK_URL` | facultatif | Paramètres du salon → *Intégrations* → *Webhooks* → *Nouveau webhook* → *Copier l'URL*. |

Sans canal de notification, les bonnes affaires restent visibles dans l'interface.

Vous pouvez aussi **coller ces clés directement dans l'interface** : Réglages → Installation → « Clés
(fichier .env) » → *Enregistrer*. Elles sont écrites dans le `.env` local (ignoré par git, jamais envoyé
ailleurs) et prises en compte tout de suite. Ne collez jamais ces clés dans un message ou un fichier public.

**Mode sans IA** : Réglages → Budget IA → décochez « Utiliser l'IA ». Deal Sniper trie alors les annonces et
estime leur revente à partir de la **cote du marché** (prix réels des annonces vues), sans aucun appel à Claude,
sans clé et sans dépense. Vous perdez le diagnostic de panne et les notes de réparation, mais gardez le
repérage, la marge estimée et les alertes sur les grosses marges.

## 3. Utilisation

Double-cliquez sur **`start_ui.bat`**, puis cliquez sur **▶ Lancer un passage** (en haut à droite).
Gardez la fenêtre noire ouverte tant que vous utilisez l'interface : la fermer arrête Deal Sniper.

**Onglets**

| Onglet | Contenu |
|---|---|
| **Recherche** | recherche ponctuelle : tapez un objet précis, tous les sites le cherchent en une fois. **Critères éliminatoires** choisis avant de lancer (appliqués avant l'IA, donc gratuits) : prix min / max, état, livraison obligatoire, vendeur particulier ou pro, zone (ville + rayon en km), mots exclus, nombre maximum d'annonces analysées. Les annonces déjà écartées par d'autres critères sont réévaluées. Compte rendu final : trouvées, notées, **écartées avec leur raison**, durée, coût IA ; les résultats se filtrent ensuite comme dans l'onglet Annonces (les mots exclus s'appliquent en direct). |
| **Enregistrées** | recherches toutes prêtes : une liste de modèles (ex. « Mini-PC home lab » : M920q, M720q, EliteDesk…) lancés d'un coup sur tous les sites avec leurs critères (prix, état, remise, vendeur, zone, mots exclus). **▶ Lancer** pour une recherche immédiate, ou **En boucle toutes les N min** (30 min minimum) : tant que l'interface reste ouverte, la recherche est relancée toute seule, seules les **nouvelles annonces** sont analysées et les bonnes affaires partent sur **Telegram** (selon vos réglages de notifications). La carte affiche le dernier et le prochain scan. Une seule recherche à la fois ; rien ne part pendant un passage, une vérification ou une surveillance `watch` en cours (elle attend son tour). « ☆ Enregistrer » dans l'onglet Recherche en crée une. |
| **Analyse** | collez le lien d'**une** annonce (Vinted, leboncoin, eBay, Facebook, boutique…) : **Claude Opus 5.5** (le modèle le plus avancé, réflexion poussée) lit l'annonce, examine jusqu'à 4 photos, cherche les prix de vente réels sur le web et fait le **bilan** : verdict (acheter / négocier / passer), note sur 10, modèle exact, revente réelle et achat max, points forts / faibles, **risque d'arnaque**, ce qui est visible sur les photos, **points à vérifier en main propre**, questions au vendeur, message de négociation prêt à copier, graphique des ventes. ≈ 0,15 à 0,40 $ par annonce (compté dans le budget IA du jour). Une annonce déjà vue lors d'un passage est reprise de la base ; sinon la page est lue une fois, normalement. Si le site refuse la lecture automatique (leboncoin souvent), recopiez titre, prix et description dans « Saisir les infos à la main ». Les 30 dernières analyses restent affichées. |
| **Annonces** | toutes les annonces trouvées, 20 par page : photo, prix d'achat / revente / marge (« cote » quand la revente vient des prix réels), les 3 notes, distance. **☆** met en favori, **⊘** masque l'annonce, **i** ouvre le détail : achat / revente / marge / notes, panne, cote du marché, ville et distance, mode de remise, vendeur, **points de vigilance**, description, « Je l'ai acheté », **💬 Négocier** (message poli prêt à copier, avec le prix conseillé). **Analyse approfondie** (à la demande, quelques centimes : un modèle plus puissant + recherche web) : **verdict final** (✅ à acheter, 🤝 à négocier avec le prix à proposer, ❌ à laisser passer), **📷 examen de la photo** (état visible, défauts, photo douteuse ou de catalogue), revente réelle, prix d'achat maximum, **graphique des dernières ventes** et de la cote, liens de réparation, et sur un bouton séparé des **questions à poser au vendeur** propres au modèle. Badges : ⭐ marque recherchée, PRO (vendeur professionnel), ⬇ baisse de prix, ❌ plus en ligne. Vues Notées / Écartées / Toutes / Favoris / Masquées ; filtres par texte, site, catégorie, notes, prix, marge, **zone** (ville ou code postal + rayon), remise, vendeur (particulier / pro), période ; tri par date, note, marge, prix, facilité de vente ou distance ; **🔄 Encore en ligne ?** vérifie les annonces affichées et liste celles qui ont disparu (gratuit ; leboncoin et Facebook ne sont pas vérifiables) ; **export CSV** pour Excel. Pendant un passage, une ligne par site montre l'avancement, la durée et le coût IA, et **■ Annuler** l'arrête proprement (rien n'est perdu : les annonces non traitées reviennent au passage suivant). |
| **Achats** | vos achats (ajoutés depuis une annonce ou à la main) : prix payé, réparation, prix de revente réel, **temps passé**. Bénéfice, rentabilité, **rentabilité horaire (€/h)**, stock. Dès 3 ventes, Deal Sniper compare vos ventes réelles à ses estimations et ajuste sa prudence tout seul ; chaque vente réelle enrichit aussi la cote du modèle. **Suivi fiscal** : ventes et chiffre d'affaires de l'année, proximité des seuils de déclaration (indicatif). |
| **Recherches** | ce que chaque site cherche. La carte **🌐 Tous les sites** contient les recherches communes, lancées partout ; chaque site peut en ajouter. |
| **Catégories** | familles d'objets (PC portables, mini-PC, Dyson…) : mots-clés du titre, budget maximum, exclusions, signaux positifs. Une annonce hors catégorie est écartée gratuitement. |
| **Filtres** | mots interdits, signaux « bonne affaire », **marques recherchées** (qualité reconnue, cote stable : annonce priorisée et mieux notée), déjà vendue / réservée, annonces d'acheteurs. |
| **Réglages** | menu à gauche : Notifications (seuil + **message immédiat** pour les pépites, le reste en récap groupé), **Veille** (modèles précis repérés en priorité), Budget IA (**mode sans IA** possible), Localisation et prix, Fonctionnement (dont **💾 sauvegarde / restauration** en ZIP de la base et de `config.yaml`, jamais `.env`), Installation (clés `.env`), Apparence. |

Tout est enregistré dans votre `config.yaml` (bouton **Enregistrer** ou **Ctrl+S**) et appliqué au passage suivant.

Un seul passage tourne à la fois, même entre programmes : si une surveillance continue (`watch`) tourne déjà,
« Lancer un passage » l'indique au lieu de doubler les requêtes et les notifications.

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
Sans clé eBay dans les secrets, les passages planifiés s'arrêtent donc tout de suite (rien à faire là-bas).

**Home lab / Raspberry Pi / serveur Linux** (24 h/24) : clonez le dépôt puis lancez le script d'installation,
qui installe les paquets (Python, Xvfb), l'environnement, les navigateurs, puis deux services systemd :
la veille continue (`dealsniper`) et l'interface web sur le réseau local (`dealsniper-ui`).

```bash
git clone https://github.com/Marflus/DealSniper.git && cd DealSniper
bash deploy/install_homelab.sh             # à la fin : le lien (avec jeton) à ouvrir depuis le PC ou le téléphone
journalctl -u dealsniper -f                # suivre la veille ; journalctl -u dealsniper-ui -f pour l'interface
```

Les fichiers `deploy/*.service` contiennent `__USER__` et `__DIR__`, remplacés par le script : ne les copiez
pas tels quels.

Supprimez alors le bloc `schedule:` du workflow GitHub pour ne pas analyser deux fois les mêmes annonces.

## 6. Réglages

Tout se règle depuis l'interface ; `config.yaml` reste lisible et commenté si vous préférez l'éditer à la main.

| Section | Rôle |
|---|---|
| `searches` | recherches communes à tous les sites (`condition` : `parts` = pour pièces, `used` = occasion, `any` ; seul eBay l'utilise) |
| `sites` | un bloc par site : activé, rythme en surveillance continue, recherches propres, `fetch_details` |
| `categories` | mots-clés, budget maximum, exclusions et signaux par famille d'objets |
| `filters`, `location` | mots interdits, marques recherchées (`desirable_brands`), vendu / réservé, acheteurs, prix minimum, France uniquement, `home` (votre code postal) |
| `analyzer` | `enabled` (IA ou mode sans IA), modèle de notation, modèle de l'analyse approfondie (`deep_model`), plafond quotidien, max d'annonces analysées par passage (`max_ads_per_run`), prudence (`resale_haircut`, `auto_calibrate`), seuil de la cote (`market_min_margin`), prix d'une recherche web (`web_search_usd`, 0,01 $) |
| `watchlist` | veille : modèles précis repérés en priorité (notifiés d'office) |
| `notifier` | seuil de notification, seuil du message immédiat (`instant_min_score`), récap « à vérifier », rappel de fin d'enchère (`auction_reminder_minutes`) |
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
5. **fiche détaillée** des annonces retenues, les plus prometteuses d'abord et dans la limite du nombre maximum
   d'annonces analysées (les autres ne sont pas lues et reviennent au passage suivant), puis nouveau filtre sur la
   description ;
6. **notation par Claude** par lots. Garde-fous en Python : la revente retenue est la plus basse entre la cote et
   l'estimation de l'IA, rabotée de 10 % (ou selon vos ventes réelles), et la marge est recalculée
   (revente − prix − pièce − 10 % de frais). Une annonce que l'IA n'arrive pas à noter (refus, réponse illisible)
   est réessayée, puis abandonnée au 3e échec pour ne pas la repayer à chaque passage ;
7. **notification** des meilleures annonces.

Les annonces déjà vues dont le prix baisse sont signalées (« ⬇ Baisses de prix ») avec leur marge et leur note
recalculées au nouveau prix, sans nouvel appel à l'IA.

**Coût IA** : environ 0,0001 $ par annonce notée avec Claude Haiku 5.5 (modèle par défaut, réflexion désactivée).
La cote du marché évite en plus d'envoyer à l'IA les annonces sans marge possible. Un plafond quotidien (1 $ par
défaut) bloque toute surprise : une fois atteint, les annonces attendent le lendemain. L'analyse approfondie
(à la demande) coûte ses tokens plus 0,01 $ par recherche web (5 au maximum) ; le bouton affiche la moyenne de
vos analyses précédentes.

**Distances** : avec votre code postal (onglet Filtres), chaque annonce affiche sa distance. Les coordonnées des
communes viennent de l'API officielle geo.api.gouv.fr, téléchargées une fois dans `data/communes.json`.

**Respect des sites** : requêtes espacées et identifiées (`DealSniper/3.0`), API officielle pour eBay, pause
automatique de 6 h si un site refuse l'accès.

**Sites lus par navigateur** (`leboncoin.py`, `vinted.py`, `facebook.py`) : ces connecteurs pilotent un
navigateur et sont maintenus à part. Ils peuvent être bloqués à tout moment par les sites et ne fonctionnent pas
sur GitHub Actions. Le navigateur de Playwright (Vinted, Facebook) est installé automatiquement par
`start_ui.bat` au premier lancement (sinon : `.venv\Scripts\python -m playwright install chromium`).

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
│   ├── availability.py         « Encore en ligne ? » : annonces vendues ou supprimées
│   ├── runlock.py              un seul passage à la fois (verrou partagé entre programmes)
│   ├── demo.py                 fausses données pour l'aperçu (ui --demo)
│   ├── notifier.py             Telegram / Discord
│   ├── db.py                   SQLite : annonces vues, coûts, pauses
│   ├── http.py                 client HTTP poli (délais par site, robots.txt, 429, 403)
│   ├── config.py, models.py    configuration, modèle d'annonce
│   ├── sites/                  un fichier par site (base.py = interface, _template.py = modèle)
│   ├── utils/                  prix, texte, localisation, URL
│   └── web/                    interface : app.py (API Flask), security.py, preview.py (photos), static/
├── tests/                      pytest
├── deploy/                     install_homelab.sh + services systemd (veille, interface)
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

**Sécurité de l'interface** (`web/security.py`) : les requêtes qui modifient quelque chose doivent venir de
l'interface elle-même (une page d'un autre site ne peut pas réécrire vos clés ni lancer d'analyse), les noms
d'hôte autres que `localhost` et les adresses IP sont refusés (rebinding DNS ; exceptions dans
`DEALSNIPER_ALLOWED_HOSTS`), et les autres appareils du réseau doivent présenter le jeton de `data/ui_token`.
Ne l'exposez pas pour autant sur Internet : `--host 0.0.0.0` uniquement sur votre Wi-Fi.
