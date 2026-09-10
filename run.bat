@echo off
rem AutoColor local service -> http://127.0.0.1:8761/
cd /d %~dp0
.venv\Scripts\python -m uvicorn backend.app:app --host 127.0.0.1 --port 8761
