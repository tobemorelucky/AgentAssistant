# 2026-05-20 Phase 5 Legacy Cleanup

## 本轮目标
- 清理不再需要的 legacy AIOps 主链兼容路径
- 收口 README 与架构文档
- 新增验收矩阵文档
- 固化 Phase 5 主流程回归测试

## 本轮完成

### 1. 主执行链收口为 runtime-first
- `executor.py` 仅保留两类正式执行路径：
  - Investigation Runtime task
  - 结构化工具步骤
- 旧 generic 自由工具调用主链不再作为正式入口

### 2. Verifier 移除 legacy 主分支依赖
- 删除旧 generic / legacy disk 主判定逻辑
- 运行时 profile 继续使用各自 runtime verifier
- 非 runtime 情况下，仅做最小化受控收口

### 3. 默认巡检正式语义固化
- 默认巡检入口为 `host_health_patrol_profile`
- `patrol_dispatch_profile` 不再作为公开可执行 profile
- 巡检发现异常后通过异常升级机制进入 CPU / Memory / Disk 专项诊断

### 4. 文档统一
- 重写 `README.md`
- 重写 `docs/aiops_investigation_architecture.md`
- 新增 `docs/aiops_acceptance_test_matrix.md`

### 5. 测试固化
- 删除旧 generic / patrol helper 相关历史测试
- 新增并通过 Phase 5 清理回归测试

## 当前正式架构
- 普通 RAG：只做知识问答，不读取实时主机状态
- AIOps：走实时证据驱动诊断
- 默认巡检：CPU / 内存 / 磁盘基础扫描 + 活跃告警辅助 + 异常自动升级
- 专项诊断：`cpu_pressure_profile` / `memory_pressure_profile` / `disk_pressure_profile`
- Follow-up：依赖上一轮上下文判定 explanation / local enrichment / external enrichment
- Tavily / `web_search`：仅在本地知识不足或用户反馈建议无效时受控触发

## 当前保留项
- `controlled_no_profile`
  - 作为受控结束机制保留
- `patrol_dispatch.py`
  - 作为内部 alert -> profile 映射 helper 保留
- `disk_cleanup.py` 中少量 helper
  - 仍保留，后续可继续收缩，但不再是正式主入口

## 验证
已通过：
- `python -m py_compile ...`
- `python -m pytest tests/agent/test_phase5_cleanup.py tests/agent/test_investigation_phase1.py tests/agent/test_investigation_phase3.py tests/agent/test_host_health_patrol_phase48.py tests/agent/test_rag_guard_and_followup.py tests/agent/test_followup_enrichment_phase49e.py -o addopts=''`

结果：
- `30 passed`
