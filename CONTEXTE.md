# Contexte pour reprendre le projet (à lire en premier)

Ce fichier résume ce qui n'est ni dans le code ni dans l'historique Git : les préférences de Marius,
les décisions en attente et les limites à respecter. Pour le détail technique : `README.md`, puis
`git log` (chaque commit explique quoi et pourquoi).

## Marius et ses habitudes
- Développeur sous **Windows** ; lance tout par des `.bat` (`start_ui.bat`, `update.bat`, `demo.bat`).
  Projet : `C:\Users\mariu\Documents\Projets\DealSniper`. Interface : http://127.0.0.1:5000
- Écrit en français, souvent vite : répondre en français, simplement, sans jargon.
- Utilise aussi l'interface sur **téléphone** (jeton d'accès : lien affiché au démarrage avec `--host 0.0.0.0`).
- Notifications sur **Telegram** (second message avec le lien complet, pour transférer une annonce).
- Pas encore de **clé eBay** (inscription développeur en attente de validation).

## Limites à respecter (non négociables)
- **Ne pas aider à contourner les protections anti-robots** : leboncoin (DataDome, Patchright),
  Vinted, Facebook. Les connecteurs `sites/leboncoin.py`, `vinted.py`, `facebook.py` sont ceux de Marius :
  ne pas y ajouter de furtivité, de rotation, de contournement de captcha, etc. Leboncoin le bloque
  régulièrement (« accès refusé, en pause 6 h ») : c'est attendu, la réponse est d'espacer ou de désactiver.
- Ne jamais afficher, copier ou committer de clé (`.env`, `config.yaml` sont ignorés par Git).
- Volume : ne pas augmenter le nombre de pages chargées par site (risque de blocage).

## Fonctionnement à connaître
- Passages : `engine.py` (liste → filtre gratuit → cote → fiche → IA → notifications), un seul à la fois
  (verrou `data/run.lock`, aussi entre programmes).
- IA : notation rapide par lots (Claude Haiku 5.5) ; **analyse approfondie** à la demande (Claude Sonnet 5.5 +
  recherche web + **photo de l'annonce**), verdict acheter / négocier / passer, questions au vendeur sur bouton.
- Recherche ponctuelle : critères éliminatoires avant l'IA ; les résultats se filtrent avec la barre de
  l'onglet Annonces (un message dit combien de résultats sont masqués et par quels filtres).
- Mode démo (`demo.bat`) : fausses données, aucun appel extérieur (passage, IA, notifications refusés).
- Tests : `python -m pytest -q` ; test navigateur : `tests/test_ui_smoke.py`.

## Constats récents (tests de Marius, 21/11/2026)
- Recherche « chaussures blanches taille 43 » : 78 trouvées, 77 notées, affichage OK.
- **Notation rapide trop optimiste hors électronique** : baskets à 15 € estimées 60 € en revente par la
  notation rapide, 25 € par l'analyse approfondie (plus juste). La notation rapide n'a que le titre
  (Vinted ne donne pas la description dans la liste) et des repères surtout électronique.
- **Verdict inadapté aux petits objets** : l'achat max vise toujours +30 € net (`deep.py`, `TARGET_PROFIT`),
  impossible pour un objet revendu 25 € (achat max négatif → « Ne pas acheter »).
- Leboncoin : accès refusé à chaque passage.

## Décisions en attente de réponse de Marius (ne pas coder avant sa réponse)
1. Marge visée du verdict : +30 € fixe, ou proportionnelle (ex. 30 % de la revente, au moins 10 €) ?
2. Descriptions Vinted : ouvrir la page des annonces RETENUES pour lire la description (une requête par
   annonce qui passe le filtre ; `VintedSite.fetch_detail` ne fait rien aujourd'hui) ?
3. Repères mode / chaussures dans la notation rapide (et éventuelle catégorie « Mode et chaussures ») ?
4. Volume sans risque : Vinted `limit` 25 → 36 et leboncoin 25 → 35 (même nombre de pages chargées) ?
5. Notifications : interrupteur général dans Réglages, et/ou « pas de notification pendant une recherche
   ponctuelle » (aujourd'hui elles partent comme pour un passage normal) ?
6. Leboncoin : l'espacer fortement (un passage toutes les 1 à 2 h) ou le désactiver ?

## Ce que Marius a demandé à la session locale
Ouvrir http://127.0.0.1:5000, tout tester avec ses vraies données, lancer des recherches réelles et comparer
avec ce que montrent Vinted, leboncoin et Facebook pour les mêmes recherches (que rate-t-on ?), puis corriger.
Demander les décisions ci-dessus avant de coder ce qui en dépend.
