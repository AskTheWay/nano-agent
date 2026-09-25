"""edit_file 单测：str_replace 语义（唯一匹配才动手）。"""

from nano_agent.tools.fs import edit_file, write_file, read_file


def test_unique_replace(tmp_path):
    fp = tmp_path / "a.txt"
    fp.write_text("hello world\nhello nano\n", encoding="utf-8")
    r = edit_file(str(fp), "hello nano", "hello agent")
    assert "已替换 1 处" in r
    assert fp.read_text(encoding="utf-8") == "hello world\nhello agent\n"


def test_ambiguous_replace_errors(tmp_path):
    fp = tmp_path / "a.txt"
    fp.write_text("x\nx\n", encoding="utf-8")
    r = edit_file(str(fp), "x", "y")
    assert "[错误]" in r and "2 处" in r
    assert fp.read_text(encoding="utf-8") == "x\nx\n"  # 未被修改


def test_no_match_errors_with_hint(tmp_path):
    fp = tmp_path / "a.txt"
    fp.write_text("aaa\n", encoding="utf-8")
    r = edit_file(str(fp), "zzz", "y")
    assert "[错误]" in r and "未找到" in r


def test_write_then_read_roundtrip(tmp_path):
    fp = tmp_path / "sub" / "b.txt"  # 父目录不存在，应自动创建
    r = write_file(str(fp), "第一行\n第二行\n")
    assert "已写入" in r
    content = read_file(str(fp))
    assert "第一行" in content and "第二行" in content


def test_read_file_truncates_long_file(tmp_path):
    """给模型的截断：>120 行时头 100 + 尾 20 + 省略标注。"""
    fp = tmp_path / "long.txt"
    fp.write_text("\n".join(f"line{i}" for i in range(1, 151)), encoding="utf-8")
    out = read_file(str(fp))
    assert "line1" in out and "line150" in out  # 头尾都在
    assert "省略" in out and "line110" not in out  # 中段被省略
