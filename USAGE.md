# 使用说明（含手动测试步骤）

面向「想自己跑一遍」的人，从零到能用大约 5 分钟。全程**不需要部署任何模型**，不联网也能用。

---

## 0. 你需要什么

- Python 3.10+（本项目在 3.12 上开发）
- 可选：Claude Code（用于自动续接）；不装也能用 CLI 手动提炼
- 可选：本机已在跑的 OpenAI 兼容服务（Ollama / LM Studio / llama.cpp / vLLM），有就自动用，没有就用本机规则提炼

---

## 1. 安装（3 条命令）

```powershell
cd D:\AI-test\ai-memory-tool
python -m pip install -e ".[dev]"
ai-memory init
```

`ai-memory init` 会：
- 生成配置 `~/.ai-memory/config.toml`（已存在不会覆盖）
- 打印 hook / MCP 的接入方式

---

## 2. 自检：一眼看清是否接线正常

```powershell
ai-memory status
```

正常输出示例（新装、还没聊过 Claude Code）：

```
版本：     0.2.0
记忆库：   C:\Users\你\.ai-memory\memory.db（尚未创建，首次触发时自动建库）
配置文件： C:\Users\你\.ai-memory\config.toml
提炼模型： NoneProvider
会话目录： C:\Users\你\.claude\projects（不存在，尚未使用过 Claude Code？）
当前项目： D--AI-test-你的项目
会话统计： 共 0 个，已入库 0 个，待处理 0 个
记忆条目： 0 条（全库）
```

看到这几行就说明程序没问题：`提炼模型：NoneProvider` = 零模型规则模式；路径没写错；库可以正常读写。

---

## 3. 先手动跑通一次（推荐，不用等 Claude Code）

真实流程里，记忆来源于**上一个**会话——第一次启动必然为空，要下次才有摘要。想立刻验证，用 `run` 直接喂一个假会话。

**准备一个假的会话文件**（模拟 Claude Code 的 jsonl 格式）：

```powershell
$demo = "$env:TEMP\aimem-demo"
New-Item -ItemType Directory -Force "$demo\projects\my-proj" | Out-Null
@'
{"type":"user","message":{"content":"帮我写一个 Python 爬虫，抓取网页标题"}}
{"type":"assistant","message":{"content":"可以，用 requests + BeautifulSoup。"}}
{"type":"user","message":{"content":"好的"}}
{"type":"user","message":{"content":"跑 pytest 报错 ImportError，模块找不到"}}
'@ | Set-Content -Encoding UTF8 "$demo\projects\my-proj\s1.jsonl"

# 让本工具把这里当作会话目录、把库放到临时位置（不污染真实数据）
$env:AI_MEMORY_HOME = $demo
$env:AI_MEMORY_CLAUDE_PROJECTS = "$demo\projects"
```

**提炼 + 查看结果**：

```powershell
ai-memory status --project my-proj     # 应显示「待处理 1 个」
ai-memory run    --project my-proj --catch-up --out-dir "$demo\out"
ai-memory show   latest --project my-proj
ai-memory search 爬虫 --project my-proj
```

`run` 成功后，`$demo\out\summary.md` 就是将要注入给 AI 的摘要：

```
- 目标：帮我写一个 Python 爬虫，抓取网页标题
- 进展：有效对话 3 条（原始 4 条，已去噪）；最近一条 AI 回复：可以，用 requests + BeautifulSoup。
- 结论：（规则提炼，未做语义归纳；细节请用 search_memory 查库或回溯会话引用）
- 待办：跑 pytest 报错 ImportError，模块找不到
- 卡点：跑 pytest 报错 ImportError，模块找不到
- 关键上下文：requests、BeautifulSoup、pytest
```

**注意**：这是零模型的规则提炼，只做「去噪 + 摘录 + 提取文件/命令」，**不会归纳语义**，所以「结论」如实标注未归纳、不编造。想要真正的语义摘要，见第 6 节配置模型。

**清场**（验证完把临时环境变量删掉，避免影响真实使用）：

```powershell
Remove-Item Env:AI_MEMORY_HOME, Env:AI_MEMORY_CLAUDE_PROJECTS
Remove-Item -Recurse -Force "$env:TEMP\aimem-demo"
```

---

## 4. 接入 Claude Code（自动续接）

