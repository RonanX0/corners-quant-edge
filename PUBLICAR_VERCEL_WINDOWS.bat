@echo off
setlocal
cd /d "%~dp0"
echo.
echo ============================================
echo   CORNERS QUANT EDGE - PUBLICAR NA VERCEL
echo ============================================
echo.
where npx >nul 2>nul
if %errorlevel% neq 0 (
  echo ERRO: O Node.js nao esta instalado ou npx nao esta no PATH.
  echo Instale o Node.js LTS em https://nodejs.org/
  pause
  exit /b 1
)
echo A Vercel pode solicitar login e confirmacao do projeto.
echo.
call npx --yes vercel@latest --prod
if %errorlevel% neq 0 (
  echo.
  echo O deploy nao foi concluido. Confira a mensagem de erro acima.
) else (
  echo.
  echo Publicacao solicitada. Confira a URL mostrada acima.
)
echo.
pause
