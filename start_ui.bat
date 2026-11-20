@echo off
rem Deal Sniper : lance l'interface web (double-clic). Crée l'environnement Python au premier lancement.
cd /d "%~dp0"
title DealSniper-UI

if not exist ".venv\Scripts\python.exe" (
    echo Premier lancement : creation de l'environnement Python...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo Python est introuvable. Installez-le depuis https://www.python.org/downloads/ ^(cochez "Add to PATH"^).
        pause
        exit /b 1
    )
)

echo Verification des dependances...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo Echec de l'installation des dependances.
    pause
    exit /b 1
)

rem Navigateur de Playwright (Vinted, Facebook) : telecharge une seule fois.
if not exist ".venv\playwright-ok" (
    echo Installation du navigateur pour Vinted / Facebook ^(une seule fois^)...
    ".venv\Scripts\python.exe" -m playwright install chromium && echo ok> ".venv\playwright-ok"
)

".venv\Scripts\python.exe" -m dealsniper ui --open %*
pause
