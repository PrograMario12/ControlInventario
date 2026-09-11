@echo off
rem Abre Control de Inventario sin dejar una consola abierta.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" main.py
