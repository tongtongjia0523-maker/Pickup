# AI 记忆工具（会话摘要续接）

个人版跨会话续接工具：新会话开始时，自动提炼上次会话摘要并注入，让 AI 接得上「上次聊到哪、结论是什么、下一步做什么」。开源、纯本地、**默认零模型**——不部署任何 LLM 也能用。

> 想直接上手测试？看 **[使用说明 USAGE.md](USAGE.md)**（含 5 分钟手动跑通步骤）。

## 安装

```bash
cd ai-memory-tool
python -m pip install -e ".[dev]"
ai-memory init    # 写入默认配置 + 打印接入说明
```

## 接入 Claude Code

### 1. 注册 SessionStart hook 与 MCP server

在项目 `.claude/settings.json` 中：

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

### 2. 让摘要自动加载进上下文

在项目 `CLAUDE.md` 加一行：

```markdown
@.claude/memory/summary.md
```

## 提炼模型：默认不需要任何模型

配置文件 `~/.ai-memory/config.toml`（`ai-memory init` 生成，全部项有默认值）：

| provider.name | 说明 |
|---------------|------|
| `none`（默认） | 纯本地规则提炼：去噪 + 摘录关键消息 + 提取路径/命令。零依赖零成本可离线。「结论」明确标注未做语义归纳，不编造。 |
| `openai` | 任意 OpenAI 兼容端点。`base_url` 留空且 `auto_detect=true` 时自动探测本机已在运行的 Ollama(11434)/LM Studio(1234)/llama.cpp(8080)/vLLM(8000)——**无需安装任何东西，有就用、没有就静默回退 none**。也支持 DeepSeek/Kimi/OpenRouter 等云端兼容端点。 |
| `anthropic` | Anthropic API，需 `ANTHROPIC_API_KEY` 环境变量。 |

其他配置段：

```toml
[trigger]
catch_up = false    # true 时启动把项目内所有未提炼会话补入库（摘要仍只注入最新一条）
max_catchup = 10

[retention]
enabled = false     # 默认不自动回收
cold_days = 180     # 超过且从未被搜索命中 → 回收候选（core_memory/archive 永不回收）
delete_policy = "archive_only"  # archive_only=只报告 | hard=hook 启动时软删除
```

环境变量 `AI_MEMORY_PROVIDER` / `AI_MEMORY_DB` / `AI_MEMORY_CONFIG` 可覆盖配置。

## CLI

```bash
ai-memory status                     # 环境自检：路径 / 生效模型 / 待处理会话
ai-memory run [--catch-up]           # 手动触发一次提炼（等同 hook，便于测试）
ai-memory list [--all] [--type T]   # 列出记忆（默认当前项目）
ai-memory show <id|latest>          # 查看全文（支持 id 前缀）
ai-memory search <关键词>           # 全文搜索（FTS5 trigram，支持中文子串）
ai-memory export [--all] [--out f]  # 导出 markdown（默认 stdout）
ai-memory forget <id> --yes         # 软删除
ai-memory prune [--yes]             # 回收冷藏记忆（默认 dry-run）
ai-memory stats                     # 库统计
```

## 工作原理

SessionStart hook 确定性触发 → 扫描**本项目**的 session 文件 → 提炼 6 字段摘要（目标/进展/结论/待办/卡点/关键上下文）→ 累积写入 SQLite（同一 session 摘要去重，多窗口并发安全 WAL）→ 导出 `summary.md` 被 CLAUDE.md 引用自动加载。AI 需要更深记忆时调用 MCP 工具 `get_memory`（上次摘要）或 `search_memory`（全文搜索）。

hook 任何异常都写入 `~/.ai-memory/error.log`，绝不阻塞 Claude Code 启动。

## 运行测试

```bash
python -m pytest -v
```
