"""全局配置：从 .env 读取，供所有模块复用。"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    def __init__(self) -> None:
        # 数据库
        self.database_url: str = os.getenv(
            "DATABASE_URL", "sqlite:///./nl2sql_demo.db"
        )
        # 大模型（OpenAI 兼容协议，默认阿里百炼）
        self.llm_api_key: str = os.getenv("BAILIAN_API_KEY", "")
        self.llm_base_url: str = os.getenv(
            "LLM_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self.llm_model: str = os.getenv("LLM_MODEL", "qwen-plus")
        # Agent 行为
        self.max_rows: int = int(os.getenv("MAX_ROWS", "50"))
        self.max_repair_attempts: int = int(os.getenv("MAX_REPAIR_ATTEMPTS", "3"))
        # 业务文档层
        self.schema_docs_path: Path = BASE_DIR / "config" / "schema_docs.json"

    @property
    def dialect(self) -> str:
        """当前数据库方言，用于生成对应语法的 SQL。"""
        if self.database_url.startswith("mysql"):
            return "MySQL"
        if self.database_url.startswith("postgres"):
            return "PostgreSQL"
        return "SQLite"

    ENV_KEYS = {
        "llm_api_key": "BAILIAN_API_KEY",
        "llm_base_url": "LLM_BASE_URL",
        "llm_model": "LLM_MODEL",
        "database_url": "DATABASE_URL",
    }

    def save_to_env(self, updates: dict[str, str]) -> None:
        """把运行时配置变更持久化到 .env（保留其它行）。"""
        env_path = BASE_DIR / ".env"
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        for attr, env_key in updates.items():
            replaced = False
            for i, line in enumerate(lines):
                if line.startswith(f"{env_key}="):
                    lines[i] = f"{env_key}={updates[attr]}"
                    replaced = True
                    break
            if not replaced:
                lines.append(f"{env_key}={updates[attr]}")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


settings = Settings()
