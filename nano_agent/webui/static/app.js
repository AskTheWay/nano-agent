/* nano-agent 观测台前端：WebSocket 事件流 -> 六面板渲染。零依赖。 */
"use strict";

const $ = (id) => document.getElementById(id);
const ROLE_COLOR = { system: "#bf3989", user: "#58a6ff", assistant: "#3fb950", tool: "#d29922" };
let session = { token_limit: 30000, rules: { allow: [], deny: [] } };
let stats = { prompt: 0, cached: 0, compacts: 0 };
let lastAnswer = null;  // turn_end 与 POST 响应的查重锚点

/* ---------- 通用 ---------- */
function addDiv(parentId, cls, html) {
  const el = document.createElement("div");
  el.className = cls; el.innerHTML = html;
  const parent = $(parentId); parent.appendChild(el);
  parent.scrollTop = parent.scrollHeight;
  return el;
}
const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ---------- ① 对话流 ---------- */
function chatMsg(role, text) {
  const who = { user: "你", assistant: "nano-agent", tool: "工具", sys: "系统" }[role] || role;
  addDiv("chat-flow", `msg ${role}`, `<span class="who">${who}</span>${esc(text)}`);
}

/* ---------- ② Prompt 组装 ---------- */
function renderPrompt(ev) {
  const stack = $("prompt-stack"); stack.innerHTML = "";
  const list = $("prompt-list"); list.innerHTML = "";
  const total = ev.total || 1;
  $("prompt-total").textContent = `≈ ${ev.total} tokens`;

  const segs = [{ cls: "schema", tip: `工具定义 schema ≈ ${ev.schema_tokens} tok`, w: ev.schema_tokens }]
    .concat(ev.msgs.map((m) => ({ cls: m.role, tip: `${m.role} ≈ ${m.tokens} tok`, w: m.tokens })));
  segs.forEach((s) => {
    const el = document.createElement("div");
    el.className = `seg ${s.cls}`;
    el.style.width = Math.max(0.5, (s.w / total) * 100) + "%";
    el.dataset.tip = s.tip;
    stack.appendChild(el);
  });
  ev.msgs.forEach((m, i) => {
    list.insertAdjacentHTML("beforeend",
      `<details class="pl-row"><summary>` +
      `<span class="dot" style="background:${ROLE_COLOR[m.role]}"></span>` +
      `<span>${m.role} #${i}</span><span class="pv">${esc(m.preview)}</span>` +
      `<span class="tok">${m.tokens} tok ▾</span></summary>` +
      `<pre class="pl-full">${esc(m.full || m.preview)}</pre></details>`);
  });
  // ③ 的估算同步更新
  $("s-est").textContent = ev.total;
  updateMeter(ev.total);
}

/* ---------- ③ 上下文仪表盘 ---------- */
function updateMeter(total) {
  const pct = Math.min(100, (total / session.token_limit) * 100);
  const fill = $("ctx-fill");
  fill.style.width = pct + "%";
  fill.className = "meter-fill" + (pct >= 80 ? " over" : "");
  $("ctx-label").textContent =
    `${total} / ${session.token_limit}（${pct.toFixed(0)}%，黄线=80% 压缩阈值）`;
}
function updateStats() {
  $("s-prompt").textContent = stats.prompt;
  const rate = stats.prompt ? (stats.cached / stats.prompt * 100).toFixed(1) + "%" : "—";
  $("s-cached").textContent = `${stats.cached} (${rate})`;
  $("s-compact").textContent = stats.compacts;
}

/* ---------- ④ 调度时间线（只画主线；子代理的轮次归⑥） ---------- */
let currentTurnDiv = null, currentGrp = null, taskNo = 0;

// 从参数里挑最有信息量的一个显示（command/path/pattern/...），比整坨 JSON 直观
function pickArg(args) {
  if (!args) return "";
  for (const k of ["command", "path", "pattern", "description", "old_str"]) {
    if (args[k] != null) return `${k}=${String(args[k]).slice(0, 60)}`;
  }
  return JSON.stringify(args).slice(0, 60);
}

function renderSchedule(ev) {
  if (!currentTurnDiv || ev.source === "sub") return;
  const mk = (grpCls, title, calls) => {
    const grp = addDiv("sched-flow", `grp ${grpCls}`,
      `<div class="grp-title"><b>${title}</b> ${calls.length} 个</div>`);
    calls.forEach((c) => {
      grp.insertAdjacentHTML("beforeend",
        `<div class="call-row"><span class="badge ask" data-pending="1">…</span>` +
        `<b>${esc(c.name)}</b><span class="call-args">${esc(pickArg(c.args))}</span></div>`);
    });
    return grp;
  };
  if (ev.parallel.length) mk("parallel", "并行（只读·无副作用）", ev.parallel);
  if (ev.serial.length) mk("serial", "串行（写操作·需逐个执行）", ev.serial);
  currentGrp = currentTurnDiv;
}

