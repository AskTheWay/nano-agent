"""搜索与 shell 工具单测：截断 / 剪枝 / 非法输入 / 编码。"""

import os

from nano_agent.tools.search import glob, grep, _skipped
from nano_agent.tools.shell import bash, _decode


# ========== glob ==========

def test_glob_truncates_at_200(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for i in range(250):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    out = glob(pattern="*.txt")
    assert "共 250 条" in out and "只显示前 200" in out


def test_glob_skips_dirs_by_segment(tmp_path):
    """黑名单按路径段匹配。注意：Python glob 本来就不匹配点开头的隐藏文件
    （.git/.gitignore 都不会出现在结果里），所以这里用 node_modules 验证段级剪枝。"""
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.txt").write_text("x", encoding="utf-8")
    (tmp_path / "keep_me.txt").write_text("x", encoding="utf-8")
    out = glob(pattern="*", path=str(tmp_path))
    assert "keep_me.txt" in out
    assert "node_modules" not in out
    # 递归模式下黑名单目录的内容也不出现
    out2 = glob(pattern="**/*.txt", path=str(tmp_path))
    assert "pkg.txt" not in out2 and "keep_me.txt" in out2


def test_skipped_segment_logic():
    assert _skipped("a/.git/b.txt")
    assert _skipped("a\\__pycache__\\b.pyc")
    assert not _skipped("a/.gitignore")
    assert not _skipped("a/node_modules_keep/b.txt")  # 前缀相似但段不同


def test_glob_missing_dir_errors():
    assert "[错误] 目录不存在" in glob(pattern="*", path="no/such/dir")


# ========== grep ==========

def test_grep_invalid_regex_errors():
    assert "[错误] 正则表达式无效" in grep(pattern="([unclosed")


def test_grep_finds_with_line_numbers(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\ndef foo():\n    pass\n", encoding="utf-8")
    out = grep(pattern=r"def \w+", path=str(tmp_path))
    assert "a.py:2:" in out and "def foo" in out


def test_grep_no_match(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert "无匹配" in grep(pattern="zzz", path=str(tmp_path))


def test_grep_truncates_at_50(tmp_path):
    (tmp_path / "big.txt").write_text("\n".join(f"hit{i}" for i in range(80)),
                                      encoding="utf-8")
    out = grep(pattern="hit", path=str(tmp_path))
    assert "已截断" in out


# ========== bash ==========

def test_bash_echo_roundtrip():
    """基本执行 + 退出码 + 输出。"""
    out = bash(command="echo nano_agent_test_42")
    assert "[exit 0]" in out and "nano_agent_test_42" in out


def test_bash_chinese_output_not_mojibake():
    """中文输出不乱码（双码页回退：cmd 的 GBK 或工具的 UTF-8 都能解）。"""
    out = bash(command="echo 你好世界测试")
    assert "你好世界测试" in out
    assert "�" not in out  # 没有替换符


def test_bash_nonzero_exit_reported():
    out = bash(command="exit 3")
    assert "[exit 3]" in out


def test_bash_timeout_kills_tree():
    """超时被杀且【真的返回】（Windows 上 kill cmd 不杀孙进程时会挂死）。"""
    import time
    t0 = time.time()
    out = bash(command='python -c "import time; time.sleep(30)"')
    elapsed = time.time() - t0
    assert "超时" in out
    assert elapsed < 20  # BASH_TIMEOUT 默认 10s + 收尸余量；挂死实现会 30s+


# ========== _decode 双码页回退 ==========

def test_decode_utf8_then_gbk():
    assert _decode("hello".encode("utf-8")) == "hello"
    assert _decode("你好".encode("utf-8")) == "你好"      # UTF-8 优先
    assert _decode("你好".encode("gbk")) == "你好"        # 回退到本机码页
