"""权限引擎单测：deny-first、前缀/glob 匹配、会话级 remember。"""

from nano_agent.permissions import (Decision, PermissionEngine, Rule,
                                    denied_observation)


def _engine(rules: dict) -> PermissionEngine:
    e = PermissionEngine()
    e.allow = [Rule(r) for r in rules.get("allow", [])]
    e.deny = [Rule(r) for r in rules.get("deny", [])]
    e.default = Decision(rules.get("default", "ask"))
    return e


# ========== 判定顺序 ==========

def test_deny_beats_allow():
    """同一调用同时命中 allow 和 deny：deny 赢（顺序即优先级）。"""
    e = _engine({"allow": ["read_file"], "deny": ["read_file(**/.env)"]})
    assert e.check("read_file", {"path": "x/.env"}) == Decision.DENY
    assert e.check("read_file", {"path": "x/a.py"}) == Decision.ALLOW


def test_default_ask():
    e = _engine({"allow": ["read_file"], "deny": []})
    assert e.check("write_file", {"path": "a.txt", "content": "x"}) == Decision.ASK


def test_empty_rules_everything_asks():
    e = _engine({})
    assert e.check("bash", {"command": "echo hi"}) == Decision.ASK


# ========== 规则匹配语义 ==========

def test_tool_name_wildcard():
    e = _engine({"allow": ["read_*"], "deny": []})
    assert e.check("read_file", {"path": "a"}) == Decision.ALLOW
    assert e.check("write_file", {"path": "a"}) == Decision.ASK  # 不匹配


def test_bash_exact_match():
    e = _engine({"allow": ["bash(git status)"], "deny": []})
    assert e.check("bash", {"command": "git status"}) == Decision.ALLOW
    assert e.check("bash", {"command": "git status -u"}) == Decision.ASK  # 整串才算


def test_bash_prefix_match():
    e = _engine({"allow": ["bash(git diff:*)"], "deny": []})
    assert e.check("bash", {"command": "git diff"}) == Decision.ALLOW
    assert e.check("bash", {"command": "git diff HEAD"}) == Decision.ALLOW
    assert e.check("bash", {"command": "git push"}) == Decision.ASK     # 前缀不匹配
    assert e.check("bash", {"command": "git diffx"}) == Decision.ASK    # 边界：prefix+空格


def test_path_glob_deny():
    e = _engine({"allow": [], "deny": ["read_file(**/.env)"]})
    assert e.check("read_file", {"path": "mini/.env"}) == Decision.DENY
    assert e.check("read_file", {"path": ".env"}) == Decision.DENY      # ** 匹配零层
    assert e.check("read_file", {"path": "src/main.py"}) == Decision.ASK


def test_windows_backslash_normalized():
    e = _engine({"allow": [], "deny": ["read_file(**/.env)"]})
    assert e.check("read_file", {"path": "mini\\.env"}) == Decision.DENY  # 反斜杠归一


# ========== 会话级 remember ==========

def test_remember_allow_session_scope():
    e = _engine({})
    assert e.check("write_file", {"path": "a.txt", "content": "x"}) == Decision.ASK
    e.remember_allow("write_file", {"path": "a.txt", "content": "x"})
    assert e.check("write_file", {"path": "b.txt", "content": "x"}) == Decision.ALLOW


def test_remember_allow_bash_by_first_token():
    e = _engine({})
    e.remember_allow("bash", {"command": "python -m pytest tests/"})
    assert e.check("bash", {"command": "python app.py"}) == Decision.ALLOW
    assert e.check("bash", {"command": "pip install x"}) == Decision.ASK


# ========== 拒绝观测文本 ==========

def test_denied_observation_is_actionable():
    s = denied_observation("read_file", {"path": ".env"})
    assert s.startswith("[权限拒绝]")
    assert "换一个" in s or "说明" in s  # 告诉模型下一步能做什么
