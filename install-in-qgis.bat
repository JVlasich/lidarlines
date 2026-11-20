@echo off
setlocal EnableExtensions

REM ============================================================
REM  install-in-qgis.bat
REM  Adds a QGIS Processing script to the Toolbox, and optionally
REM  pip-installs the repo into QGIS's Python first (dependencies).
REM
REM  Usage:  install-flight-lines.bat "C:\path\to\reporoot"
REM          (no argument = current folder)
REM ============================================================

set "REPO=%~1"
if "%REPO%"=="" set "REPO=%CD%"
if "%REPO:~-1%"=="\" set "REPO=%REPO:~0,-1%"

REM ---- settings ----
set "SCRIPT=plan_flight_lines.py"
set "PROFILE=default"
set "INSTALL_DEPS=1"        REM set to 0 to skip pip and only copy the script

set "SRC=%REPO%\%SCRIPT%"
set "DEST=%APPDATA%\QGIS\QGIS3\profiles\%PROFILE%\processing\scripts"

if not exist "%SRC%" (
    echo [ERROR] Not found: %SRC%
    exit /b 1
)

if not "%INSTALL_DEPS%"=="1" goto :copy

REM find QGIS and pip-install the repo (dependencies)
set "O4W="
for %%D in ("C:\OSGeo4W" "C:\OSGeo4W64") do if exist "%%~D\bin\o4w_env.bat" set "O4W=%%~D"
if not defined O4W for /d %%D in ("C:\Program Files\QGIS *") do if exist "%%~D\bin\o4w_env.bat" set "O4W=%%~D"
if not defined O4W (
    echo [!] QGIS not found - skipping pip, copying the script only.
    goto :copy
)
call "%O4W%\bin\o4w_env.bat"
echo [*] pip installing %REPO%
python -m pip install "%REPO%" || python -m pip install --user "%REPO%"

:copy
if not exist "%DEST%" mkdir "%DEST%"
copy /Y "%SRC%" "%DEST%\" >nul
echo [OK] Copied %SCRIPT% to the Processing scripts folder:
echo      %DEST%
echo.
echo Restart QGIS, or refresh the Processing Toolbox

pause
endlocal & exit /b 0
