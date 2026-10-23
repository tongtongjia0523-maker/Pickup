# Pickup

跨会话 AI 记忆工具：**新会话自动接上上次的进度**，不用再从头解释。

接入 Claude Code 后，你第二次打开同一项目，Claude 会在第一条回复里主动告诉你：上次做到哪、结论是什么、下一步该做什么。所有记忆存在本机一个 SQLite 文件里，**默认自动接入你已有的模型**，一个都没有也能用。

> 本仓库即 GitHub 上的 `Pickup`；本地目录名仍是 `ai-memory-tool`（hook 配置的是绝对路径，改目录名会导致 hook 失效）。

---

## 核心功能

| 能力 | 说明 |
|---|---|
| **自动续接** | SessionStart hook 读取本项目上一个会话，提炼 6 字段摘要（目标 / 进展 / 结论 / 待办 / 卡点 / 关键上下文），注入新会话。 |
| **自动接入模型** | 默认 `provider.name = auto`：先看环境变量里的密钥（Anthropic / OpenAI 兼容），再探测本机已在运行的服务（Ollama 11434、LM Studio 1234、llama.cpp 8080、vLLM 8000），有就用 AI 归纳；全都没有则回退纯本地规则提炼。**不需要部署任何模型**。 |
| **常驻核心记忆** | 每个项目一条 `core_memory`：你的角色、性格、写作风格、高频问题。每次会话开头常驻注入（上限 300 字），让 AI 一上来就"认识你"。 |
| **记忆分类** | 会话自动归类：`archive`（已形成结论/产出成品）、`scratch`（零散追问 ≤3 条）、`session_summary`（一般会话）。 |
| **分级检索** | 查记忆时按 `常驻 → 归档 → 会话摘要 → 零散` 逐级下探，前一级命中就停，避免把整库塞进上下文（省 token）。 |
| **去重与并发安全** | 同一会话只入库一次（唯一索引保证，多窗口同时跑也不会重复）；SQLite WAL 模式支持多窗口并发写。 |
| **全文搜索** | SQLite FTS5（trigram）支持中文子串；不足 3 字符自动回退 LIKE。 |
| **语义检索兜底** | FTS 无命中时自动转向量检索（余弦相似度）：复用 provider 的 auto 探测链接入 `/embeddings` 端点，向量存同一个 SQLite 文件。不装向量数据库、不常驻进程、不下载模型；探测不到端点就静默跳过，行为与未开启时完全一致。 |
| **本地配置网页** | `ai-memory web` 启动一个**仅本机可访问**的单页面板：改配置、编辑核心记忆、浏览/搜索/删除记忆，不用手改 TOML。 |
| **隐私优先** | 全部数据在 `~/.ai-memory/memory.db`；默认不出网，只有你配置云端 provider 才会联网。 |
| **绝不阻塞启动** | hook 的任何异常都吞掉并写 `~/.ai-memory/error.log`，Claude Code 照常启动。 |

---

## 快速开始

```bash
cd ai-memory-tool
python -m pip install -e ".[dev]"
ai-memory init        # 生成 ~/.ai-memory/config.toml 并打印接入说明
ai-memory status      # 自检：库路径 / 生效模型 / 待处理会话
```

### 接入 Claude Code（两步）

**1）注册 SessionStart hook**（全局 `~/.claude/settings.json` 或项目 `.claude/settings.json`）：

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "python D:/AI-test/ai-memory-tool/hooks/session_start.py" }
        ]
      }
    ]
  }
}
```

**2）注册 MCP server**（让 AI 能按需深查记忆）：

```bash
claude mcp add ai-memory -- python -m ai_memory.mcp_server
```

> 不需要在 `CLAUDE.md` 里加任何 `@` 引用——hook 直接输出到会话上下文。

### 关于"自动出现摘要"

Claude Code 的机制是：**在你发出第一条消息之前，模型不会主动产出任何回复**。所以能做到的最接近效果是——hook 把摘要注入上下文，并在注入文本里要求模型"在本轮回复的最开头主动复述"。实际体验：打开 `claude` 后随便说一句（甚至只说 `Hi`），它第一句就会先把上次进度讲给你；不需要你专门问"我们上次做到哪"。

如果你完全不打字，终端里不会有内容——这是宿主协议决定的，不是本工具的问题。

---

## 使用方式

### 日常（零操作）

启动 `claude` → 说话 → 它先复述上次进度 → 继续干活。会话结束自动入库，无需任何命令。

### 管理（偶尔用）

```bash
ai-memory status                     # 环境自检
ai-memory web                        # 打开本地配置网页（推荐）
ai-memory core show                  # 查看常驻核心记忆
ai-memory core set "我是后端工程师，偏好简洁回答…"
ai-memory list [--all] [--type T]    # 列出记忆
ai-memory show <id|latest>           # 查看全文（支持 id 前缀）
ai-memory search <关键词>            # 分级检索
ai-memory export [--all] [--out f]   # 导出 markdown
ai-memory forget <id> --yes          # 软删除
ai-memory prune [--yes]              # 回收冷藏记忆（默认 dry-run）
ai-memory stats                      # 库统计
ai-memory run [--catch-up]           # 手动触发一次提炼（不用重启 Claude Code）
```

### 本地配置网页

```bash
ai-memory web              # 默认 http://127.0.0.1:8765/
ai-memory web --port 9000 --no-open
```

三个页签：

1. **配置** —— provider / trigger / retention / semantic 全部可编辑，保存即写回 `config.toml`（保留原有注释）；API Key 不回显明文，留空表示不修改。
2. **核心记忆** —— 编辑本项目常驻记忆，实时显示字数（>300 会提示截断）。
3. **记忆浏览** —— 搜索、按类型筛选、查看全文、软删除。

安全边界：只监听 `127.0.0.1`，校验 Host 头防 DNS rebinding，无账号体系（前提是只在本机使用）。

---

## 配置

`~/.ai-memory/config.toml`（`ai-memory init` 生成；网页或手工编辑均可）：

```toml
[provider]
name = "auto"        # auto | none | openai | anthropic
base_url = ""        # 留空时 auto 自动探测本机服务
model = ""           # 留空取探测到的第一个模型
api_key = ""
auto_detect = true   # 探测 11434/1234/8080/8000
timeout_seconds = 30