// 每条新用户消息画一条任务分隔（agent.run 从 turn 1 重新计数，需要视觉边界）
function newTaskDivider(text) {
  addDiv("sched-flow", "task-divider", `▶ 任务 #${++taskNo}：${esc(text.slice(0, 40))}`);
  currentTurnDiv = null; currentGrp = null;
}
function renderPermission(ev) {
  // 找最近一个匹配工具名的 pending 徽章上色
  const rows = document.querySelectorAll("#sched-flow .call-row");
  for (let i = rows.length - 1; i >= 0; i--) {
    const badge = rows[i].querySelector(".badge");
    if (badge && rows[i].querySelector("b").textContent === ev.tool && badge.dataset.pending) {
      badge.dataset.pending = "";
      badge.className = `badge ${ev.decision}`;
      badge.textContent = ev.decision + (ev.answered ? `→${ev.answered}` : "");
      break;
    }
  }
}
function renderToolResult(ev) {
  if (ev.source === "sub") {  // 子代理的工具调用：缩进汇入⑥的 sidechain 卡
    if (subCard) subCard.insertAdjacentHTML("beforeend",
      `<div class="sub-turn">· ${esc(ev.name)}(${esc(pickArg(ev.args))}) ${ev.ok ? "✓" : "✗"}</div>`);
    return;
  }
  if (!currentGrp) return;
  currentGrp.insertAdjacentHTML("beforeend",
    `<div class="call-row"><span class="badge ${ev.ok ? "ok" : "err"}">${ev.ok ? "✓" : "✗"}</span>` +
    `<span class="call-args">${esc(ev.preview.slice(0, 100))}</span></div>`);
  // 可展开的工具卡：摘要一行，点开看完整观测（含全部参数与输出）
  addDiv("chat-flow", "msg toolcard",
    `<span class="who">工具 · ${esc(ev.name)} · 点击展开</span>` +
    `<details><summary>${esc(ev.name)}(${esc(pickArg(ev.args))})` +
    ` → ${esc(ev.preview.slice(0, 120))}</summary>` +
    `<pre>${esc(ev.full || ev.preview)}</pre></details>`);
}

/* ---------- ⑤ 沙箱 ---------- */
async function refreshFiles() {
  const r = await fetch("/api/sandbox"); const data = await r.json();
  const tree = $("file-tree"); tree.innerHTML = "";
  (data.files || []).forEach((f) => {
    tree.insertAdjacentHTML("beforeend",
      `<div class="file"><b>文件</b> <span class="fp">${esc(f.path)}</span> (${f.size}B)` +
      `<pre>${esc(f.preview)}</pre></div>`);
  });
  if (!data.files.length) tree.innerHTML = `<div class="ev" style="color:#8b949e">sandbox/ 为空——让 agent 创建点什么</div>`;
}

/* ---------- ⑥ 子代理 ---------- */
let subCard = null;
function renderSubSpawn(ev) {
  subCard = addDiv("sub-flow", "sub-card",
    `<div class="spawn">↳ 派生子代理 ${esc(ev.sa_type)}</div>` +
    `<div class="desc">${esc(ev.description)}</div>` +
    `<div style="color:#8b949e">（内部事件实时汇入①④面板）</div>`);
}
function renderSubDone(ev) {
  if (!subCard) return;
  subCard.insertAdjacentHTML("beforeend",
    `<div class="done">✓ 完成：${ev.n_tools} 次工具调用 | 子上下文 ≈ ${ev.tokens} tok（已丢弃）</div>` +
    `<div class="summary">${esc(ev.summary)}</div>`);
  subCard = null;
}

/* ---------- 权限确认条 ---------- */
function showConfirm(ev) {
  $("confirm-text").textContent = `[权限确认] ${ev.tool}(${JSON.stringify(ev.args)})`;
  $("confirm-bar").classList.remove("hidden");
  $("confirm-bar").dataset.cid = ev.id;
}
document.querySelectorAll("#confirm-bar button").forEach((b) => {
  b.onclick = async () => {
    await fetch("/api/confirm", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: $("confirm-bar").dataset.cid, answer: b.dataset.ans }) });
    $("confirm-bar").classList.add("hidden");
  };
});

