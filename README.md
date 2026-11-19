# 🎯 Deal Sniper

Chasseur automatique de **bonnes affaires électroniques en France** :

- 🔧 **à réparer** : PC portables sans SSD, consoles qui surchauffent, TV au rétroéclairage HS, outillage vendu sans batterie… ;
- 💎 **en revente directe** : appareils **fonctionnels** vendus nettement sous leur cote (vente urgente, déménagement…).

Deal Sniper interroge l'**API officielle eBay** (recherches en direct, sans limite pratique), lit vos **alertes
email** (leboncoin, Vinted, Agorastore…) et des **flux RSS**, élimine
gratuitement les annonces sans intérêt ou hors de France, fait noter les autres par **Claude** et vous envoie
sur **Telegram / Discord** uniquement les affaires notées **≥ 7/10**, avec la marge estimée, un niveau de
confiance et la question à poser au vendeur.

```
 Boîte mail (IMAP) ─┐
                    ├─► sources ─► filters ─► analyzer ─► notifier ─► Telegram / Discord
 Flux RSS ──────────┘   (extraction)  (0 token,   (Claude,    (score ≥ 7)
                                       France)     par lots)
                              data/seen_ads.json · data/deals.json · data/runs.json ─► interface web
```

**Principes**
- **Pas de navigateur ni de scraping** : uniquement vos emails d'alerte et des flux publics. Pas de risque de blocage de compte.
- **Coût minimal** : le pré-filtre Python élimine la majorité des annonces sans appel API ; les autres partent par lots
  de 10 avec descriptions tronquées à 600 caractères ; plafond de dépense journalier.
- **Robuste** : un email mal formé, une source en panne ou une erreur API temporaire n'arrêtent rien ; les annonces non
  analysées sont retentées au passage suivant ; une alerte Telegram prévient si une source tombe en panne.
- **Décision humaine** : Deal Sniper prévient, vous contactez le vendeur. Rien n'est acheté ni envoyé automatiquement.

---

