@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

set "LOG=%PROJECT_DIR%build_log.txt"

> "%LOG%" echo Build PyInstaller - Polizas a Excel 3420
>> "%LOG%" echo Fecha: %date% %time%
>> "%LOG%" echo Carpeta: %PROJECT_DIR%
>> "%LOG%" echo.

echo ========================================
echo Build PyInstaller - Polizas a Excel 3420
echo ========================================
echo.

if exist "venv\Scripts\python.exe" (
    echo [1/7] venv existente detectado.
    >> "%LOG%" echo [1/7] venv existente detectado.
) else (
    echo [1/7] No existe venv. Creando...
    >> "%LOG%" echo [1/7] No existe venv. Creando...

    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv venv >> "%LOG%" 2>&1
    ) else (
        python -m venv venv >> "%LOG%" 2>&1
    )

    if errorlevel 1 (
        echo ERROR: No se pudo crear venv. Verifica que Python este instalado.
        >> "%LOG%" echo ERROR: No se pudo crear venv.
        goto :end
    )
)

set "PY=venv\Scripts\python.exe"

if not exist "%PY%" (
    echo ERROR: No se encontro %PY%
    >> "%LOG%" echo ERROR: No se encontro %PY%
    goto :end
)

echo [2/7] Verificando Python del venv...
>> "%LOG%" echo [2/7] Verificando Python del venv...
"%PY%" --version >> "%LOG%" 2>&1

if errorlevel 1 (
    echo ERROR: Python del venv no responde.
    >> "%LOG%" echo ERROR: Python del venv no responde.
    goto :end
)

echo [3/7] Actualizando pip...
>> "%LOG%" echo [3/7] Actualizando pip...
"%PY%" -m pip install --upgrade pip >> "%LOG%" 2>&1

echo [4/7] Instalando dependencias...
>> "%LOG%" echo [4/7] Instalando dependencias...
"%PY%" -m pip install pyinstaller anthropic Pillow openpyxl msal msal-extensions requests >> "%LOG%" 2>&1

if errorlevel 1 (
    echo ERROR: Fallo al instalar dependencias.
    >> "%LOG%" echo ERROR: Fallo al instalar dependencias.
    goto :end
)

echo Limpiando builds previos...
>> "%LOG%" echo Limpiando builds previos...

if exist "build" rmdir /s /q "build" >> "%LOG%" 2>&1
if exist "dist" rmdir /s /q "dist" >> "%LOG%" 2>&1
if exist "Polizas a Excel 3420.spec" del /q "Polizas a Excel 3420.spec" >> "%LOG%" 2>&1
if exist "Updater.spec" del /q "Updater.spec" >> "%LOG%" 2>&1

set "ICON_ARG="
set "ICON_DATA_ARG="
if exist "icon.ico" (
    set "ICON_ARG=--icon=icon.ico"
    set "ICON_DATA_ARG=--add-data=icon.ico;."
)

echo [5/7] Verificando sintaxis...
>> "%LOG%" echo [5/7] Verificando sintaxis...
"%PY%" -m py_compile app.py audit.py update_manager.py updater.py >> "%LOG%" 2>&1

if errorlevel 1 (
    echo ERROR: Hay errores de sintaxis. Revisa build_log.txt.
    >> "%LOG%" echo ERROR: Hay errores de sintaxis.
    goto :end
)

echo [6/7] Compilando Polizas a Excel 3420.exe...
>> "%LOG%" echo [6/7] Compilando Polizas a Excel 3420.exe...

"%PY%" -m PyInstaller ^
  --onefile ^
  --windowed ^
  --noconfirm ^
  --clean ^
  --noupx ^
  %ICON_ARG% ^
  %ICON_DATA_ARG% ^
  --name "Polizas a Excel 3420" ^
  --hidden-import audit ^
  --hidden-import update_manager ^
  --hidden-import graph_client ^
  --hidden-import openpyxl ^
  --hidden-import msal ^
  --hidden-import msal_extensions ^
  app.py >> "%LOG%" 2>&1

if exist "dist\Polizas a Excel 3420.exe" (
    echo OK: dist\Polizas a Excel 3420.exe generado.
    >> "%LOG%" echo OK: dist\Polizas a Excel 3420.exe generado.

    echo Actualizando SHA256 en version.json...
    >> "%LOG%" echo Actualizando SHA256 en version.json...
    powershell -Command "& { $hash = (Get-FileHash 'dist\Polizas a Excel 3420.exe' -Algorithm SHA256).Hash; $j = Get-Content 'version.json' -Raw | ConvertFrom-Json; $j.sha256 = $hash; ($j | ConvertTo-Json -Depth 5) | Set-Content 'version.json' -Encoding UTF8; Write-Host 'SHA256:' $hash }" >> "%LOG%" 2>&1
    powershell -Command "& { $hash = (Get-FileHash 'dist\Polizas a Excel 3420.exe' -Algorithm SHA256).Hash; Write-Host 'SHA256:' $hash }"
) else (
    echo FALLO: dist\Polizas a Excel 3420.exe NO generado.
    >> "%LOG%" echo FALLO: dist\Polizas a Excel 3420.exe NO generado.
)

echo [7/7] Compilando Updater.exe...
>> "%LOG%" echo [7/7] Compilando Updater.exe...

"%PY%" -m PyInstaller ^
  --onefile ^
  --windowed ^
  --noconfirm ^
  --clean ^
  --noupx ^
  --name Updater ^
  --hidden-import tkinter ^
  --hidden-import tkinter.messagebox ^
  updater.py >> "%LOG%" 2>&1

if exist "dist\Updater.exe" (
    echo OK: dist\Updater.exe generado.
    >> "%LOG%" echo OK: dist\Updater.exe generado.
) else (
    echo FALLO: dist\Updater.exe NO generado.
    >> "%LOG%" echo FALLO: dist\Updater.exe NO generado.
)

echo.
echo ========================================
echo Resultado final
echo ========================================

if exist "dist\Polizas a Excel 3420.exe" (
    echo [OK] dist\Polizas a Excel 3420.exe
) else (
    echo [FALLO] dist\Polizas a Excel 3420.exe NO generado
)

if exist "dist\Updater.exe" (
    echo [OK] dist\Updater.exe
) else (
    echo [FALLO] dist\Updater.exe NO generado
)

echo.
echo Build terminado. Revisa build_log.txt para detalles.

if not exist "dist\Polizas a Excel 3420.exe" goto :end

echo.
echo ========================================
echo Git commit y push
echo ========================================
>> "%LOG%" echo.
>> "%LOG%" echo [Git] Commit y push...

for /f "usebackq tokens=*" %%v in (`powershell -Command "(Get-Content version.json -Raw | ConvertFrom-Json).version"`) do set "APP_VER=%%v"

git add app.py graph_client.py audit.py update_manager.py updater.py ^
        version.json build.bat contexto.md generar_key.py >> "%LOG%" 2>&1

git diff --cached --quiet
if not errorlevel 1 (
    echo No hay cambios nuevos para commitear.
    >> "%LOG%" echo [Git] Nada que commitear.
    goto :push
)

git commit -m "build: v%APP_VER%" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [AVISO] git commit falló. Revisa build_log.txt.
    >> "%LOG%" echo [Git] ERROR en commit.
    goto :end
)
echo [OK] Commit creado: build: v%APP_VER%

:push
git push >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [AVISO] git push falló ^(sin red o sin remote configurado^).
    >> "%LOG%" echo [Git] ERROR en push.
) else (
    echo [OK] Push exitoso a origin.
    >> "%LOG%" echo [Git] Push OK.
)

:end
echo.
pause
