"""提炼器：构造 prompt 并调用 provider 生成会话摘要。"""
from ai_memory.providers import LLMProvider

_TEMPLATE = """你是会话摘要提炼器。阅读下面的 AI 编程会话，提炼成结构化摘要用于跨会话续接。

【严格规则】
1. 只输出下面 6 行，每行以「- 字段名：」开头，不要输出任何其他文字
2. 不要复述对话原文，提炼要点即可
3. 忽略问候、寒暄、无关闲聊
4. 某字段没有内容就写「无」

- 目标：一句话说明这次会话在做什么
- 进展：做到了哪一步
- 结论：敲定了什么决定（没有写"无"）
- 待办：下一步要做什么
- 卡点：卡在哪里（没有写"无"）
- 关键上下文：文件名、命令、技术选型等（没有写"无"）

【会话内容】
{conversation}
"""


def build_prompt(conversation: str) -> str:
    return _TEMPLATE.format(conversation=conversation)


def summarize(conversation: str, provider: LLMProvider) -> str:
    """raw_input=True 的 provider（规则提炼）直接吃原始会话，不套 LLM 模板。"""
    if getattr(provider, "raw_input", False):
        return provider.complete(conversation)
    return provider.complete(build_prompt(conversation))
