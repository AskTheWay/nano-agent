"""pytest 共享夹具：测试用的假 LLM（两轮固定剧本，不触网）。"""


class FakeLLM:
    """第一轮并行请求读两个文件，第二轮收尾。"""

    def __init__(self):
        self.n = 0

    def chat(self, messages, tools):
        self.n += 1
        if self.n == 1:
            return {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "read_file", "arguments": '{"path": "f1.txt"}'}},
                {"id": "c2", "type": "function",
                 "function": {"name": "read_file", "arguments": '{"path": "f2.txt"}'}},
            ]}, None
        return {"role": "assistant", "content": "done"}, None
