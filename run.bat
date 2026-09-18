@echo off
rem AutoColor local service (worktree branch) -> http://127.0.0.1:8762/  (8761 = main checkout)
cd /d %~dp0
.venv\Scripts\python -m uvicorn backend.app:app --host 127.0.0.1 --port 8762
