"""Report builder for dependent AIOps follow-up enrichment turns."""

from __future__ import annotations

from textwrap import dedent
from typing import Any


def build_followup_enrichment_report(state: dict[str, Any]) -> str:
    previous = dict(state.get("previous_aiops_context") or {})
    resolution = dict(state.get("followup_resolution") or {})
    evidence_store = dict(state.get("evidence_store") or {})
    current_query = str(state.get("input") or "").strip()
    previous_profile = str(previous.get("previous_profile_id") or "unknown-profile")
    previous_object = str(previous.get("previous_target_object") or "unknown-target")
    previous_query = str(previous.get("previous_user_query") or "未记录上一轮问题")
    previous_summary = _build_compact_previous_summary(previous)
    previous_recommendations = str(previous.get("previous_recommendations") or "未记录上一轮处理建议")
    safety_notes = str(
        previous.get("previous_action_safety_notes")
        or "本轮未执行任何重启、扩容、限流、清理或其他高风险操作。"
    )

    local_payload = _first_payload(evidence_store, ["cpu_runbook", "memory_runbook", "disk_runbook"])
    external_payload = _first_payload(evidence_store, ["external_reference"])

    reason = _build_resolution_reason(
        resolution=resolution,
        previous=previous,
        local_payload=local_payload,
        external_payload=external_payload,
    )
    local_block = _render_local_knowledge(local_payload)
    external_block = _render_external_reference(external_payload)
    updated_advice = _build_updated_advice(
        previous_recommendations=previous_recommendations,
        local_payload=local_payload,
        external_payload=external_payload,
    )

    resolution_type = str(resolution.get("resolution") or "").strip()
    resolution_label = {
        "retrieve_more_local_knowledge": "补充本地知识",
        "use_tavily_external_search": "补充外部搜索",
    }.get(resolution_type, "追问补充")

    return dedent(
        f"""
        # AIOps 追问补充诊断报告

        ## 关联上一轮诊断
        - 上一轮问题：{previous_query}
        - 上一轮 Profile：`{previous_profile}`
        - 上一轮对象：`{previous_object}`
        - 上一轮诊断摘要：{previous_summary}
        - 上一轮处理建议摘要：{previous_recommendations}

        ## 本轮追问理解
        - 用户追问：{current_query}
        - 处理方式：{resolution_label}
        - 触发原因：{reason}

        ## 本轮补充参考
        {local_block}

        {external_block}

        ## 更新后的排查建议
        {updated_advice}

        ## 安全边界
        - {safety_notes}
        - 外部资料仅作补充参考，不替代本地主机实时证据。
        - 若后续需要重启、扩容、限流、清理缓存或其他高风险动作，仍应经过人工确认或审批。
        """
    ).strip()


def _first_payload(evidence_store: dict[str, Any], slots: list[str]) -> dict[str, Any]:
    for slot in slots:
        record = evidence_store.get(slot) or {}
        payload = record.get("payload") if isinstance(record, dict) else {}
        if isinstance(payload, dict):
            return payload
    return {}


def _build_compact_previous_summary(previous: dict[str, Any]) -> str:
    target_object = str(previous.get("previous_target_object") or "目标对象")
    profile_id = str(previous.get("previous_profile_id") or "")
    key_evidence = list(previous.get("previous_key_evidence") or [])
    diagnosis_summary = str(previous.get("previous_diagnosis_summary") or "").strip()

    resource_label = {
        "cpu_pressure_profile": "CPU 压力",
        "memory_pressure_profile": "内存压力",
        "disk_pressure_profile": "磁盘压力",
    }.get(profile_id, "资源压力")

    parts = [f"上一轮判断 `{target_object}` 存在{resource_label}。"]
    evidence_parts: list[str] = []
    for item in key_evidence[:3]:
        text = str(item).strip()
        if text:
            evidence_parts.append(
                text.replace("usage=", "使用率 ")
                .replace("top=", "热点 ")
                .replace("top_dir=", "目录 ")
                .replace("top_file=", "文件 ")
            )
    if evidence_parts:
        parts.append("关键证据包括：" + "；".join(evidence_parts) + "。")
    elif diagnosis_summary:
        parts.append(diagnosis_summary[:180].rstrip("。") + "。")
    return "".join(parts)


def _build_resolution_reason(
    *,
    resolution: dict[str, Any],
    previous: dict[str, Any],
    local_payload: dict[str, Any],
    external_payload: dict[str, Any],
) -> str:
    resolution_type = str(resolution.get("resolution") or "").strip()
    default_reason = str(resolution.get("reason") or "").strip()
    has_previous_runbook = bool(str(previous.get("previous_runbook_summary") or "").strip())
    external_available = bool(str(external_payload.get("content") or "").strip() or (external_payload.get("artifacts") or []))
    local_available = bool(str(local_payload.get("content") or "").strip())

    if resolution_type == "use_tavily_external_search" and has_previous_runbook:
        if external_available:
            return "上一轮已基于本地 Runbook 给出处理建议，但用户反馈仍未解决，因此补充外部公开资料寻找新的排查思路。"
        return "上一轮已基于本地 Runbook 给出处理建议，但用户反馈仍未解决，因此尝试补充外部公开资料进一步排查。"

    if resolution_type == "retrieve_more_local_knowledge" and local_available:
        return "上一轮建议需要补充更多本地 Runbook / RAG 参考，以便细化后续处置思路。"

    return default_reason or "根据上一轮诊断上下文补充后续解释或参考资料。"


