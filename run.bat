@echo off
cd /d "%~dp0stoloto-simulator"
start "" http://localhost:8080
python -m http.server 8080
