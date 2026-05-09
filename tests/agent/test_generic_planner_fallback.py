from app.agent.aiops.planner import _build_generic_fallback_plan
from app.agent.aiops.replanner import _build_generic_template_report
from app.agent.aiops.verifier import _generic_template_verify


def test_generic_fallback_plan_includes_local_knowledge_first():
    steps = _build_generic_fallback_plan("docker镜像冲突怎么办", ["retrieve_knowledge", "web_search"])
    assert steps
    assert "retrieve_knowledge" in steps[0]
    assert any("web_search" in step for step in steps)


def test_generic_template_report_mentions_no_dangerous_action():
    state = {
        "input": "docker镜像冲突怎么办",
        "past_steps": [
            (
                "调用 retrieve_knowledge",
                '{"content":"镜像标签冲突通常与仓库来源或部署配置不一致有关。","artifacts":[]}',
            ),
            (
                "调用 web_search",
                '{"content":"Docker 官方文档建议核对 tag 与 registry。","artifacts":[{"page_content":"Docker image tag troubleshooting","metadata":{"title":"Docker tag docs","source":"https://docs.docker.com/example","provider":"tavily"}}]}',
            ),
        ],
    }
    report = _build_generic_template_report(state)
    assert "未执行任何" in report
    assert "联网搜索补充资料" in report
    assert "Docker tag docs" in report


def test_generic_template_verifier_passes_with_minimum_sections():
    state = {
        "response": (
            "# AIOps 诊断报告\n\n"
            "## 风险提示\n"
            "- 本次诊断未执行任何镜像删除、覆盖、pull、prune、rm 或其他危险操作。\n\n"
            "## 联网搜索补充资料\n"
            "- 资料标题：Docker tag docs\n  链接：https://docs.docker.com/example\n"
        ),
        "past_steps": [
            ("调用 retrieve_knowledge", "本地知识库命中了镜像标签冲突排查手册。"),
            ("调用 web_search", "补充了 Docker 官方文档链接。"),
        ],
    }
    result = _generic_template_verify(state)
    assert result.passed is True


def test_generic_template_verifier_accepts_no_execution_wording_variant():
    state = {
        "response": (
            "# AIOps 诊断报告\n\n"
            "## 风险提示\n"
            "- 本次诊断没有执行任何镜像删除、覆盖、pull、prune、rm 或其他危险操作。\n"
        ),
        "past_steps": [
            ("调用 retrieve_knowledge", "本地知识库命中了镜像标签冲突排查手册。"),
            ("整理结论", "已整理当前证据。"),
        ],
    }
    result = _generic_template_verify(state)
    assert "missing_safety_disclaimer" not in result.risk_warnings
