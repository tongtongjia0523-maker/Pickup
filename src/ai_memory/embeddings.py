"""向量嵌入：调用 OpenAI 兼容端点的 /embeddings。

与 summarization provider **解耦**：不在 LLMProvider protocol 里，
独立函数、独立探测链、独立降级路径。两端复用的只是「找可用端点」逻辑。

降级策略：
- 没有可用的 /embeddings 端点 → 返回 None（调用方直接跳过语义兜底）
- 端点只支持 chat 不支持 embeddings → 静默返回 None
- 网络超时 / 密钥错 → 静默返回 None

不强制部署、不下载模型、不常驻进程。向量存进主库同一个 SQLite 文件的
独立表 memory_embedding（BLOB 序列化），规模小时 Python 侧算余弦。
"""
from __future__ import annotations

import os
from typing import Optional


# ---------- 端点探测（复用与 provider_from_config 相同的 auto 链） ----------

def _pick_endpoint(cfg: dict) -> Optional[tuple[str, str, str]]:
    """挑一个可用的 embedding 端点：返回 (base_url, model, api_key) 或 None。

    优先级（与 provider_from_config 的 auto 分支完全一致）：
      1) 显式 base_url（配置里写了就直接用，不探测）
      2) 环境变量密钥：OPENAI_API_KEY（Anthropic 不走 embeddings，跳过）
      3) 本机已在运行的服务：Ollama(11434)/LM Studio(1234)/llama.cpp(8080)/vLLM(8000)
      4) 以上都没有 → None
    """
    from ai_memory.providers import detect_local_endpoint  # 延迟导入，避免循环

    p = cfg.get("provider", {})
    base = str(p.get("base_url") or "").rstrip("/")
    model = str(cfg.get("semantic", {}).get("model") or p.get("model") or "")
    api_key = str(p.get("api_key") or "")

    if base:
        return base, model, api_key

    if os.environ.get("OPENAI_API_KEY"):
        return (
            os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            model or "text-embedding-3-small",
            os.environ["OPENAI_API_KEY"],
        )

    if p.get("auto_detect", True):
        found = detect_local_endpoint()
        if found:
            return found[0], model or found[1] or "default", ""

    return None


def embedding_available(cfg: dict) -> bool:
    """当前配置下能否拿到向量？不做实际网络调用，零成本快速判断。"""
    sem = cfg.get("semantic", {})
    if sem.get("enabled") is False:
        return False
    return _pick_endpoint(cfg) is not None


# ---------- 实际调用 ----------

def embed(text: str, cfg: dict, timeout: float = 5.0) -> Optional[list[float]]:
    """把一段文本转成向量。任何失败都返回 None，绝不抛错阻塞宿主。

    timeout 默认 5s — 这是兜底，不应该拖慢检索链路。
    """
    if not text or not text.strip():
        return None

    picked = _pick_endpoint(cfg)
    if picked is None:
        return None

    base, model, api_key = picked
    try:
        import httpx

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # 端点声明支持什么 model 不确定时留空，让服务方用默认
        payload = {"input": text, "model": model} if model else {"input": text}
        resp = httpx.post(f"{base}/embeddings", json=payload,
                          headers=headers, timeout=timeout)
        if resp.status_code != 200:
            return None
        data = resp.json()
        vec = (data.get("data") or [{}])[0].get("embedding")
        if isinstance(vec, list) and all(isinstance(x, (int, float)) for x in vec):
            return [float(x) for x in vec]
    except Exception:
        pass

    return None


# ---------- 序列化 ----------

def vec_to_blob(vec: list[float]) -> bytes:
    """list[float] → BLOB（pickle 序列化）。"""
    import pickle
    return pickle.dumps(vec)


def blob_to_vec(blob: bytes) -> list[float]:
    """BLOB → list[float]。"""
    import pickle
    return pickle.loads(blob)


# ---------- 写入点共用的嵌入 ----------

def _load_cfg_safe(cfg: dict | None) -> dict:
    if cfg is not None:
        return cfg
    try:
        from ai_memory.config import load_config
        return load_config()
    except Exception:
        return {}


def _usable(cfg: dict) -> bool:
    """semantic 开关 + 端点探测，任一不满足就不做任何网络调用。"""
    if cfg.get("semantic", {}).get("enabled") is False:
        return False
    return embedding_available(cfg)


def _model_name(cfg: dict) -> str:
    return str(cfg.get("semantic", {}).get("model") or "default")


def try_embed(store, entry_id: str, text: str, cfg: dict | None = None) -> bool:
    """为一条刚写入的记忆生成并存储向量。任何失败静默跳过，返回是否成功。

    语义开关关闭 / 没有可用端点 / 调用失败 → False，调用方无需关心。
    """
    try:
        cfg = _load_cfg_safe(cfg)
        if not _usable(cfg):
            return False
        vec = embed(text, cfg)
        if vec is None:
            return False
        store.store_embedding(entry_id, vec, _model_name(cfg))
        return True
    except Exception:
        return False


def backfill_embeddings(store, project: str | None = None, limit: int = 20,
                        cfg: dict | None = None) -> int:
    """懒回填：给本项目尚无向量的记忆补 embedding，返回补上的条数。

    只在语义检索真正被触发时调用（FTS 全空），所以不会拖慢常规路径。
    limit 限制单次补的数量；首条就失败说明端点不可用，立刻停止避免逐条超时。
    """
    try:
        cfg = _load_cfg_safe(cfg)
        if not _usable(cfg) or limit <= 0:
            return 0
        model = _model_name(cfg)
        done = 0
        for entry in store.list_missing_embeddings(project=project, limit=limit):
            vec = embed(entry.content, cfg)
            if vec is None:
                break
            store.store_embedding(entry.id, vec, model)
            done += 1
        return done
    except Exception:
        return 0


def semantic_search(store, query: str, project: str | None = None, limit: int = 10,
                    cfg: dict | None = None, backfill_limit: int = 20) -> list:
    """语义兜底检索：开关检查 → 懒回填 → 查询向量 → 余弦排序。

    约定：调用方必须确认 FTS 已经全空才调用（绝不与 FTS 结果混合）。
    未启用 / 无端点 / 任何异常 → 返回空列表。
    """
    try:
        cfg = _load_cfg_safe(cfg)
        if not _usable(cfg):
            return []
        backfill_embeddings(store, project=project, limit=backfill_limit, cfg=cfg)
        vec = embed(query, cfg)
        if vec is None:
            return []
        threshold = float(cfg.get("semantic", {}).get("threshold", 0.3))
        return store.search_semantic(vec, project=project, limit=limit,
                                     threshold=threshold)
    except Exception:
        return []
