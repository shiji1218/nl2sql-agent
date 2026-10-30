"""API 配置接口：在线查看/修改大模型配置，热生效并持久化到 .env。"""
from openai import OpenAI
from pydantic import BaseModel

from app.config import settings
from app.llm.client import reset_client
from fastapi import APIRouter

router = APIRouter(prefix="/api/config")


def _masked(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 12:
        return "已配置"
    return f"{key[:6]}****{key[-4:]}"


def _current() -> dict:
    return {
        "llm_base_url": settings.llm_base_url,
        "llm_model": settings.llm_model,
        "api_key_masked": _masked(settings.llm_api_key),
        "has_key": bool(settings.llm_api_key),
    }


class ConfigIn(BaseModel):
    api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None


@router.get("")
def get_config() -> dict:
    """当前配置（Key 打码显示）。"""
    return _current()


@router.post("")
def update_config(cfg: ConfigIn) -> dict:
    """更新配置：内存热生效 + 持久化到 .env。"""
    updates: dict[str, str] = {}
    if cfg.api_key and cfg.api_key.strip():
        settings.llm_api_key = cfg.api_key.strip()
        updates["llm_api_key"] = settings.llm_api_key
    if cfg.llm_base_url and cfg.llm_base_url.strip():
        settings.llm_base_url = cfg.llm_base_url.strip().rstrip("/")
        updates["llm_base_url"] = settings.llm_base_url
    if cfg.llm_model and cfg.llm_model.strip():
        settings.llm_model = cfg.llm_model.strip()
        updates["llm_model"] = settings.llm_model
    if updates:
        settings.save_to_env(updates)
        reset_client()  # 下次调用按新配置重建客户端
    return {"ok": True, **_current()}


@router.post("/test")
def test_config(cfg: ConfigIn | None = None) -> dict:
    """测试连通性：用给定(或当前)配置发一次最小对话请求。"""
    api_key = (cfg.api_key if cfg and cfg.api_key else settings.llm_api_key) or ""
    base_url = (cfg.llm_base_url if cfg and cfg.llm_base_url else settings.llm_base_url).rstrip("/")
    model = cfg.llm_model if cfg and cfg.llm_model else settings.llm_model
    if not api_key:
        return {"ok": False, "error": "未填写 API Key"}
    try:
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=15)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
        )
        return {"ok": True, "message": f"连接成功，模型 {model} 可用（返回模型: {resp.model}）"}
    except Exception as e:  # noqa: BLE001 —— 把任何失败原因透传给前端
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