def _render_local_knowledge(payload: dict[str, Any]) -> str:
    content = str(payload.get("content") or "").strip()
    if not payload:
        return "- 本轮未新增本地 Runbook / RAG 参考。"
    if payload.get("ok") is False:
        return f"- 本地 Runbook / RAG 补充失败：{payload.get('message') or 'retrieve_knowledge failed'}"
    if not content:
        return "- 本地 Runbook / RAG 未返回有效新增内容。"
    return "\n".join(
        [
            "- 新增本地 Runbook / RAG 参考：",
            f"  {content[:500]}",
        ]
    )


def _render_external_reference(payload: dict[str, Any]) -> str:
    if not payload:
        return "- 本轮未触发外部补充参考。"
    if payload.get("ok") is False:
        return f"- 外部补充参考获取失败：{payload.get('message') or 'web_search failed'}"

    blocks: list[str] = ["- 外部补充参考："]
    content = str(payload.get("content") or "").strip()
    artifacts = payload.get("artifacts") or []
    if content:
        blocks.append(f"  {content[:500]}")
    for index, artifact in enumerate(artifacts[:3], start=1):
        if not isinstance(artifact, dict):
            continue
        metadata = artifact.get("metadata") or {}
        title = str(metadata.get("title") or f"外部资料 {index}")
        url = str(metadata.get("source") or "")
        summary = str(artifact.get("page_content") or "").strip()
        blocks.append(f"  - 标题：{title}")
        if url:
            blocks.append(f"    链接：{url}")
        if summary:
            blocks.append(f"    摘要：{summary[:280]}")
    return "\n".join(blocks)


def _build_updated_advice(
    *,
    previous_recommendations: str,
    local_payload: dict[str, Any],
    external_payload: dict[str, Any],
) -> str:
    lines = [
        f"- 继续以上一轮建议为主线：{previous_recommendations}",
    ]
    if str(local_payload.get("content") or "").strip():
        lines.append("- 结合新增本地 Runbook / RAG 参考，优先验证与当前 CPU/Memory/Disk 压力最贴近的低风险排查步骤。")
    if str(external_payload.get("content") or "").strip() or (external_payload.get("artifacts") or []):
        lines.append("- 由于上一轮本地建议未解决问题，已补充外部资料进一步排查；请将外部建议作为补充思路，而不是本地现场事实。")
        lines.extend(_build_incremental_external_advice(external_payload))
    if not str(local_payload.get("content") or "").strip() and not (
        str(external_payload.get("content") or "").strip() or (external_payload.get("artifacts") or [])
    ):
        lines.append("- 本轮未拿到新的有效补充资料，建议先核对上一轮建议是否已完整执行，并补充更明确的执行结果或报错现象。")
    return "\n".join(lines)


def _build_incremental_external_advice(external_payload: dict[str, Any]) -> list[str]:
    content = " ".join(
        [
            str(external_payload.get("content") or ""),
            *[
                str(artifact.get("page_content") or "")
                for artifact in (external_payload.get("artifacts") or [])
                if isinstance(artifact, dict)
            ],
        ]
    ).lower()

    suggestions: list[str] = []
    if any(token in content for token in ("load average", "load high", "iowait", "io wait")):
        suggestions.append("- 外部补充参考：区分“CPU 百分比高”和“load 高但伴随 I/O wait”的情况，避免把等待型问题误判成纯计算饱和。")
    if any(token in content for token in ("thread", "hot thread", "jstack", "goroutine", "stack")):
        suggestions.append("- 外部补充参考：若进程级热点不足以解释问题，可进一步下钻到线程级热点，确认是否存在锁竞争、线程池拥塞或单线程忙等。")
    if any(token in content for token in ("worker", "uvicorn", "gunicorn", "nginx", "java", "python", "process")):
        suggestions.append("- 外部补充参考：结合热点进程类型，继续核对 worker 数量、并发模型和异常进程行为，判断是配置过载还是单个进程异常飙高。")
    if any(token in content for token in ("cache", "docker", "container", "cgroup", "limit")):
        suggestions.append("- 外部补充参考：检查容器/宿主机资源限制、缓存竞争或运行时参数，确认是否存在资源上限设置不合理的问题。")
    if not suggestions and content.strip():
        suggestions.append("- 外部补充参考：优先把外部资料中的排查项与上一轮本地证据逐项比对，筛出与当前现象最接近的 1-2 条新思路再继续验证。")
    return suggestions[:4]
