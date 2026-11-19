"""Démon de veille 24 h/24 (Raspberry Pi / Linux) : `python -m dealsniper daemon`.

Une boucle asyncio par connecteur, chacune à son rythme, avec détection en deux
temps (liste légère, puis détail uniquement pour les nouveautés) et une base
SQLite locale. Les filtres, l'analyse Claude et les notifications sont ceux du
reste de Deal Sniper.
"""
