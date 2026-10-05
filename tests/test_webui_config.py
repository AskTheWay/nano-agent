"""WebUI 配置热重载的单测：.env 写回逻辑（不依赖 fastapi 运行时）。"""

from nano_agent.webui.server import _mask, _update_env_file


def test_mask():
    assert _mask("abcdefghijklmnop") == "abcdef…mnop"
    assert _mask("short") == "…"
    assert _mask("1234567890123") == "123456…0123"


def test_update_env_replaces_and_preserves(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "# 注释要保留\nOPENAI_BASE_URL=https://old/v1\n"
        "OPENAI_API_KEY=old-key\nMODEL_NAME=old-model\n\n# TOKEN_LIMIT=30000\n",
        encoding="utf-8")
    _update_env_file("https://new/v1", "new-key", "new-model")
    content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "OPENAI_BASE_URL=https://new/v1" in content
    assert "OPENAI_API_KEY=new-key" in content
    assert "MODEL_NAME=new-model" in content
    assert "old" not in content.replace("# 注释要保留", "")
    assert "# 注释要保留" in content          # 注释行保留
    assert "# TOKEN_LIMIT=30000" in content    # 其他变量行保留


def test_update_env_creates_missing_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _update_env_file("https://x/v1", "k", "m")  # .env 不存在 -> 新建
    content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "OPENAI_BASE_URL=https://x/v1" in content
    assert "MODEL_NAME=m" in content
