@echo off
setlocal
title Editor de Videos - Inicializacao

rem Usa a pasta deste arquivo mesmo quando iniciado por um atalho.
cd /d "%~dp0"

where uv >nul 2>&1
if errorlevel 1 (
    echo ERRO: uv nao encontrado. Instale o uv e adicione-o ao PATH.
    goto :erro
)

where npm >nul 2>&1
if errorlevel 1 (
    echo ERRO: npm nao encontrado. Instale o Node.js e abra este arquivo novamente.
    goto :erro
)

if not exist "%~dp0API\pyproject.toml" (
    echo ERRO: pasta API nao encontrada ao lado deste arquivo.
    goto :erro
)

if not exist "%~dp0frontend\package.json" (
    echo ERRO: pasta frontend nao encontrada ao lado deste arquivo.
    goto :erro
)

if not exist "%~dp0frontend\node_modules" (
    echo Instalando as dependencias do frontend...
    pushd "%~dp0frontend"
    call npm ci
    if errorlevel 1 (
        popd
        echo ERRO: nao foi possivel instalar as dependencias do frontend.
        goto :erro
    )
    popd
)

echo Iniciando backend e frontend...
start "Editor de Videos - Backend" /D "%~dp0API" cmd /k "uv run python -m src.api --reload"
start "Editor de Videos - Frontend" /D "%~dp0frontend" cmd /k "npm run dev -- --open"
echo O navegador sera aberto quando o frontend estiver pronto.
echo Para encerrar, pressione Ctrl+C nas duas janelas e depois feche-as.
exit /b 0

:erro
echo.
pause
exit /b 1
