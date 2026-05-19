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
    previous_summary = str(previous.get("previous_diagnosis_summary") or "未记录上一轮诊断摘要")
    previous_recommendations = str(previous.get("previous_recommendations") or "未记录上一轮处置建议")
    previous_runbook = str(previous.get("previous_runbook_summary") or "上一轮未引用本地 Runbook")
    safety_notes = str(
        previous.get("previous_action_safety_notes")
        or "本轮未执行任何重启、扩容、限流、清理或其他高风险操作。"
    )

    local_payload = _first_payload(evidence_store, ["cpu_runbook", "memory_runbook", "disk_runbook"])
    external_payload = _first_payload(evidence_store, ["external_reference"])

    local_block = _render_local_knowledge(local_payload)
    external_block = _render_external_reference(external_payload)
    updated_advice = _build_updated_advice(
        previous_recommendations=previous_recommendations,
        local_payload=local_payload,
        external_payload=external_payload,
    )

    reason = str(resolution.get("reason") or "").strip()
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
        - 触发原因：{reason or "根据上一轮诊断上下文补充后续解释或参考资料。"}

        ## 本轮补充参考
        {local_block}

        {external_block}

        ## 更新后的排查建议
        {updated_advice}

        ## 安全边界
        - {safety_notes}
        - 外部资料仅作补充参考，不替代本地主机实时证据。
        - 若后续需要重启、限流、扩容、清理缓存或其他高风险动作，仍应经过人工确认或审批。
        """
    ).strip()


def _first_payload(evidence_store: dict[str, Any], slots: list[str]) -> dict[str, Any]:
    for slot in slots:
        record = evidence_store.get(slot) or {}
        payload = record.get("payload") if isinstance(record, dict) else {}
        if isinstance(payload, dict):
            return payload
    return {}


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
    if not str(local_payload.get("content") or "").strip() and not (
        str(external_payload.get("content") or "").strip() or (external_payload.get("artifacts") or [])
    ):
        lines.append("- 本轮未拿到新的有效补充资料，建议先核对上一轮建议是否已完整执行，并补充更明确的执行结果或报错现象。")
    return "\n".join(lines)
