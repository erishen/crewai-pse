"""配置管理 — 从环境变量和 .env 加载。"""

import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "deepseek-chat")
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    PSE_MAX_RETRIES: int = int(os.getenv("PSE_MAX_RETRIES", "3"))


settings = Settings()
