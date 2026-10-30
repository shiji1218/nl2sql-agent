"""应用入口：uvicorn app.main:app --reload 后访问 http://127.0.0.1:8000"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.chat import router as api_router
from app.api.config import router as config_router

app = FastAPI(title="NL2SQL Agent - 智能问数", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """兜底：未捕获异常返回 JSON，让前端能显示真实错误（而非纯文本 500）。"""
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": f"服务器内部错误: {type(exc).__name__}: {exc}"},
    )


app.include_router(api_router)
app.include_router(config_router)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