在项目 `.claude/settings.json`（或 `~/.claude/settings.json`）中加：

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python D:/AI-test/ai-memory-tool/hooks/session_start.py"
          }
        ]
      }
    ]
  },
  "mcpServers": {
    "ai-memory": {
      "command": "python",
      "args": ["-m", "ai_memory.mcp_server"]
    }
  }
}
```

再在项目 `CLAUDE.md` 里加一行，让摘要自动进入 AI 上下文：

```markdown
@.claude/memory/summary.md
```

### 接入后的预期行为

1. **第一次启动**：没有历史会话，什么都不发生（正常）
2. 你和 AI 聊一轮，退出
3. **第二次启动**：hook 自动把上次会话提炼成摘要 → 写入 `memory.db` → 生成 `.claude/memory/summary.md` → 被 `CLAUDE.md` 引用注入
4. AI 需要更深的旧记忆时，会调用 MCP 工具 `get_memory`（上次摘要）或 `search_memory`（全文搜索）

hook 的任何异常都写进 `~/.ai-memory/error.log`，**绝不会拖慢或阻塞 Claude Code 启动**。

---

## 5. 命令速查

| 命令 | 作用 |
|------|------|
| `ai-memory init` | 生成配置 + 打印接入说明 |
| `ai-memory status [--project P]` | 环境自检：路径 / 生效模型 / 待处理会话 |
| `ai-memory run [--project P] [--catch-up]` | 手动触发一次提炼（等同 hook，便于测试） |
| `ai-memory list [--all] [--type T]` | 列出记忆 |
| `ai-memory show <id\|latest>` | 查看全文（id 支持前缀匹配） |
| `ai-memory search <关键词>` | 全文搜索（中文子串可搜） |
| `ai-memory export [--all] [--out f.md]` | 导出 markdown（缺省打印到终端） |
| `ai-memory forget <id> --yes` | 软删除一条 |
| `ai-memory prune [--yes]` | 回收冷藏记忆（默认只预览，不删） |
| `ai-memory stats` | 库统计（条目数、体积、分项目） |

`--project` 缺省 = 当前工作目录对应的项目；`list/export` 加 `--all` 跨项目。

---

## 6. 配置提炼模型（可选）

编辑 `~/.ai-memory/config.toml`：

```toml
[provider]
name = "none"        # none（默认，零模型）| openai | anthropic
base_url = ""        # 留空 + auto_detect=true 时自动探测本机端点
model = ""
api_key = ""
auto_detect = true
timeout_seconds = 30
```

**自动探测**：`name = "openai"` 时，工具会依次探测本机 `11434`(Ollama) / `1234`(LM Studio) / `8080`(llama.cpp) / `8000`(vLLM)，有服务就直接用，**不用装 Ollama**、不用改配置；探测不到就静默回退 `none` 规则模式，绝不报错。

云服务同理，填 `base_url` 即可（DeepSeek / Kimi / OpenRouter 等 OpenAI 兼容端点）：

```toml
[provider]
name = "openai"
base_url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
api_key = "sk-..."
```

`anthropic` 走官方 SDK，需环境变量 `ANTHROPIC_API_KEY`。

```toml
[trigger]
catch_up = false   # true：每次启动把项目内所有未提炼会话补入库（摘要仍只注入最新一条）
max_catchup = 10

[retention]
enabled = false       # 默认不自动回收
cold_days = 180       # 超期且从未被搜索命中 → 回收候选
delete_policy = "archive_only"   # archive_only=只报告 | hard=启动时软删除
```

改完配置**下次会话生效**（hook 每次启动重读配置）。

---

## 7. 记忆怎么省 token

三层结构，按需加载，不一次性全塞给 AI：

| 层 | 内容 | 何时进上下文 | token 成本 |
|----|------|-------------|-----------|
| 常驻 | `.claude/memory/summary.md`（**只有最新一条**摘要，六字段精简） | 每次启动自动注入 | 极小、固定 |
| 搜索 | 库里全部历史摘要 | AI 主动调 `search_memory` 时 | 只取命中的片段 |
| 归档 | 已成文的结果类记忆 | 语义检索时 | 命中才返回 |

去重保证同一会话不会重复入库；`catch-up` 批量补课时，其余会话只入库不注入，靠搜索取用。

---

## 8. 常见问题

**Q：接了 hook，但 Claude Code 启动后没有摘要？**
- 第一次启动本来就为空，需要先聊一轮、再启动第二次
- `ai-memory status` 看「待处理」是否为 0；若 > 0，执行 `ai-memory run --catch-up` 立即处理
- 看 `~/.ai-memory/error.log` 是否有异常堆栈

**Q：中文搜不到？**
- 搜索走 FTS5 trigram，**关键词至少 3 个字符**才走全文索引；1-2 个字符会自动回退成 `LIKE` 模糊匹配
- 例：`ai-memory search 爬虫`（2 字，走 LIKE）；`ai-memory search 数据库层`（4 字，走索引）

**Q：为什么只注入一条摘要，不是全部？**
- 故意的。全部注入会吃 token；其余历史靠 `search_memory` 按需检索

**Q：多个窗口/多台机器同时写库会不会冲突？**
- 库是 WAL 模式 + 3 秒忙等，同一台机器多窗口并发读写安全
- 同一 `(项目, session)` 摘要有唯一索引，重复触发不会写两条

**Q：想彻底清空重来？**
```powershell
Remove-Item "$env:USERPROFILE\.ai-memory\memory.db*"   # 含 -wal / -shm
```

**Q：数据存在哪？**
- 记忆库：`~/.ai-memory/memory.db`
- 配置：`~/.ai-memory/config.toml`
- 错误日志：`~/.ai-memory/error.log`
- 注入文件：项目下 `.claude/memory/summary.md`

---

## 9. 跑测试

```powershell
cd D:\AI-test\ai-memory-tool
python -m pytest -q
```
