# 🎯 Deal Sniper

Chasseur automatique de **bonnes affaires électroniques** :

- 🔧 **à réparer** : PC portables pros sans SSD, consoles qui surchauffent, TV au rétroéclairage HS,
  outillage vendu sans batterie… ;
- 💎 **en revente directe** : appareils **fonctionnels** vendus nettement sous leur cote
  (vente urgente, déménagement, succession…), revendables tels quels.

Deal Sniper lit vos **alertes email** (leboncoin, Agorastore, eBay, Vinted…) et des **flux RSS**,
élimine gratuitement les annonces sans intérêt, fait noter les autres par **Claude** (modèle économique)
et vous envoie sur **Telegram / Discord** uniquement les affaires notées **≥ 7/10**.

```
 Boîte mail (IMAP) ─┐
                    ├─► collector.py ─► filter.py ─► analyzer.py ─► notifier.py ─► Telegram / Discord
 Flux RSS ──────────┘   (dédoublonnage)  (0 token)    (Claude Haiku)   (score ≥ 7)
                                 │                          │
                                 └──── data/seen_ads.json ──┴── data/deals.json ──► dashboard.py (interface web)
```

- ✅ **Sans navigateur** (pas de Selenium / Playwright) → pas de blocage anti-bot, rien à scraper.
- ✅ **Coût API minimal** : pré-filtre en Python pur, envoi par lots, descriptions tronquées à 600 caractères,
  réponse JSON contrainte par schéma, **plafond de dépense journalier**.
- ✅ **Robuste** : un email mal formé, un flux en panne ou une erreur API temporaire n'arrête rien ;
  les annonces non analysées sont retentées au passage suivant.
- ✅ **Automatisable** sur GitHub Actions (dépôt privé), un serveur, un Raspberry Pi ou votre PC.

---

## Sommaire

