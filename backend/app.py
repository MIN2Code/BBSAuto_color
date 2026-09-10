"""AutoColor 本地服务入口 → http://127.0.0.1:8761/"""
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router

app = FastAPI(title="AutoColor")
app.include_router(router)

_FRONTEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")


@app.get("/")
def index():
    return FileResponse(os.path.join(_FRONTEND, "index.html"))


@app.get("/calibration")
def calibration_page():
    return FileResponse(os.path.join(_FRONTEND, "index.html"))


app.mount("/static", StaticFiles(directory=_FRONTEND), name="static")


@app.middleware("http")
async def no_cache_static(request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path == "/":
        resp.headers["Cache-Control"] = "no-cache"
    return resp
