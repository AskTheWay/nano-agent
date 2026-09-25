# 04. 权限系统详解：deny-first

> 对应代码：`git diff m3-context m4-permissions`。对照官方机制：
> Permissions (IAM) 与 Settings 页的 `Tool(specifier)` 规则语法。

## 一、为什么 coding agent 必须有权限层

bash / write_file 本质是**任意代码执行**。没有权限层的 agent 等于把 shell
交给了概率模型。Claude Code 把权限做成了完整的 IAM 体系；教学版保留
三个最核心的机制：规则判定、交互确认、拒绝观测。

## 二、deny-first：顺序即优先级

```python
def check(tool_name, args) -> Decision:
    for r in self.deny:                        # 1. deny 先查，命中即死，不询问
        if r.matches(tool_name, args): return Decision.DENY
    for r in self.allow + self.session_allow:  # 2. allow 次查（配置 + 会话级）
        if r.matches(tool_name, args): return Decision.ALLOW
    return self.default                        # 3. 默认 ask（写操作靠它兜底）
```

为什么 deny 必须赢过 allow：安全规则的语义是"无论如何都不许"。
如果 allow 能覆盖 deny，一条宽泛的 allow（如 `bash`）会击穿所有细粒度
deny（如 `bash(rm:*)`）。**这就是"默认开放、例外禁止"和"默认封闭、
例外放行"两种安全哲学的差别**——Claude Code 选了后者倾向（default=ask）。

## 三、规则语法与匹配语义

```
"read_file"              工具名精确（该工具的任何调用）
"read_*"                 工具名通配
"bash(git status)"       bash 命令整串相等（git status -u 不算）
"bash(git diff:*)"       bash 命令前缀匹配（git diff / git diff HEAD 都算）
"read_file(**/.env)"     路径类参数 glob（fnmatch，反斜杠归一化）
```

实现里两个值得记住的坑：

1. **fnmatch 的 `**` 不是跨层 glob**——它把 `**` 当普通 `*`（还会跨越 `/`），
   `**/.env` 匹配不了根目录下的 `.env`。`_path_match()` 手工补了"去掉
   `**/` 前缀"的零层变体来凑出 glob 语义。
2. **Windows 反斜杠**：`mini\.env` 和 `mini/.env` 必须归一到同一形式再比。

## 四、交互确认放哪：调度器的隐藏约束

权限的 ASK 需要弹 `input()`——而**并行线程里弹交互会交叉打架**（两个
线程同时抢 stdin）。所以调度器在【分组阶段】就先查一次权限：

```
只读 且 ALLOW  -> 并行组（线程池里安全执行）
其余（写/ASK/未知） -> 串行组（交互确认只发生在主线程，天然串行）
```

这是"权限系统"和"调度器"两个机制的真实耦合点——单看任何一边都
发现不了，合起来才会遇到。面试聊到这里是真正的加分项。

## 五、拒绝不是异常，是观测

```
模型 ──read_file(.env)──> [权限拒绝] read_file(path='.env') 未被允许执行。
模型 <── 请换一个不需要该操作的方式完成任务，或向用户说明需要授权。
模型 ──read_file(config.py)──> ...   ← 自己改道
```

和 M1 的"错误即观测"同构：把拒绝包装成 tool 消息返回，模型天然会
换路。对比抛异常打断循环——观测式的拒绝让 agent 保持运转。

## 六、"always allow" 的会话级实现

终端确认支持 `y / n / a` 三键。`a` 调 `remember_allow()` 往**内存中的
会话级 allow 列表**追加规则（bash 按首 token 放行，如 `bash(python:*)`；
其他工具按工具名放行）。对照官方：Claude Code 的 "always allow" 会写进
settings.json **持久化**——教学版选会话级是刻意的保守：重启后重新问一遍。

## 七、动手验收清单

- [ ] `帮我读一下 .env` → 命中 deny（`read_file(**/.env)`），无确认直接拒绝，
      模型回复改道方案（比如改读 .env.example）
- [ ] `跑下 git status` → 命中 allow，静默执行
- [ ] `在 sandbox/ 创建 test.txt 随便写点什么` → 弹 y/n/a；选 `a` 后
      同会话第二次写文件不再询问（`/permissions` 可查看会话级规则）
- [ ] `跑下 git push` → `bash(git diff:*)` 前缀不匹配，弹确认
      （前缀匹配的边界：`git diffx` 也不该匹配 `git diff:*`——
      实现里用 `prefix + " "` 或整串相等判断）

## 八、这一层在面试里怎么说

> "我的权限引擎是 deny-first 三态判定：deny 命中即死、allow 次之、
> 默认 ask 走终端 y/n/a 确认。规则语法对标 Claude Code 的 Tool(specifier)，
> 支持工具名通配、bash 命令前缀、路径 glob——实现时踩了 fnmatch 的
> `**` 不跨层的坑。有个调度和权限的耦合点：ASK 需要交互，所以权限
> 检查前置到调度分组阶段，只读且放行的才进并行组，其余串行——
> 否则多线程弹 input 会竞争 stdin。"
