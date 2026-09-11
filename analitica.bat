@echo off
rem Ejecuta la analítica batch del inventario: guarda el reporte en reports\ y en la base de datos.
rem Acepta los mismos parámetros que "python -m inventario.analytics" (ver README).
cd /d "%~dp0"
".venv\Scripts\python.exe" -m inventario.analytics --persist %*
