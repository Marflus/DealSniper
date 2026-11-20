@echo off
rem Deal Sniper : récupère la dernière version (git pull) puis relance l'interface (double-clic).
cd /d "%~dp0"
title Mise a jour

echo Arret de l'interface en cours (si elle tourne)...
taskkill /fi "WINDOWTITLE eq DealSniper-UI*" /t /f >nul 2>&1

echo Recuperation de la derniere version...
git pull
if errorlevel 1 (
    echo.
    echo La mise a jour a echoue. Si git parle de "local changes", lancez :
    echo     git stash
    echo puis relancez update.bat ^(vos modifications sont gardees dans "git stash list"^).
    pause
    exit /b 1
)

call start_ui.bat %*