/* ---------- 发送 ---------- */
async function send() {
  const input = $("chat-input"); const text = input.value.trim();
  if (!text) return;
  input.value = "";
  if (text.startsWith("/")) {
    const r = await fetch("/api/command", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cmd: text }) });
    const d = await r.json();
    chatMsg("sys", d.ok ? d.output : d.error);
    refreshFiles();
    return;
  }
  chatMsg("user", text);
  newTaskDivider(text);
  const r = await fetch("/api/chat", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ input: text }) });
  const d = await r.json();
  if (!d.ok) chatMsg("sys", `[出错] ${d.error}`);
  else if (d.answer != null && d.answer !== lastAnswer) chatMsg("assistant", d.answer);  // ws 断线兜底
  refreshFiles();
}
$("send").onclick = send;
$("chat-input").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
document.querySelectorAll("header nav button").forEach((b) => {
  b.onclick = () => { $("chat-input").value = b.dataset.cmd; send(); };
});

/* ---------- 模型配置弹窗（热重载） ---------- */
const cfgModal = $("cfg-modal");
$("cfg-btn").onclick = async () => {
  const d = await (await fetch("/api/config")).json();
  $("cfg-url").value = d.base_url || "";
  $("cfg-model").value = d.model || "";
  $("cfg-key").value = "";
  $("cfg-key").placeholder = d.api_key_masked
    ? `当前：${d.api_key_masked}（留空保留）` : "填入 API key";
  $("cfg-probe").textContent = "";
  cfgModal.classList.remove("hidden");
};
$("cfg-cancel").onclick = () => cfgModal.classList.add("hidden");
$("cfg-save").onclick = async () => {
  $("cfg-probe").textContent = "保存中…";
  const r = await fetch("/api/config", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      base_url: $("cfg-url").value.trim(),
      api_key: $("cfg-key").value.trim(),
      model: $("cfg-model").value.trim(),
    }) });
  const d = await r.json();
  if (!d.ok) { $("cfg-probe").textContent = d.error; return; }
  $("cfg-probe").textContent = d.probe;
  // session_start 事件会自动更新 header；2 秒后收起
  setTimeout(() => cfgModal.classList.add("hidden"), 2000);
  chatMsg("sys", `[配置已热重载] 模型切换为 ${d.model}，会话已清零`);
};

/* ---------- WebSocket 事件路由 ---------- */
function route(ev) {
  switch (ev.type) {
    case "session_start":
      session.token_limit = ev.token_limit; session.rules = ev.rules;
      $("session-info").textContent =
        `模型 ${ev.model} | 工具 ${ev.tools.length} 个 | 预算 ${ev.token_limit}`;
      updateMeter(0);
      break;
    case "turn_start":
      if (ev.source === "sub") {  // 子代理的轮次 -> ⑥面板的 sidechain 卡
        if (subCard) subCard.insertAdjacentHTML("beforeend",
          `<div class="sub-turn">— 子代理 turn ${ev.turn} —</div>`);
        break;
      }
      currentTurnDiv = addDiv("sched-flow", "turn-head", `— turn ${ev.turn} —`);
      currentGrp = null;
      break;
    case "prompt_assembly": renderPrompt(ev); break;
    case "llm_response":
      stats.prompt += ev.usage.prompt; stats.cached += ev.usage.cached;
      updateStats();
      if (ev.tool_calls.length) {
        chatMsg("sys", `模型请求调用 ${ev.tool_calls.length} 个工具：` +
          ev.tool_calls.map((c) => `${c.name}(${c.args.slice(0, 50)})`).join("，"));
      }
      break;
    case "schedule": renderSchedule(ev); break;
    case "permission": renderPermission(ev); break;
    case "permission_ask": showConfirm(ev); break;
    case "tool_result": renderToolResult(ev); break;
    case "file_change":
      addDiv("sandbox-events", "ev", `<b>${ev.op}</b> ${esc(ev.path)}`);
      refreshFiles();
      break;
    case "bash_exec":
      addDiv("sandbox-events", "ev", `<b>bash</b> ${esc(ev.command)} <span class="out">${esc(ev.exit_line)}</span>`);
      break;
    case "compact":
      stats.compacts++; updateStats();
      addDiv("compact-log", "compact-mark",
        `⇩ 压缩：丢弃 ${ev.dropped} 条，${ev.before} → ${ev.after} tok`);
      break;
    case "subagent_spawn": renderSubSpawn(ev); break;
    case "subagent_done": renderSubDone(ev); break;
    case "turn_end":
      lastAnswer = ev.answer != null ? ev.answer : (ev.answer_preview || "");
      chatMsg("assistant", lastAnswer);
      break;
    case "command_output": chatMsg("sys", ev.output); break;
  }
}

function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (e) => route(JSON.parse(e.data));
  ws.onclose = () => setTimeout(connect, 1500);  // 断线重连
}
connect(); refreshFiles(); updateStats();
