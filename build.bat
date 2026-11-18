@echo off
rem Builds the Windows release: dist\TikVortex\ and dist\TikVortex-1.0.0-windows.zip
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
echo [1/4] Installing build tools...
%PY% -m pip install --upgrade pip >nul
%PY% -m pip install -r requirements-build.txt || goto :fail
echo [2/4] Rendering the icon...
%PY% tools\make_icon.py || goto :fail
echo [3/4] Building with PyInstaller...
%PY% -m PyInstaller TikVortex.spec --noconfirm --clean || goto :fail
echo [4/4] Adding docs and packing the zip...
copy /y README.md dist\TikVortex\ >nul
copy /y LICENSE dist\TikVortex\LICENSE.txt >nul
powershell -NoProfile -Command "Compress-Archive -Path 'dist\TikVortex' -DestinationPath 'dist\TikVortex-1.0.0-windows.zip' -Force" || goto :fail
echo.
echo Done: dist\TikVortex\TikVortex.exe  and  dist\TikVortex-1.0.0-windows.zip
pause
exit /b 0
:fail
echo.
echo Build failed - see the messages above.
pause
exit /b 1