1. [Structure du projet](#1-structure-du-projet)
2. [Installation](#2-installation)
3. [Préparer la boîte mail dédiée](#3-préparer-la-boîte-mail-dédiée)
4. [Créer les alertes sur les sites](#4-créer-les-alertes-sur-les-sites)
5. [Notifications Telegram / Discord](#5-notifications-telegram--discord)
6. [Régler `config.yaml`](#6-régler-configyaml)
7. [Utilisation](#7-utilisation)
8. [Interface web](#8-interface-web)
9. [Automatisation](#9-automatisation)
10. [Coûts](#10-coûts)
11. [Dépannage](#11-dépannage)

---

## 1. Structure du projet

| Fichier | Rôle |
|---|---|
| `collector.py` | Lit la boîte mail (IMAP) et les flux RSS ; extrait titre, prix, livraison, description nettoyée, source, URL. |
| `filter.py` | Pré-filtre gratuit : catégorie, budget max (prix + port), mots-clés rédhibitoires, annonces d'acheteurs. |
| `analyzer.py` | Envoie les annonces par lots à Claude et récupère un JSON structuré (type d'affaire, score, panne, coûts, marge…). |
| `notifier.py` | Envoie les annonces ≥ seuil sur Telegram et/ou Discord (console si aucun canal). |
| `main.py` | Orchestre le tout + options de test (`--dry-run`, `--test-notif`, `--loop`…). |
| `dashboard.py` | Petite interface web locale : tableau des affaires, coûts, bouton « Lancer ». |
| `core.py` / `storage.py` | Utilitaires communs (prix, texte, URL) et stockage JSON atomique. |
| `config.yaml` | Catégories, budgets, mots-clés, réglages IA et notifications. |
| `.env.example` | Modèle des variables secrètes (à copier en `.env`). |
| `data/` | `seen_ads.json` (annonces déjà traitées), `deals.json` (historique analysé), `runs.json` (coûts). |
| `.github/workflows/` | Exécution planifiée (`dealsniper.yml`) et tests automatiques (`tests.yml`). |
| `tests/` | Tests unitaires (`pytest`). |

---

## 2. Installation

Prérequis : **Python 3.10+** et une **clé API Anthropic** ([console.anthropic.com](https://console.anthropic.com/settings/keys)).

```bash
git clone https://github.com/Marflus/DealSniper.git
cd DealSniper

python -m venv .venv
# Linux / macOS :
source .venv/bin/activate
# Windows (PowerShell) :
.venv\Scripts\Activate.ps1

pip install -r requirements.txt

cp .env.example .env        # Windows : copy .env.example .env
```

Ouvrez ensuite `.env` et renseignez au minimum `ANTHROPIC_API_KEY` et les variables `IMAP_*`.

> 🔒 `.env` est exclu par `.gitignore` : vos secrets ne partent jamais sur GitHub.

---

## 3. Préparer la boîte mail dédiée

Utilisez une **adresse créée uniquement pour les alertes** (ne donnez jamais l'accès à votre boîte principale).

**Gmail** (recommandé) :
1. Créez un compte Gmail, par ex. `mes.alertes.deals@gmail.com`.
2. Activez la **validation en deux étapes** (Compte Google → Sécurité).
3. Créez un **mot de passe d'application** : Compte Google → Sécurité → *Mots de passe des applications*.
4. Dans `.env` :
   ```
   IMAP_SERVER=imap.gmail.com
   IMAP_EMAIL=mes.alertes.deals@gmail.com
   IMAP_PASSWORD=abcd efgh ijkl mnop   # le mot de passe d'application
   ```

Autres fournisseurs : Outlook/Hotmail `outlook.office365.com`, GMX `imap.gmx.com`, OVH `ssl0.ovh.net`
(certains imposent aussi un mot de passe d'application ou l'activation d'IMAP dans les réglages).

> Astuce : vous pouvez aussi garder vos alertes sur votre compte habituel et créer une **règle de transfert
> automatique** vers la boîte dédiée.

---

## 4. Créer les alertes sur les sites

- **leboncoin** : faites une recherche (ex. « thinkpad » dans *Ordinateurs*, prix max 180 €),
  cliquez sur **« Sauvegarder la recherche »** et activez les **alertes par email** vers la boîte dédiée.
- **Agorastore** : créez des alertes par mots-clés depuis votre espace (enchères publiques de collectivités).
- **eBay** : enregistrez une recherche et activez les **notifications par email**.
  Si vous disposez d'un flux RSS (eBay, [RSS-Bridge](https://github.com/RSS-Bridge/rss-bridge), rss.app…),
  ajoutez-le dans `config.yaml > sources > rss > feeds`.
- **Vinted** et autres : tout site qui envoie des alertes email fonctionne ; ajoutez l'expéditeur dans
  `sources.imap.senders` et le format d'URL dans `ad_url_patterns`.

💡 Faites des recherches **ciblées pannes simples** : « sans ssd », « sans disque », « batterie hs »,
« sans batterie », « surchauffe », « pas d'image »… et, pour la revente directe, des recherches
**par modèle avec un prix max bas** (ex. « ps5 » à 250 € max, « thinkpad t14 » à 200 € max).
Le pré-filtre fera le reste.

Vérifiez l'extraction sur un vrai email : enregistrez-le en `.eml` (Gmail : ⋮ → *Télécharger le message*) puis :

```bash
python main.py --test-email alerte.eml
```

---

## 5. Notifications Telegram / Discord

Les deux sont optionnels et cumulables. Sans aucun canal, les affaires s'affichent dans la console.

**Telegram**
1. Parlez à [@BotFather](https://t.me/BotFather) → `/newbot` → récupérez le **token**.
2. Envoyez un message à votre bot, puis récupérez votre **chat id** via [@userinfobot](https://t.me/userinfobot).
3. `.env` : `TELEGRAM_BOT_TOKEN=...` et `TELEGRAM_CHAT_ID=...`

**Discord**
1. Paramètres du salon → *Intégrations* → *Webhooks* → *Nouveau webhook* → *Copier l'URL*.
2. `.env` : `DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...`

Test : `python main.py --test-notif`

Exemples de notification :

```
🎯 8/10 · 🔧 Réparation · PC portables pros · leboncoin
Lenovo ThinkPad T480 i5 16 Go sans SSD

💶 Prix : 85 € + 6,50 € port = 91,50 €
🔧 Panne probable : SSD manquant
🧩 Pièce ≈ 25 € · 📈 Revente ≈ 220 €
💰 Marge nette ≈ 82 €
💬 Modèle très demandé, simple ajout d'un SSD M.2.
🔗 Voir l'annonce
```

```
🔥 9/10 · 💎 Revente directe · Consoles · leboncoin
PS5 Slim lecteur + 2 manettes — déménagement, vente urgente

💶 Prix : 250 €
📈 Revente ≈ 390 € (en l'état)
💰 Marge nette ≈ 101 €
💬 Fonctionnelle, facture fournie, 35 % sous la cote.
🔗 Voir l'annonce
```

---

## 6. Régler `config.yaml`

Tout est commenté dans le fichier. L'essentiel :

```yaml
categories:
  - name: "PC portables pros"
    keywords: ["thinkpad", "latitude", "elitebook"]   # reconnaissance (dans le titre en priorité)
    max_budget: 180                                    # prix + livraison max
    exclude_keywords: ["coque seule"]                  # exclusions propres à la catégorie
    include_keywords: ["sans ssd", "batterie hs"]      # signaux positifs → priorisés et transmis à Claude
    require_include: false                             # true = n'analyser QUE les annonces avec un signal

filters:
  global_exclude_keywords: ["carte mere hs", "oxyde", "bloque icloud", "mot de passe bios"]
  deal_signals: ["urgent", "demenagement", "succession", "avec facture"]  # signaux « bonne affaire »

analyzer:
  model: "claude-haiku-4-5"
  max_daily_cost_usd: 0.10

notifier:
  min_score: 7
  deal_types: ["reparation", "revente_directe"]   # retirez un type pour ne plus le recevoir
```

### Deux types d'affaires

Claude classe chaque annonce en **`reparation`** (objet en panne ou incomplet) ou **`revente_directe`**
(objet fonctionnel au moins ~30 % sous sa cote d'occasion), avec le même barème de marge :
une note ≥ 8 exige une **marge nette ≥ 40 €** et un risque faible. Un prix anormalement bas sur un produit
récent et recherché est traité comme une **arnaque probable** (note ≤ 3).

- Les `deal_signals` (vente urgente, déménagement, facture, comme neuf…) font remonter les annonces
  fonctionnelles en priorité, comme les `include_keywords` le font pour les pannes simples.
- Les `max_budget` s'appliquent aux deux types : relevez-les si vous visez du matériel fonctionnel plus cher.

- Les mots-clés sont **insensibles à la casse et aux accents**, cherchés en **mots entiers**,
  et tolèrent les accords (`oxyde` attrape « oxydée », « oxydés »).
- `require_include: true` sur une catégorie réduit fortement le nombre d'appels IA.
- Pour diviser encore le coût par ~10 : `model: "claude-haiku-5-5"` + `effort: low`.

---

## 7. Utilisation

```bash
python main.py --dry-run      # 1er test : collecte + filtre, affiche retenues/rejetées (aucun appel IA, rien sauvegardé)
python main.py -v --no-ai     # montre ce qui partirait à l'IA, avec les raisons de rejet détaillées
python main.py                # passage complet
python main.py --loop 30      # tourne en continu, un passage toutes les 30 min
python main.py --test-notif   # teste Telegram / Discord
python main.py --reset-seen   # oublie l'historique (toutes les annonces seront retraitées)
python -m pytest -q           # tests unitaires (pip install pytest)
```

---

## 8. Interface web

```bash
python dashboard.py
# → http://127.0.0.1:5000
```

- tableau des annonces analysées, triable (score, prix, marge…) et filtrable (catégorie, type d'affaire, score, texte) ;
- coût API du jour et du mois, dernier passage ;
- boutons **Lancer un passage**, **Collecte sans IA**, **Tester les notifications**.

> L'interface n'a pas d'authentification : elle n'écoute que sur `127.0.0.1`. Ne l'exposez pas sur Internet.

---

## 9. Automatisation

### Option A — GitHub Actions (dépôt privé, rien à héberger)

Le workflow `.github/workflows/dealsniper.yml` lance `main.py` **toutes les heures de 6 h à 23 h**
et recommite `data/*.json` pour mémoriser les annonces déjà vues.

1. Poussez le projet sur votre dépôt **privé** (l'historique `data/deals.json` y sera stocké).
2. Dépôt → **Settings → Secrets and variables → Actions → New repository secret**, créez :
   `ANTHROPIC_API_KEY`, `IMAP_SERVER`, `IMAP_EMAIL`, `IMAP_PASSWORD`
   (+ `IMAP_PORT`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_URL` si besoin).
3. **Settings → Actions → General → Workflow permissions** : cochez *Read and write permissions*.
4. Onglet **Actions** → *Deal Sniper* → **Run workflow** pour un premier essai.

Quota : ~18 passages/jour ≈ 550 minutes/mois, sous les **2 000 min gratuites** des dépôts privés.
Modifiez la ligne `cron` pour changer la fréquence (GitHub peut retarder les crons de quelques minutes).

### Option B — PC / serveur / Raspberry Pi

```bash
python main.py --loop 20                   # le plus simple
# ou via cron (Linux) :
*/20 7-23 * * * cd /chemin/DealSniper && .venv/bin/python main.py >> data/cron.log 2>&1
```

Un verrou (`data/.run.lock`) empêche deux passages simultanés (cron + interface web).

---

## 10. Coûts

Le pré-filtre élimine généralement 70 à 90 % des annonces **sans aucun appel API**.
Pour celles qui restent, un lot de 10 annonces ≈ 2 500 tokens en entrée + 800 en sortie :

| Modèle | Prix (entrée / sortie, par million de tokens) | 50 annonces analysées / jour |
|---|---|---|
| `claude-haiku-4-5` (défaut) | 1 $ / 5 $ | ≈ 0,03 $ / jour |
| `claude-haiku-5-5` | 0,10 $ / 0,50 $ | ≈ 0,003 $ / jour |

Le coût réel de chaque passage est calculé à partir de la réponse de l'API, enregistré dans `data/runs.json`
et affiché dans l'interface. `max_daily_cost_usd` (0,10 $ par défaut) stoppe l'analyse si le plafond est atteint ;
les annonces restantes sont reportées au lendemain.

---

## 11. Dépannage

| Problème | Solution |
|---|---|
| `Connexion IMAP impossible` | Vérifiez serveur/port, IMAP activé, **mot de passe d'application** (Gmail/Outlook). |
| 0 annonce extraite d'un email | `python main.py --test-email alerte.eml` puis ajustez `ad_url_patterns` / `senders`. |
| Trop d'annonces « hors catégorie » | Ajoutez des modèles dans `keywords` de la catégorie. |
| Trop d'appels IA | Baissez les `max_budget`, ajoutez des exclusions, ou `require_include: true`. |
| `clé API refusée` | Vérifiez `ANTHROPIC_API_KEY` (et le crédit sur la console Anthropic). |
| Rien reçu sur Telegram | Envoyez d'abord un message à votre bot ; testez avec `--test-notif`. |
| Le workflow GitHub ne pousse pas `data/` | Activez *Read and write permissions* (§ 9, étape 3). |

---

### Bon usage

Deal Sniper ne fait que lire **vos propres emails d'alerte** et des **flux publics** : pas de scraping,
pas de contournement de protections. Respectez les conditions d'utilisation des plateformes, et vérifiez
toujours une annonce (et le vendeur) avant d'acheter : les estimations de l'IA sont indicatives.
