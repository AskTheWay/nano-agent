"""配置加载：.env -> AppConfig。

对应 M1 单文件里的"第 1 段：环境与配置"。
"""

import json
import os
import sys

from dotenv import load_dotenv


# ========== 配置对象 ==========

class AppConfig:
    """全部可调项集中在这里（M3/M4 会继续往里加依赖注入的组件）。"""

    def __init__(self) -> None:
        load_dotenv()  # 把 .env 的键值写进 os.environ（已存在的环境变量优先）
        self.base_url = os.environ.get("OPENAI_BASE_URL", "")
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        self.model = os.environ.get("MODEL_NAME", "")
        extra = os.environ.get("OPENAI_EXTRA_BODY", "").strip()
        # 额外请求体：mimo 的 {"thinking": {"type": "disabled"}} 这类私有参数
        self.extra_body = json.loads(extra) if extra else None
        self.max_turns = int(os.environ.get("MAX_TURNS", "25"))
        self.bash_timeout = int(os.environ.get("BASH_TIMEOUT", "10"))
        self.token_limit = int(os.environ.get("TOKEN_LIMIT", "30000"))  # M3 生效

    def validate(self) -> None:
        """缺配置时给出可操作的报错，而不是让 SDK 抛一坨 traceback。"""
        missing = [k for k, v in [
            ("OPENAI_BASE_URL", self.base_url),
            ("OPENAI_API_KEY", self.api_key),
            ("MODEL_NAME", self.model),
        ] if not v]
        if missing:
            print("[启动失败] 缺少模型配置。请复制 .env.example 为 .env 并填写：")
            print("  " + "\n  ".join(missing))
            sys.exit(1)


def load_config() -> AppConfig:
    cfg = AppConfig()
    cfg.validate()
    return cfg