## Sommaire
1. [Structure du projet](#1-structure-du-projet)
2. [Installation](#2-installation)
3. [Boîte mail dédiée](#3-boîte-mail-dédiée)
4. [Alertes sur les sites](#4-alertes-sur-les-sites)
5. [Notifications](#5-notifications)
6. [Configuration](#6-configuration)
7. [Commandes](#7-commandes)
8. [Automatisation GitHub Actions](#8-automatisation-github-actions)
9. [Coûts](#9-coûts)
10. [Dépannage](#10-dépannage)

---

## 1. Structure du projet

```
DealSniper/
├── config.yaml               # catégories, budgets, mots-clés, réglages (à adapter)
├── .env.example              # modèle des secrets (à copier en .env)
├── main.py                   # raccourci : python main.py = python -m dealsniper
├── dealsniper/
│   ├── cli.py                # commandes (run, inspect-emails, test-notif, dashboard…)
│   ├── pipeline.py           # un passage : sources → filtres → IA → notifications
│   ├── config.py             # lecture de config.yaml / .env, valeurs par défaut
│   ├── models.py             # Ad : une annonce normalisée
│   ├── sources/
│   │   ├── ebay_api.py       # API officielle eBay (recherches en direct, France)
│   │   ├── email_alerts.py   # lecture IMAP de la boîte dédiée + diagnostic
│   │   ├── email_parser.py   # email d'alerte → annonces (emails transférés compris)
│   │   └── rss.py            # flux RSS / Atom
│   ├── filters.py            # pré-filtre gratuit (catégorie, France, budget, exclusions)
│   ├── location.py           # ville / code postal, détection étranger et DOM-TOM
│   ├── prices.py             # prix, livraison, enchères, devises
│   ├── urls.py               # liens de suivi, URL canonique, identifiant d'annonce
│   ├── text.py               # nettoyage HTML, mots-clés sans accents
│   ├── analyzer.py           # notation par Claude (lots, schéma JSON, coût)
│   ├── notifier.py           # Telegram / Discord
│   ├── storage.py            # fichiers d'état JSON (écriture atomique)
│   └── web/                  # interface web locale (Flask)
├── data/                     # état : annonces vues, historique, coûts
├── tests/                    # tests unitaires (pytest)
└── .github/workflows/        # passage planifié (dealsniper.yml) et tests (tests.yml)
```

---

## 2. Installation

Prérequis : **Python 3.10+** et une **clé API Anthropic** ([console.anthropic.com](https://console.anthropic.com/settings/keys)).

```bash
git clone https://github.com/Marflus/DealSniper.git
cd DealSniper
python -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env               # Windows : copy .env.example .env
```

Remplissez `.env` (jamais commité : il est exclu par `.gitignore`).

> Pour un usage 100 % GitHub Actions, l'installation locale est facultative : voir § 8.

---

## 3. API eBay (recommandé)

C'est la source la plus riche : jusqu'à 200 annonces récentes par recherche, filtrées par eBay (objets **situés en
France**, prix max, état « pour pièces » ou « occasion »), 5 000 appels gratuits par jour. Aucun email nécessaire.

1. Créez un compte sur [developer.ebay.com](https://developer.ebay.com) (gratuit ; validation sous ~1 jour).
2. **Application Keys** → créez un jeu de clés **Production** : récupérez l'**App ID (Client ID)** et le
   **Cert ID (Client Secret)**.
3. eBay demande de déclarer un point de notification « suppression de compte » : comme Deal Sniper ne stocke aucune
   donnée d'utilisateur eBay, choisissez l'**exemption** (*Alerts & Notifications → Marketplace account deletion →
   Not persisting eBay data*).
4. Ajoutez les secrets `EBAY_CLIENT_ID` et `EBAY_CLIENT_SECRET` (GitHub et/ou `.env`).
5. Adaptez les recherches dans `config.yaml` → `sources.ebay_api.searches` (`query`, `condition`, `max_price`).

Sans ces clés, la source est simplement ignorée.

## 3 bis. Boîte mail dédiée

Utilisez une **adresse Gmail créée pour les alertes** :
1. Activez la **validation en deux étapes** du compte Google.
2. Créez un **mot de passe d'application** (Compte Google → Sécurité → *Mots de passe des applications*).
3. Renseignez `IMAP_SERVER=imap.gmail.com`, `IMAP_EMAIL` et `IMAP_PASSWORD` (le mot de passe d'application).

⚠️ Outlook / Hotmail n'acceptent plus l'IMAP par mot de passe : gardez-les comme boîte perso et **transférez**
les alertes vers la boîte dédiée. Les emails **transférés ou redirigés** sont reconnus automatiquement
(l'expéditeur d'origine est retrouvé dans le contenu).

---

## 4. Alertes sur les sites

- **leboncoin** : recherche → *Sauvegarder la recherche* → dans *Favoris → Recherches*, activez l'**enveloppe ✉️**
  (alerte email). La cloche 🔔 ne concerne que le téléphone. Si votre compte est sur votre boîte perso, créez une
  règle qui redirige les emails de leboncoin vers la boîte dédiée.
- **eBay** : inutile avec l'API (§ 3) ; sinon, recherche avec filtre « Articles en France » → *Enregistrer cette
  recherche* → notifications par email.
- **Vinted** : compte avec la boîte dédiée → recherche (mots-clés + prix max) → *Sauvegarder la recherche* →
  *Paramètres → Notifications* : activez les **emails** pour les recherches sauvegardées (si l'option n'apparaît pas,
  Vinted ne notifie que dans l'application et rien n'arrivera dans la boîte). Les liens `vinted.fr/items/…` sont reconnus.
- **Agorastore** et autres : même principe ; ajoutez l'expéditeur dans `sources.email.senders` si besoin.

> **Combien d'annonces ?** Deal Sniper n'a **aucune limite de nombre** : il lit tous les emails des 3 derniers jours,
> analyse toutes les annonces retenues et envoie toutes les bonnes affaires. La seule limite est celle des sites :
> un récapitulatif leboncoin ne montre qu'environ **5 annonces**, même s'il en annonce 745. Pour tout voir, créez
> plusieurs recherches **précises avec prix max** qui produisent chacune moins de 5 nouvelles annonces par jour.

Exemples de recherches efficaces (avec prix max) : `thinkpad sans ssd`, `latitude sans disque`, `ps4 surchauffe`,
`tv retroeclairage`, `makita sans batterie`, et pour la revente directe un modèle précis à prix bas (`ps5` ≤ 250 €).

---

## 5. Notifications

**Telegram** : @BotFather → `/newbot` → token ; envoyez un message à votre bot puis ouvrez
`https://api.telegram.org/bot<TOKEN>/getUpdates` pour lire votre `chat.id`.
**Discord** : salon → *Intégrations* → *Webhooks* → copier l'URL.

```
🎯 8/10 · 🔧 Réparation · PC portables pros
Lenovo ThinkPad T480 i5 16 Go sans SSD

💶 Prix : 85 € + 6,50 € port = 91,50 €
📍 Lyon 69003 · leboncoin
🔧 Panne probable : SSD manquant
🧩 Pièce ≈ 25 € · 📈 Revente ≈ 220 €
💰 Marge nette ≈ 82 € (confiance haute)
💬 Modèle très demandé, simple ajout d'un SSD M.2.
❓ À demander : Le chargeur d'origine est-il fourni ?
🔗 Voir l'annonce
```

Si une source tombe en panne (mot de passe IMAP révoqué…), un message ⚠️ vous prévient, au plus une fois par jour.

---

## 6. Configuration

Tout est commenté dans `config.yaml`. L'essentiel :

| Section | Rôle |
|---|---|
| `sources.email` | boîte IMAP, expéditeurs suivis, motifs d'URL d'annonce, suivi des liens de redirection |
| `location` | `france_only` (rejette « Provenance : Chine », prix en £/$…) et `exclude_overseas` (DOM-TOM) |
| `filters` | mots-clés rédhibitoires, annonces d'acheteurs, signaux « bonne affaire », prix minimum |
| `categories` | mots-clés de reconnaissance, `max_budget` (prix + port), exclusions et signaux par catégorie |
| `analyzer` | modèle, taille des lots, troncature, plafond de dépense journalier (1 $ par défaut, `null` = aucun) |
| `notifier` | seuil de score, types d'affaires notifiés |

- Mots-clés **insensibles à la casse et aux accents**, cherchés en **mots entiers**, accords tolérés
  (`oxyde` attrape « oxydée »).
- `require_include: true` sur une catégorie n'envoie à Claude que les annonces avec un signal positif.
- Pour diviser le coût par ~10 : `model: "claude-haiku-5-5"` et `effort: low`.

---

## 7. Commandes

```bash
python -m dealsniper run                 # un passage complet (= python main.py)
python -m dealsniper -v run --dry-run    # collecte + filtre avec raisons de rejet, sans IA ni sauvegarde
python -m dealsniper run --no-ai         # montre ce qui partirait à l'IA
python -m dealsniper run --loop 30       # tourne en continu (un passage toutes les 30 min)
python -m dealsniper inspect-emails      # diagnostic : annonces extraites de chaque email de la boîte
python -m dealsniper test-email a.eml    # diagnostic sur un email enregistré
python -m dealsniper test-notif          # fausse annonce sur Telegram / Discord
python -m dealsniper reset-seen          # oublie l'historique des annonces vues
python -m dealsniper dashboard           # interface web sur http://127.0.0.1:5000
python -m pytest                         # tests (pip install pytest)
```

---

## 8. Automatisation GitHub Actions

Le workflow `.github/workflows/dealsniper.yml` lance un passage **toutes les heures de 6 h à 23 h** et recommite
`data/*.json` pour mémoriser l'état.

1. **Settings → Secrets and variables → Actions** : `ANTHROPIC_API_KEY`, `IMAP_SERVER`, `IMAP_EMAIL`,
   `IMAP_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, et si besoin `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`,
   `DISCORD_WEBHOOK_URL`.
2. **Settings → Actions → General → Workflow permissions** : *Read and write permissions*.
3. **Actions → Deal Sniper → Run workflow**, puis choisissez le **mode** :
   - `run` : passage complet ;
   - `dry-run` : collecte + filtre détaillé, sans IA ni sauvegarde ;
   - `inspect-emails` : ce qui est extrait de chaque email (idéal après avoir configuré une alerte) ;
   - `test-notif` : fausse annonce envoyée sur Telegram / Discord.

GitHub peut retarder ou sauter des passages planifiés aux heures chargées : c'est normal, le suivant rattrape
(les emails des 3 derniers jours sont relus). ~18 passages/jour ≈ 550 min/mois, sous le quota gratuit.

---

## 9. Coûts

Un lot de 10 annonces ≈ 2 500 tokens en entrée et 1 000 en sortie :

| Modèle | Prix (entrée / sortie, par million de tokens) | 50 annonces analysées / jour |
|---|---|---|
| `claude-haiku-4-5` (défaut) | 1 $ / 5 $ | ≈ 0,04 $ / jour |
| `claude-haiku-5-5` | 0,10 $ / 0,50 $ | ≈ 0,004 $ / jour |

Le coût réel de chaque passage est enregistré dans `data/runs.json` et affiché dans l'interface ;
`max_daily_cost_usd` (1 $ par défaut, soit ~1 000 annonces analysées par jour) est une simple sécurité contre une
facture imprévue ; mettez `null` pour le supprimer.

---

## 10. Dépannage

| Problème | Solution |
|---|---|
| `connexion IMAP impossible` | mot de passe d'application, adresse, IMAP activé. |
| `IMAP : N email(s)` mais 0 annonce | lancez le mode `inspect-emails` : il montre la source reconnue et les liens de chaque email. |
| Annonces « hors catégorie » | ajoutez le modèle dans les `keywords` d'une catégorie. |
| Trop d'appels IA | baissez les `max_budget`, ajoutez des exclusions, ou `require_include: true`. |
| `clé API refusée` | vérifiez `ANTHROPIC_API_KEY` et le crédit sur la console Anthropic. |
| Rien sur Telegram | mode `test-notif` ; envoyez d'abord un message à votre bot. |
| L'état n'est pas recommité | *Read and write permissions* (§ 8, étape 2). |

### Sources non prises en charge

**Facebook Marketplace, et la lecture directe de leboncoin ou Vinted** : aucune API publique. Les projets qui s'y
connectent (fbm-sniper, zorojuro12/deal-sniper…) contournent les protections anti-robots (navigateur « stealth »,
jetons internes, rotation de cookies et d'IP). C'est contraire aux conditions de ces sites, cela casse à chaque
changement de leur côté (le `STATUS.md` de fbm-sniper-community indique toutes ses sources hors service en mai 2026)
et expose votre compte et votre IP à un blocage : Deal Sniper ne le fait pas.

Vérifiez toujours une annonce et le vendeur avant d'acheter : les estimations de l'IA sont indicatives.
