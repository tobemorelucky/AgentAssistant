from app.agent.aiops.skill_loader import SkillDefinition
from app.agent.aiops.skill_router import infer_intents, match_skills, score_skill


def test_infer_intents_matches_cpu_and_logs():
    intents = infer_intents("please diagnose high cpu and check error logs")
    assert "cpu_diagnosis" in intents
    assert "log_analysis" in intents


def test_score_skill_prefers_service_and_alert_matches():
    skill = SkillDefinition(
        name="CPU Runbook",
        description="diagnose cpu incidents",
        trigger={
            "services": ["checkout"],
            "alerts": ["HighCPUUsage"],
            "keywords": ["cpu"],
            "intents": ["cpu_diagnosis"],
        },
    )

    score, reasons = score_skill("checkout service has HighCPUUsage and cpu keeps rising", skill)

    assert score >= 100
    assert "service" in reasons
    assert "alert" in reasons
    assert "keyword" in reasons
    assert "intent" in reasons


def test_match_skills_limits_to_top_three(monkeypatch):
    skills = [
        SkillDefinition(name=f"skill-{index}", description="x", trigger={"keywords": [f"hit{index}"]})
        for index in range(5)
    ]
    monkeypatch.setattr("app.agent.aiops.skill_router.load_skills", lambda: skills)

    result = match_skills("hit0 hit1 hit2 hit3 hit4", limit=3)

    assert len(result) == 3
