"""大模型客户端：OpenAI 兼容协议，默认对接阿里百炼。"""
import time

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from app.config import settings

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.llm_api_key:
            raise RuntimeError(
                "未配置 BAILIAN_API_KEY，请复制 .env.example 为 .env 并填入 Key"
            )
        _client = OpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
        )
    return _client


def reset_client() -> None:
    """配置变更后重置客户端，下次调用按新配置重建（热生效）。"""
    global _client
    _client = None


def chat(system: str, user: str, temperature: float = 0.0, max_retries: int = 3) -> str:
    """非流式调用，返回完整回复文本。temperature=0 保证 SQL 生成的稳定性。

    网络类异常（连接失败/超时/限流）自动指数退避重试；重试耗尽后抛出最后异常。
    """
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = get_client().chat.completions.create(
                model=settings.llm_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
            )
            return resp.choices[0].message.content or ""
        except (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError) as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(2 ** attempt)  # 2s → 4s 指数退避
    raise last_err  # type: ignore[misc]


def strip_code_fence(text: str) -> str:
    """剥掉 LLM 有时违规输出的 ```sql ... ``` 包裹。"""
    t = text.strip()
    if t.startswith("```"):
        # 去掉首行 ```sql / ```
        first_newline = t.find("\n")
        if first_newline != -1:
            t = t[first_newline + 1 :]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()
