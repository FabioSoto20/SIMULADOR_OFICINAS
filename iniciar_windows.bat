@echo off
cd /d "%~dp0"
py -m streamlit run app.py
if errorlevel 1 (
  echo.
  echo Si faltan librerias, ejecutar: py -m pip install -r requirements.txt
  pause
)
