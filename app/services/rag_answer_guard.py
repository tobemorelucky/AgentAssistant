"""Guards that keep standard RAG chat from pretending to be realtime AIOps."""

from __future__ import annotations

from typing import Iterable


REALTIME_SCOPE_KEYWORDS = (
    "当前服务器",
    "当前主机",
    "当前系统",
    "实时主机",
    "实时服务器",
    "现在服务器",
    "系统现在",
    "当前是否异常",
)

REALTIME_RESOURCE_KEYWORDS = (
    "cpu",
    "内存",
    "memory",
    "磁盘",
    "disk",
    "系统",
    "服务器",
    "主机",
)

REALTIME_ACTION_KEYWORDS = (
    "检查",
    "巡检",
    "看看",
    "分析",
    "情况",
    "状态",
    "是否异常",
    "怎么样",
)


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def is_realtime_status_request_in_rag(question: str) -> bool:
    text = (question or "").strip()
    if not text:
        return False
    if _contains_any(text, REALTIME_SCOPE_KEYWORDS):
        return True
    return _contains_any(text, REALTIME_RESOURCE_KEYWORDS) and _contains_any(text, REALTIME_ACTION_KEYWORDS)


def build_rag_realtime_guard_answer(question: str) -> str:
    return (
        "当前是知识问答模式，不能直接读取实时主机或服务器状态，因此我不能把知识库里的示例数据当成当前现场事实。\n\n"
        "如果你需要查看当前 CPU、内存、磁盘或系统是否异常，请切换到 AIOps 模式发起实时诊断。\n\n"
        "如果后续引用知识库中的案例或 runbook，它们只应视为历史案例/示例参考，不代表当前实时状态。"
    )
