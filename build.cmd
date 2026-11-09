@echo off
rem Builds dist\Fisoku.exe (one file, no Python needed on the target PC).
setlocal
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
  py -3.12 -m venv .venv || python -m venv .venv || goto :error
)
.venv\Scripts\python -m pip install -q --upgrade pip || goto :error
.venv\Scripts\python -m pip install -q -e .[dev] || goto :error
rem OCR engine for Studio's "Fisoku OCR" block. --no-deps: Studio already has its
rem libraries, and Fisoku itself never imports it (it is shipped as data).
.venv\Scripts\python -m pip install -q --no-deps rapidocr_onnxruntime==1.4.4 || goto :error
rem onnxruntime + shapely for Studio's Python 3.12; a fresh Studio install lacks them.
.venv\Scripts\python tools\fetch_engine_deps.py || goto :error
.venv\Scripts\python -m pytest -q || goto :error
.venv\Scripts\python packaging\make_icon.py || goto :error
for /f "delims=" %%p in ('.venv\Scripts\python -c "import fisoku.engine as e; print(e.source_dir())"') do set ENGINE=%%p
if not exist "%ENGINE%\__init__.py" goto :error

.venv\Scripts\pyinstaller --noconfirm --clean --onefile --console ^
  --name Fisoku ^
  --icon packaging\fisoku.ico ^
  --paths . ^
  --add-data "fisoku\web;fisoku\web" ^
  --add-data "%ENGINE%;engine\rapidocr_onnxruntime" ^
  --add-data ".dev\deps;engine\deps" ^
  --exclude-module rapidocr_onnxruntime ^
  --collect-submodules fisoku ^
  --collect-submodules uvicorn ^
  --collect-submodules mcp.client ^
  --collect-submodules mcp.shared ^
  packaging\launcher.py || goto :error

echo.
echo Hazir: %cd%\dist\Fisoku.exe
exit /b 0

:error
echo Derleme basarisiz.
exit /b 1
