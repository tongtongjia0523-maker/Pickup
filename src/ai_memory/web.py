"""本地网页配置面板（仅本机）。

启动：ai-memory web [--port 8765]

设计约束（与三期规划一致）：
- 只用标准库 http.server，不引入 Flask/FastAPI 等框架
- 前端是内嵌的单页 HTML + 原生 JS，无构建链、无 CDN，离线可用
- 只监听 127.0.0.1，不监听 0.0.0.0；不做登录（前提是只在本机使用）
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from ai_memory.config import DEFAULT_CONFIG, config_path, load_config, save_config_values
from ai_memory.paths import current_project, default_db_path
from ai_memory.store import MemoryStore

# 允许网页编辑的键（白名单，避免写入未知配置）
_EDITABLE = {
    "provider": ("name", "base_url", "model", "api_key", "auto_detect", "timeout_seconds"),
    "trigger": ("catch_up", "max_catchup"),
    "retention": ("enabled", "cold_days", "delete_policy"),
    "semantic": ("enabled", "threshold", "model"),
}


def _mask(cfg: dict) -> dict:
    """转成前端可用的配置：api_key 不回传明文，只告诉是否已设置。"""
    out = {section: dict(cfg.get(section, {})) for section in _EDITABLE}
    key = out["provider"].get("api_key") or ""
    out["provider"]["api_key"] = ""
    out["provider"]["api_key_set"] = bool(key)
    return out


def _coerce(key: str, value: Any) -> Any:
    """按默认值类型转换（前端一律传字符串/布尔）。"""
    example = DEFAULT_CONFIG.get("provider", {}).get(key)
    if example is None:
        for section in DEFAULT_CONFIG.values():
            if key in section:
                example = section[key]
                break
    if isinstance(example, bool):
        return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
    if isinstance(example, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return example
    return "" if value is None else str(value)


def build_state(store: MemoryStore, project: str) -> dict:
    """装配配置页首屏数据（纯函数，便于测试）。"""
    cfg = load_config()
    projects = sorted({e.project for e in store.list_entries(limit=100000)})
    if project not in projects:
        projects.append(project)
    core = store.get_core(project)
    return {
        "config": _mask(cfg),
        "config_path": str(config_path()),
        "db_path": str(default_db_path()),
        "project": project,
        "projects": projects,
        "core": core.content if core else "",
        "stats": store.stats(),
    }


def list_memories(store: MemoryStore, project: str | None, query: str,
                  type_: str | None, limit: int) -> list[dict]:
    """记忆列表：有 query 走分级检索，无 query 按时间倒序列出。"""
    if query.strip():
        entries = store.search_tiered(query.strip(), project=project, limit=limit)
    else:
        entries = store.list_entries(project=project, type=type_, limit=limit)
    return [
        {
            "id": e.id, "type": e.type, "project": e.project,
            "session_id": e.session_id, "created_at": e.created_at,
            "content": e.content, "importance": e.importance,
            "last_accessed_at": e.last_accessed_at,
        }
        for e in entries
    ]


class _Handler(BaseHTTPRequestHandler):
    store: MemoryStore
    project_default: str

    # ---------- 基础 ----------

    def log_message(self, fmt, *args):  # 静音访问日志，保持终端干净
        pass

    def _host_ok(self) -> bool:
        """防 DNS rebinding：只接受本机 Host。"""
        host = (self.headers.get("Host") or "").split(":")[0].strip().lower()
        return host in ("127.0.0.1", "localhost", "")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}

    # ---------- GET ----------

    def do_GET(self) -> None:
        if not self._host_ok():
            self._json({"error": "forbidden"}, 403)
            return
        url = urlparse(self.path)
        query = parse_qs(url.query)
        project = (query.get("project") or [self.project_default])[0]

        if url.path in ("/", "/index.html"):
            self._send(200, _HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif url.path == "/api/state":
            self._json(build_state(self.store, project))
        elif url.path == "/api/core":
            core = self.store.get_core(project)
            self._json({"project": project, "content": core.content if core else ""})
        elif url.path == "/api/memories":
            q = (query.get("q") or [""])[0]
            type_ = (query.get("type") or [None])[0] or None
            try:
                limit = int((query.get("limit") or ["100"])[0])
            except ValueError:
                limit = 100
            self._json({"items": list_memories(self.store, project, q, type_, limit)})
        else:
            self._json({"error": "not found"}, 404)

    # ---------- POST ----------

    def do_POST(self) -> None:
        if not self._host_ok():
            self._json({"error": "forbidden"}, 403)
            return
        path = urlparse(self.path).path
        data = self._body()

        if path == "/api/config":
            updates = {}
            for section, keys in _EDITABLE.items():
                incoming = data.get(section)
                if not isinstance(incoming, dict):
                    continue
                values = {}
                for key in keys:
                    if key in incoming:
                        # 空 api_key 表示「不修改」，避免把已保存的密钥清掉
                        if key == "api_key" and not incoming[key]:
                            continue
                        values[key] = _coerce(key, incoming[key])
                if values:
                    updates[section] = values
            save_config_values(updates)
            self._json({"ok": True, "config": _mask(load_config()),
                        "config_path": str(config_path())})
        elif path == "/api/core":
            project = str(data.get("project") or self.project_default)
            content = str(data.get("content") or "")
            entry = self.store.set_core(project, content)
            # 写入后尽力生成向量；失败静默跳过，不影响保存结果
            from ai_memory import embeddings as _emb
            _emb.try_embed(self.store, entry.id, content)
            self._json({"ok": True, "id": entry.id, "project": project})
        elif path == "/api/memories/delete":
            entry_id = str(data.get("id") or "")
            self._json({"ok": self.store.soft_delete(entry_id)})
        else:
            self._json({"error": "not found"}, 404)


def create_server(port: int = 8765, project: str | None = None,
                  store: MemoryStore | None = None):
    """创建本地 HTTP server（不启动），端口被占用时顺延最多 9 个。"""
    store = store or MemoryStore(default_db_path())
    handler = type("BoundHandler", (_Handler,),
                   {"store": store, "project_default": project or current_project()})
    for candidate in range(port, port + 10):
        try:
            return ThreadingHTTPServer(("127.0.0.1", candidate), handler), candidate
        except OSError:
            continue
    raise SystemExit(f"端口 {port}-{port + 9} 都被占用，请用 --port 指定其他端口。")


def run(port: int = 8765, project: str | None = None, open_browser: bool = True) -> None:
    """启动本地配置面板（阻塞直到 Ctrl+C）。"""
    server, port = create_server(port, project)
    url = f"http://127.0.0.1:{port}/"
    print(f"配置面板：{url}（仅本机可访问，Ctrl+C 退出）")
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pickup · 记忆配置</title>
<style>
  :root { --bg:#f7f7f5; --card:#fff; --line:#e5e5e0; --ink:#1f2320; --muted:#6b7168;
          --accent:#2f6f4f; --danger:#a33a2f; }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
         background:var(--bg); color:var(--ink); }
  header { padding:20px 24px 0; }
  h1 { font-size:19px; margin:0 0 4px; letter-spacing:.2px; }
  .sub { color:var(--muted); font-size:12.5px; }
  .tabs { display:flex; gap:4px; padding:16px 24px 0; border-bottom:1px solid var(--line); }
  .tab { border:0; background:none; padding:9px 14px; font-size:13.5px; color:var(--muted);
         cursor:pointer; border-bottom:2px solid transparent; }
  .tab.on { color:var(--accent); border-bottom-color:var(--accent); font-weight:600; }
  main { padding:22px 24px 48px; max-width:900px; }
  .panel { display:none; } .panel.on { display:block; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px;
          padding:18px 20px; margin-bottom:16px; }
  .card h2 { font-size:14px; margin:0 0 14px; }
  .row { display:grid; grid-template-columns:170px 1fr; gap:10px; align-items:center;
         margin-bottom:11px; }
  label { color:var(--muted); font-size:12.5px; }
  input[type=text], input[type=password], input[type=number], select, textarea {
    width:100%; padding:7px 9px; border:1px solid var(--line); border-radius:6px;
    font:inherit; background:#fff; color:var(--ink); }
  textarea { min-height:150px; resize:vertical; font-family:ui-monospace,Consolas,monospace;
             font-size:12.5px; }
  .hint { color:var(--muted); font-size:11.5px; margin-top:3px; }
  button.primary { background:var(--accent); color:#fff; border:0; border-radius:6px;
                   padding:8px 16px; font:inherit; cursor:pointer; }
  button.ghost { background:none; border:1px solid var(--line); border-radius:6px;
                 padding:5px 10px; font:inherit; cursor:pointer; color:var(--ink); }
  button.danger { color:var(--danger); border-color:#e3cdc9; }
  .bar { display:flex; gap:8px; align-items:center; margin-bottom:14px; }
  .bar input { flex:1; }
  .msg { font-size:12.5px; margin-left:8px; }
  .ok { color:var(--accent); } .err { color:var(--danger); }
  .item { border:1px solid var(--line); border-radius:8px; padding:11px 13px; margin-bottom:9px;
          background:#fbfbf9; }
  .item .meta { font-size:11.5px; color:var(--muted); display:flex; gap:10px;
                flex-wrap:wrap; align-items:center; }
  .badge { background:#eef2ef; color:var(--accent); border-radius:4px; padding:1px 6px;
           font-size:11px; }
  .item pre { margin:7px 0 0; white-space:pre-wrap; word-break:break-word;
              font:12.5px/1.55 ui-monospace,Consolas,monospace; }
  .empty { color:var(--muted); padding:18px 0; }
  .paths { font:11.5px ui-monospace,Consolas,monospace; color:var(--muted); }
</style>
</head>
<body>
<header>
  <h1>Pickup · 记忆配置</h1>
  <div class="sub">仅本机可访问 · 保存后下次会话生效</div>
  <div class="paths" id="paths"></div>
</header>

<nav class="tabs">
  <button class="tab on" data-panel="cfg">配置</button>
  <button class="tab" data-panel="core">核心记忆</button>
  <button class="tab" data-panel="mem">记忆浏览</button>
</nav>

<main>
  <section class="panel on" id="panel-cfg">
    <div class="card">
      <h2>提炼模型</h2>
      <div class="row"><label>模式 name</label>
        <select id="p_name">
          <option value="auto">auto — 自动接入可用模型</option>
          <option value="none">none — 纯本地规则提炼</option>
          <option value="openai">openai — OpenAI 兼容端点</option>
          <option value="anthropic">anthropic — Anthropic API</option>
        </select></div>
      <div class="row"><label>端点 base_url</label>
        <div><input type="text" id="p_base" placeholder="http://localhost:1234/v1">
        <div class="hint">留空时 auto 模式自动探测本机 11434/1234/8080/8000</div></div></div>
      <div class="row"><label>模型 model</label>
        <input type="text" id="p_model" placeholder="留空则取探测到的第一个模型"></div>
      <div class="row"><label>API Key</label>
        <div><input type="password" id="p_key" placeholder="留空表示不修改">
        <div class="hint" id="key_state"></div></div></div>
      <div class="row"><label>自动探测</label>
        <select id="p_detect"><option value="true">开启</option><option value="false">关闭</option></select></div>
      <div class="row"><label>超时（秒）</label>
        <input type="number" id="p_timeout" min="1" max="600"></div>
    </div>

    <div class="card">
      <h2>启动触发</h2>
      <div class="row"><label>补课 catch_up</label>
        <select id="t_catch"><option value="false">关闭（只处理最新一个会话）</option>
        <option value="true">开启（补入库所有未提炼会话）</option></select></div>
      <div class="row"><label>补课上限</label>
        <input type="number" id="t_max" min="1" max="200"></div>
    </div>

    <div class="card">
      <h2>记忆回收</h2>
      <div class="row"><label>自动回收</label>
        <select id="r_enabled"><option value="false">关闭（推荐）</option>
        <option value="true">开启</option></select></div>
      <div class="row"><label>冷藏天数</label>
        <input type="number" id="r_days" min="1" max="3650"></div>
      <div class="row"><label>回收方式</label>
        <select id="r_policy">
          <option value="archive_only">仅报告，不删除</option>
          <option value="hard">软删除</option></select></div>
      <div class="hint">core_memory 与 archive 类型永不自动回收。</div>
    </div>

    <button class="primary" id="save_cfg">保存配置</button><span class="msg" id="cfg_msg"></span>
  </section>

  <section class="panel" id="panel-core">
    <div class="card">
      <h2>常驻核心记忆</h2>
      <div class="hint" style="margin-bottom:8px">
        角色、性格、写作风格、高频问题等。每个项目一条，会常驻注入会话开头（上限 300 字）。</div>
      <textarea id="core_text" placeholder="例：我是后端工程师，偏好简洁直接的回答；写作风格偏正式；常问 Python/数据相关问题。"></textarea>
      <div style="margin-top:10px">
        <button class="primary" id="save_core">保存</button>
        <span class="msg" id="core_msg"></span>
        <span class="hint" id="core_len" style="margin-left:8px"></span>
      </div>
    </div>
  </section>

  <section class="panel" id="panel-mem">
    <div class="bar">
      <input type="text" id="q" placeholder="搜索记忆（≥3 字符走全文索引，命中后逐级下探）">
      <select id="f_type" style="max-width:150px">
        <option value="">全部类型</option>
        <option value="session_summary">会话摘要</option>
        <option value="archive">归档</option>
        <option value="scratch">零散</option>
        <option value="core_memory">核心记忆</option>
      </select>
      <button class="ghost" id="do_search">搜索</button>
    </div>
    <div id="mem_list"><div class="empty">加载中…</div></div>
  </section>
</main>

<script>
const $ = (id) => document.getElementById(id);
let PROJECT = "";
let PROJECTS = [];

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function setMsg(el, text, ok) {
  el.textContent = text;
  el.className = "msg " + (ok ? "ok" : "err");
  if (text) setTimeout(() => { el.textContent = ""; }, 3000);
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("on", b === btn));
    document.querySelectorAll(".panel").forEach((p) =>
      p.classList.toggle("on", p.id === "panel-" + btn.dataset.panel));
    if (btn.dataset.panel === "mem") loadMemories();
  };
});

async function loadState() {
  const r = await fetch("/api/state");
  const s = await r.json();
  PROJECT = s.project; PROJECTS = s.projects;
  const p = s.config.provider, t = s.config.trigger, rt = s.config.retention;
  $("p_name").value = p.name || "auto";
  $("p_base").value = p.base_url || "";
  $("p_model").value = p.model || "";
  $("p_key").value = "";
  $("p_detect").value = String(p.auto_detect !== false);
  $("p_timeout").value = p.timeout_seconds ?? 30;
  $("key_state").textContent = p.api_key_set ? "已保存密钥（留空则保持不变）" : "尚未设置密钥";
  $("t_catch").value = String(!!t.catch_up);
  $("t_max").value = t.max_catchup ?? 10;
  $("r_enabled").value = String(!!rt.enabled);
  $("r_days").value = rt.cold_days ?? 180;
  $("r_policy").value = rt.delete_policy || "archive_only";
  $("core_text").value = s.core || "";
  updateCoreLen();
  $("paths").textContent = "库：" + s.db_path + "  ·  配置：" + s.config_path + "  ·  项目：" + s.project;
}

function updateCoreLen() {
  const n = $("core_text").value.length;
  $("core_len").textContent = n + " 字" + (n > 300 ? "（超出 300，注入时会被截断）" : "");
}
$("core_text").oninput = updateCoreLen;

$("save_cfg").onclick = async () => {
  const body = {
    provider: {
      name: $("p_name").value, base_url: $("p_base").value, model: $("p_model").value,
      api_key: $("p_key").value, auto_detect: $("p_detect").value === "true",
      timeout_seconds: Number($("p_timeout").value || 30),
    },
    trigger: { catch_up: $("t_catch").value === "true", max_catchup: Number($("t_max").value || 10) },
    retention: { enabled: $("r_enabled").value === "true",
                 cold_days: Number($("r_days").value || 180), delete_policy: $("r_policy").value },
  };
  const r = await fetch("/api/config", {method:"POST", headers:{"Content-Type":"application/json"},
                                        body: JSON.stringify(body)});
  const d = await r.json();
  setMsg($("cfg_msg"), r.ok ? "已保存，下次会话生效" : ("保存失败：" + (d.error || r.status)), r.ok);
  if (r.ok) { $("p_key").value = ""; updateCoreLen(); }
};

$("save_core").onclick = async () => {
  const r = await fetch("/api/core", {method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({project: PROJECT, content: $("core_text").value})});
  setMsg($("core_msg"), r.ok ? "已保存" : "保存失败", r.ok);
};

async function loadMemories() {
  const q = $("q").value.trim(), t = $("f_type").value;
  const url = "/api/memories?project=" + encodeURIComponent(PROJECT) +
              "&q=" + encodeURIComponent(q) + "&type=" + encodeURIComponent(t);
  const r = await fetch(url);
  const d = await r.json();
  const list = d.items || [];
  if (!list.length) {
    $("mem_list").innerHTML = '<div class="empty">没有找到记忆。</div>';
    return;
  }
  $("mem_list").innerHTML = list.map((e) => `
    <div class="item">
      <div class="meta">
        <span class="badge">${esc(e.type)}</span>
        <span>${esc(e.created_at.slice(0, 19).replace("T", " "))}</span>
        ${e.session_id ? `<span>session ${esc(e.session_id.slice(0, 8))}</span>` : ""}
        <span style="margin-left:auto">
          <button class="ghost danger" onclick="delMem('${esc(e.id)}')">删除</button>
        </span>
      </div>
      <pre>${esc(e.content)}</pre>
    </div>`).join("");
}

async function delMem(id) {
  if (!confirm("确定删除这条记忆？（软删除，可搜索不到）")) return;
  await fetch("/api/memories/delete", {method:"POST",
    headers:{"Content-Type":"application/json"}, body: JSON.stringify({id: id})});
  loadMemories();
}

$("do_search").onclick = loadMemories;
$("q").onkeydown = (ev) => { if (ev.key === "Enter") loadMemories(); };
$("f_type").onchange = loadMemories;
loadState();
</script>
</body>
</html>
"""