[trigger]
catch_up = false     # true 时启动补入库所有未提炼会话（注入仍只给最新一条）
max_catchup = 10

[retention]
enabled = false      # 默认不自动回收
cold_days = 180      # 超过且从未被访问 → 回收候选
delete_policy = "archive_only"   # archive_only=只报告 | hard=启动时软删除

[semantic]
enabled = true       # FTS 全空时是否尝试语义兜底（探测不到 /embeddings 端点自动跳过）
threshold = 0.3      # 余弦相似度阈值，过低会带入噪声
model = ""           # embedding 模型；留空让端点用默认
```

| provider.name | 行为 |
|---|---|
| `auto`（默认） | 显式 `base_url` → `ANTHROPIC_API_KEY` → `OPENAI_API_KEY` → 本机端口探测 → 规则提炼 |
| `none` | 强制纯本地规则提炼，零依赖、可离线 |
| `openai` | 指定 OpenAI 兼容端点（云端或本机），`base_url` 留空则探测 |
| `anthropic` | Anthropic API，需 `ANTHROPIC_API_KEY` |

环境变量：`AI_MEMORY_PROVIDER` / `AI_MEMORY_DB` / `AI_MEMORY_CONFIG` / `AI_MEMORY_HOME` / `AI_MEMORY_CLAUDE_PROJECTS`。

---

## 语义检索兜底

全文检索（FTS/LIKE）查不到时，换个说法往往能查到——这就是语义兜底解决的问题。

### 触发规则

```
search_memory（MCP）/ ai-memory search（CLI）
  └─ 分级检索（FTS）：常驻 → 归档 → 会话摘要 → 零散
       ├─ 有命中 → 直接返回，语义路径完全不触发（零开销）
       └─ 全空   → 语义兜底：懒回填存量向量 → embed 查询词 → 余弦排序
```

**只在 FTS 全空时才走语义，两者绝不混合打分**——排序永远可解释。

### embedding 端点探测链

与 provider 的 auto 模式同一条链，但打 `/embeddings` 接口：

1. 配置里显式写了 `base_url` → 直接用
2. 环境变量 `OPENAI_API_KEY`（`OPENAI_BASE_URL` 可覆盖默认）→ 用 OpenAI 官方
3. 本机端口探测（Ollama 11434 / LM Studio 1234 / llama.cpp 8080 / vLLM 8000）→ 用本机服务
4. 都没有 → **语义路径静默跳过**，行为与 `[semantic] enabled = false` 时完全一致

Anthropic 不提供兼容 `/embeddings` 的接口，所以语义兜底不走 `ANTHROPIC_API_KEY`。

### 存储与计算

- 向量就地存在**同一个 SQLite 文件**的独立表 `memory_embedding`（BLOB 序列化），主表不添列，老库一键迁移（schema v3）
- 相似度用纯 Python 余弦计算——本工具目标规模是数百到几千条记忆，这个量级下 Python 足够快
- **明确不做**：向量数据库服务、常驻进程、embedding 模型兜底下载

### 懒回填

首次语义检索时，为本项目还没有向量的记忆批量补 embedding（单次上限 20 条，首条失败立即熔断防止逐条超时）。剩余的在后续检索中分批补齐，不需要手动重建。写入侧同理：会话摘要入库、`ai-memory core set`、网页保存核心记忆，三处都会顺手生成向量，失败静默跳过。

### 配置

```toml
[semantic]
enabled = true     # false 时完全关闭，行为与二期一致
threshold = 0.3    # 余弦阈值；调低召回多但噪声多，调高只留强相关
model = ""         # 留空让端点用默认模型；Ollama 可填 "nomic-embed-text" 等
```

隐私边界不变：只有探测到端点才会联网，向量数据不出本机。

---

## 工作原理

```
SessionStart hook
  ├─ 扫描本项目 session 文件（~/.claude/projects/<project>/*.jsonl）
  ├─ 只处理未入库的会话（跨类型去重）
  ├─ 提炼 6 字段摘要（AI 或规则）
  ├─ 自动分类 archive / scratch / session_summary
  ├─ 写入 SQLite（WAL + FTS5 trigram + 向量表）
  └─ 输出「核心记忆 + 最新摘要」到会话上下文
AI 需要更深记忆时 → MCP 工具 get_memory / search_memory
                     └─ 分级检索（FTS）→ 全空才语义兜底（余弦）
```

`provider` 选用 `none` 时，规则提炼做的是「确定性摘录」：去噪、取首条有效用户目标、末条待办、错误行作为卡点、提取文件路径与命令；「结论」明确标注**未做语义归纳**，不编造。配置为 `auto` 且探测到模型时，摘要由模型归纳。

---

## 运行测试

```bash
python -m pytest -q
```

## 许可

MIT（见 [LICENSE](LICENSE)）。
