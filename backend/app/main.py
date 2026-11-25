from __future__ import annotations
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import PROJECT_NAME
from app.services.handoff import PayloadHTTPException
from app.routers import audit, bags, chutes, dashboard, diverts, lines, routes, scans, settings, shifts
from app.seed import seed_if_empty

seed_if_empty()

app = FastAPI(title=PROJECT_NAME)


@app.exception_handler(PayloadHTTPException)
async def payload_http_exception_handler(_: Request, exc: PayloadHTTPException):
    return JSONResponse(status_code=exc.status_code, content=exc.payload)


app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
for mod in (dashboard, lines, chutes, routes, scans, diverts, bags, shifts, settings, audit):
    app.include_router(mod.router, prefix="/api")


@app.get("/api/健康")
def health():
    return {"产品": PROJECT_NAME, "状态": "正常"}
