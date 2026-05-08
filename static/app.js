class SuperBizAgentApp {
    constructor() {
        this.apiBaseUrl = "/api";
        this.currentMode = "quick";
        this.sessionId = this.generateSessionId();
        this.isStreaming = false;
        this.currentChatHistory = [];
        this.chatHistories = this.loadChatHistories();
        this.currentAIOpsTrace = [];
        this.currentAIOpsMessage = null;
        this.currentAIOpsContext = null;
        this.currentAIOpsState = null;
        this.currentAIOpsFeedback = null;
        this.pendingApproval = null;

        this.initializeElements();
        this.bindEvents();
        this.initMarkdown();
        this.updateUI();
        this.renderChatHistory();
        this.renderCurrentConversation();
    }

    initializeElements() {
        this.newChatBtn = document.getElementById("newChatBtn");
        this.aiOpsSidebarBtn = document.getElementById("aiOpsSidebarBtn");
        this.messageInput = document.getElementById("messageInput");
        this.sendButton = document.getElementById("sendButton");
        this.toolsBtn = document.getElementById("toolsBtn");
        this.uploadFileItem = document.getElementById("uploadFileItem");
        this.modeSelectorBtn = document.getElementById("modeSelectorBtn");
        this.modeDropdown = document.getElementById("modeDropdown");
        this.currentModeText = document.getElementById("currentModeText");
        this.fileInput = document.getElementById("fileInput");
        this.chatContainer = document.querySelector(".chat-container");
        this.chatMessages = document.getElementById("chatMessages");
        this.chatHistoryList = document.getElementById("chatHistoryList");
        this.loadingOverlay = document.getElementById("loadingOverlay");
        this.approvalModal = document.getElementById("approvalModal");
        this.approvalReason = document.getElementById("approvalReason");
        this.approvalToolName = document.getElementById("approvalToolName");
        this.approvalToolArgs = document.getElementById("approvalToolArgs");
        this.approvalApproveBtn = document.getElementById("approvalApproveBtn");
        this.approvalRejectBtn = document.getElementById("approvalRejectBtn");
    }

    bindEvents() {
        this.newChatBtn?.addEventListener("click", () => this.newChat());
        this.aiOpsSidebarBtn?.addEventListener("click", () => this.triggerAIOps());
        this.sendButton?.addEventListener("click", () => this.sendMessage());

        this.messageInput?.addEventListener("keypress", (event) => {
            if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                this.sendMessage();
            }
        });

        this.toolsBtn?.addEventListener("click", (event) => {
            event.stopPropagation();
            this.toolsBtn.closest(".tools-btn-wrapper")?.classList.toggle("active");
        });

        this.uploadFileItem?.addEventListener("click", () => {
            this.fileInput?.click();
            this.closeMenus();
        });

        this.fileInput?.addEventListener("change", (event) => this.handleFileSelect(event));

        this.modeSelectorBtn?.addEventListener("click", (event) => {
            event.stopPropagation();
            this.modeDropdown?.classList.toggle("open");
            this.modeSelectorBtn.parentElement?.classList.toggle("active");
        });

        this.modeDropdown?.querySelectorAll(".dropdown-item").forEach((item) => {
            item.addEventListener("click", (event) => {
                event.stopPropagation();
                this.currentMode = item.dataset.mode || "quick";
                this.updateUI();
                this.closeMenus();
            });
        });

        this.approvalApproveBtn?.addEventListener("click", () => this.handleApprovalDecision(true));
        this.approvalRejectBtn?.addEventListener("click", () => this.handleApprovalDecision(false));
        document.addEventListener("click", () => this.closeMenus());
    }

    initMarkdown() {
        const boot = () => {
            if (typeof marked === "undefined") {
                setTimeout(boot, 100);
                return;
            }
            marked.setOptions({
                breaks: true,
                gfm: true,
                headerIds: false,
                mangle: false,
            });
        };
        boot();
    }

    generateSessionId() {
        return `session-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    }

    closeMenus() {
        this.toolsBtn?.closest(".tools-btn-wrapper")?.classList.remove("active");
        this.modeDropdown?.classList.remove("open");
        this.modeSelectorBtn?.parentElement?.classList.remove("active");
    }

    updateUI() {
        if (this.currentModeText) {
            this.currentModeText.textContent = this.currentMode === "stream" ? "流式" : "快捷";
        }
        if (this.messageInput) this.messageInput.disabled = this.isStreaming;
        if (this.sendButton) this.sendButton.disabled = this.isStreaming;
        this.modeDropdown?.querySelectorAll(".dropdown-item").forEach((item) => {
            item.classList.toggle("active", item.dataset.mode === this.currentMode);
        });
    }

    escapeHtml(text) {
        const div = document.createElement("div");
        div.textContent = text || "";
        return div.innerHTML;
    }

    renderMarkdown(content) {
        if (!content) return "";
        if (typeof marked === "undefined") return this.escapeHtml(content);
        try {
            return marked.parse(content);
        } catch (_error) {
            return this.escapeHtml(content);
        }
    }

    highlightCodeBlocks(container) {
        if (!container || typeof hljs === "undefined") return;
        container.querySelectorAll("pre code").forEach((block) => {
            try {
                hljs.highlightElement(block);
            } catch (_error) {}
        });
    }

    loadChatHistories() {
        try {
            return JSON.parse(localStorage.getItem("chatHistories") || "[]");
        } catch (_error) {
            return [];
        }
    }

    saveChatHistories() {
        localStorage.setItem("chatHistories", JSON.stringify(this.chatHistories));
    }

    buildConversationTitle() {
        const firstUser = this.currentChatHistory.find((entry) => entry.type === "user");
        if (!firstUser) return "新对话";
        return `${firstUser.content.slice(0, 30)}${firstUser.content.length > 30 ? "..." : ""}`;
    }

    saveCurrentChat() {
        if (!this.currentChatHistory.length) return;
        const payload = {
            id: this.sessionId,
            title: this.buildConversationTitle(),
            messages: [...this.currentChatHistory],
        };
        const index = this.chatHistories.findIndex((entry) => entry.id === this.sessionId);
        if (index >= 0) this.chatHistories[index] = payload;
        else this.chatHistories.unshift(payload);
        this.chatHistories = this.chatHistories.slice(0, 50);
        this.saveChatHistories();
    }

    renderChatHistory() {
        if (!this.chatHistoryList) return;
        this.chatHistoryList.innerHTML = "";

        this.chatHistories.forEach((history) => {
            const item = document.createElement("div");
            item.className = "history-item";
            item.innerHTML = `
                <div class="history-item-content">
                    <span class="history-item-title">${this.escapeHtml(history.title)}</span>
                </div>
                <button class="history-item-delete" title="删除">
                    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <path d="M18 6L6 18M6 6L18 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                    </svg>
                </button>
            `;

            item.addEventListener("click", (event) => {
                if (!event.target.closest(".history-item-delete")) this.loadChatHistory(history);
            });

            item.querySelector(".history-item-delete")?.addEventListener("click", (event) => {
                event.stopPropagation();
                this.chatHistories = this.chatHistories.filter((entry) => entry.id !== history.id);
                this.saveChatHistories();
                this.renderChatHistory();
            });

            this.chatHistoryList.appendChild(item);
        });
    }

    setConversationState() {
        const isEmpty = (this.chatMessages?.children.length || 0) === 0;
        this.chatContainer?.classList.toggle("centered", isEmpty);
    }

    scrollToBottom() {
        if (this.chatMessages) this.chatMessages.scrollTop = this.chatMessages.scrollHeight;
    }

    showNotification(message, type = "info") {
        const notice = document.createElement("div");
        notice.textContent = message;
        notice.className = `notification notification-${type}`;
        notice.style.position = "fixed";
        notice.style.right = "20px";
        notice.style.bottom = "20px";
        notice.style.zIndex = "13000";
        notice.style.padding = "12px 16px";
        notice.style.borderRadius = "12px";
        notice.style.boxShadow = "0 10px 20px rgba(0,0,0,0.16)";
        notice.style.color = "#fff";
        notice.style.background = type === "error" ? "#d93025" : type === "success" ? "#188038" : "#1a73e8";
        document.body.appendChild(notice);

        setTimeout(() => {
            notice.remove();
        }, 2200);
    }

    createMessageElement(type, content, isLoading = false) {
        const message = document.createElement("div");
        message.className = `message ${type}`;
        const body = document.createElement("div");
        body.className = `message-content ${isLoading ? "loading-message-content" : ""}`;
        if (isLoading) {
            body.textContent = content || "处理中...";
        } else if (type === "assistant") {
            body.innerHTML = this.renderMarkdown(content);
            this.highlightCodeBlocks(body);
        } else {
            body.textContent = content;
        }
        message.appendChild(body);
        return message;
    }

    appendMessageElement(message) {
        this.chatMessages?.appendChild(message);
        this.setConversationState();
        this.scrollToBottom();
        return message;
    }

    addMessage(type, content, isLoading = false, persist = true, metadata = {}) {
        const message = this.createMessageElement(type, content, isLoading);
        this.appendMessageElement(message);
        if (persist) {
            this.currentChatHistory.push({
                type,
                content,
                timestamp: new Date().toISOString(),
                ...metadata,
            });
            this.saveCurrentChat();
            this.renderChatHistory();
        }
        return message;
    }

    addLoadingMessage(content = "处理中...") {
        return this.addMessage("assistant", content, true, false);
    }

    updateAssistantMessage(messageElement, content, { markdown = false } = {}) {
        if (!messageElement) return;
        const contentNode = messageElement.querySelector(".message-content");
        if (!contentNode) return;
        contentNode.classList.remove("loading-message-content");
        if (markdown) {
            contentNode.innerHTML = this.renderMarkdown(content);
            this.highlightCodeBlocks(contentNode);
        } else {
            contentNode.textContent = content;
        }
        this.scrollToBottom();
    }

    loadChatHistory(history) {
        this.sessionId = history.id;
        this.currentChatHistory = Array.isArray(history.messages) ? [...history.messages] : [];
        this.currentAIOpsTrace = [];
        this.currentAIOpsMessage = null;
        this.currentAIOpsContext = null;
        this.currentAIOpsState = null;
        this.currentAIOpsFeedback = null;
        this.pendingApproval = null;
        this.renderCurrentConversation();
        this.scrollToBottom();
    }

    newChat() {
        if (!this.isStreaming) this.saveCurrentChat();
        this.sessionId = this.generateSessionId();
        this.currentChatHistory = [];
        this.currentAIOpsTrace = [];
        this.currentAIOpsMessage = null;
        this.currentAIOpsContext = null;
        this.currentAIOpsState = null;
        this.currentAIOpsFeedback = null;
        this.pendingApproval = null;
        if (this.messageInput) this.messageInput.value = "";
        this.renderCurrentConversation();
        this.closeApprovalModal();
        this.renderChatHistory();
    }

    renderCurrentConversation() {
        if (!this.chatMessages) return;
        this.chatMessages.innerHTML = "";
        this.currentAIOpsMessage = null;
        this.currentAIOpsTrace = [];
        this.currentAIOpsFeedback = null;

        this.currentChatHistory.forEach((message) => {
            const element = this.addMessage(message.type, message.content, false, false, message);
            if (message.meta === "aiops-progress") {
                element.classList.add("aiops-message");
                this.currentAIOpsMessage = element;
                this.currentAIOpsTrace = Array.isArray(message.traceEntries) ? [...message.traceEntries] : [];
                this.renderTraceTimeline(element, message.traceEntries || []);
            }
            if (message.meta === "aiops-final") {
                element.classList.add("aiops-message");
                this.currentAIOpsMessage = element;
                this.currentAIOpsTrace = Array.isArray(message.traceEntries) ? [...message.traceEntries] : [];
                this.currentAIOpsFeedback = {
                    sessionId: message.sessionId || this.sessionId,
                    feedbackStatus: message.feedbackStatus || "pending",
                    generatedSkillDraft: message.generatedSkillDraft || null,
                };
                this.renderTraceTimeline(element, message.traceEntries || []);
                this.renderAIOpsFeedbackPrompt(element, this.currentAIOpsFeedback);
            }
        });

        this.setConversationState();
    }

    createAIOpsRenderState(task, mode) {
        return {
            task,
            mode,
            statusMessages: [],
            plan: [],
            steps: [],
            verifier: null,
            report: "",
            error: "",
        };
    }

    ensureAIOpsState() {
        if (!this.currentAIOpsState) {
            const context = this.currentAIOpsContext || { task: "", mode: "default" };
            this.currentAIOpsState = this.createAIOpsRenderState(context.task, context.mode);
        }
        return this.currentAIOpsState;
    }

    addAIOpsStatus(message, stage = "") {
        const state = this.ensureAIOpsState();
        const normalizedMessage = (message || "").trim();
        if (!normalizedMessage) return;
        const last = state.statusMessages[state.statusMessages.length - 1];
        if (last && last.message === normalizedMessage && last.stage === stage) return;
        state.statusMessages.push({ stage, message: normalizedMessage });
    }

    formatTraceArgs(toolArgs = {}) {
        if (!toolArgs || typeof toolArgs !== "object" || Array.isArray(toolArgs)) return "";
        const filtered = Object.fromEntries(
            Object.entries(toolArgs).filter(([key, value]) => !["type", "test", "data"].includes(key) && value !== "" && value != null),
        );
        if (!Object.keys(filtered).length) return "";
        try {
            return JSON.stringify(filtered);
        } catch (_error) {
            return "";
        }
    }

    formatStepPreview(preview) {
        if (!preview) return "";
        if (typeof preview !== "string") return String(preview);
        const text = preview.trim();
        try {
            const parsed = JSON.parse(text);
            if (Array.isArray(parsed)) return `共 ${parsed.length} 项结果`;
            if (parsed && typeof parsed === "object") {
                const filtered = Object.fromEntries(
                    Object.entries(parsed).filter(([key]) => !["type", "test", "data"].includes(key)),
                );
                if (filtered.message) return String(filtered.message);
                if (filtered.path && filtered.size_gb !== undefined) return `${filtered.path} ${filtered.size_gb}GB`;
                if (filtered.usage_percent !== undefined) {
                    return `使用率 ${filtered.usage_percent}%`;
                }
                const firstArrayKey = Object.keys(filtered).find((key) => Array.isArray(filtered[key]));
                if (firstArrayKey && filtered[firstArrayKey].length) {
                    const firstItem = filtered[firstArrayKey][0];
                    if (firstItem && typeof firstItem === "object" && firstItem.path) {
                        return `${firstArrayKey}: ${firstItem.path} ${firstItem.size_gb ?? ""}`.trim();
                    }
                }
                return JSON.stringify(filtered).slice(0, 160);
            }
        } catch (_error) {}
        return text.replace(/"type"\s*:\s*".*?"/gi, "").replace(/"test"\s*:\s*".*?"/gi, "").slice(0, 160);
    }

    buildAIOpsMarkdown({ final = false } = {}) {
        const state = this.ensureAIOpsState();
        const lines = [];
        lines.push(`# AIOps ${final ? "诊断结果" : "诊断进行中"}`);
        lines.push("");
        lines.push(`- 模式：${state.mode === "custom" ? "自定义诊断" : "默认巡检"}`);
        lines.push(`- 任务：${state.task || "未指定任务"}`);

        if (state.statusMessages.length) {
            lines.push("");
            lines.push("## 当前状态");
            state.statusMessages.slice(-8).forEach((item) => lines.push(`- ${item.message}`));
        }

        if (Array.isArray(state.plan) && state.plan.length) {
            lines.push("");
            lines.push("## 诊断计划");
            state.plan.forEach((step, index) => lines.push(`${index + 1}. ${step}`));
        }

        if (state.steps.length) {
            lines.push("");
            lines.push("## 执行步骤");
            state.steps.forEach((step, index) => {
                lines.push(`### 步骤 ${index + 1}`);
                lines.push(`- 任务：${step.current_step || "未知步骤"}`);
                lines.push(`- 摘要：${this.formatStepPreview(step.result_preview) || "无"}`);
                if (typeof step.remaining_steps === "number") {
                    lines.push(`- 剩余步骤：${step.remaining_steps}`);
                }
                lines.push("");
            });
        }

        if (state.verifier) {
            lines.push("");
            lines.push("## Verifier");
            lines.push(`- 结果：${state.verifier.passed ? "通过" : "未通过"}`);
            (state.verifier.findings || []).forEach((item) => lines.push(`- 发现：${item}`));
            (state.verifier.suggested_next_steps || []).forEach((item) => lines.push(`- 建议补充：${item}`));
        }

        if (state.report) {
            lines.push("");
            lines.push("## 最终报告");
            lines.push("");
            lines.push(state.report);
        }

        if (state.error) {
            lines.push("");
            lines.push("## 错误");
            lines.push(state.error);
        }

        return lines.join("\n").trim();
    }

    getRenderableTraceEntries(traceEntries = []) {
        return (Array.isArray(traceEntries) ? traceEntries : []).filter((trace) => trace && typeof trace === "object" && trace.node !== "memory");
    }

    renderTraceTimeline(messageElement, traceEntries = []) {
        if (!messageElement) return;
        const renderableEntries = this.getRenderableTraceEntries(traceEntries);
        let launcher = messageElement.querySelector(".trace-launcher");
        let panel = messageElement.querySelector(".trace-panel");

        if (!renderableEntries.length) {
            launcher?.remove();
            panel?.remove();
            return;
        }

        if (!launcher) {
            launcher = document.createElement("button");
            launcher.type = "button";
            launcher.className = "trace-launcher";
            messageElement.appendChild(launcher);
        }

        if (!panel) {
            panel = document.createElement("div");
            panel.className = "trace-panel";
            panel.innerHTML = `<div class="trace-list"></div>`;
            messageElement.appendChild(panel);
        }

        const updateLauncherText = () => {
            launcher.textContent = panel.classList.contains("expanded")
                ? `收起 Agent Trace (${renderableEntries.length})`
                : `查看 Agent Trace (${renderableEntries.length})`;
        };
        updateLauncherText();
        launcher.onclick = () => {
            panel.classList.toggle("expanded");
            updateLauncherText();
        };

        const list = panel.querySelector(".trace-list");
        list.innerHTML = "";

        renderableEntries.forEach((trace) => {
            const item = document.createElement("div");
            item.className = `trace-item status-${trace.status || "success"}`;
            const meta = [trace.node, trace.tool_name, trace.duration_ms ? `${trace.duration_ms}ms` : ""].filter(Boolean).join(" | ");
            const argsSummary = this.formatTraceArgs(trace.tool_args);
            item.innerHTML = `
                <div class="trace-item-title">${this.escapeHtml(trace.title || "Trace event")}</div>
                <div class="trace-item-meta">${this.escapeHtml(meta)}</div>
                <div class="trace-item-meta">${this.escapeHtml(trace.status || "")}</div>
                ${argsSummary ? `<div class="trace-item-summary">${this.escapeHtml(argsSummary)}</div>` : ""}
                <div class="trace-item-summary">${this.escapeHtml(this.formatStepPreview(trace.result_summary || ""))}</div>
            `;
            list.appendChild(item);
        });

        const feedback = messageElement.querySelector(".aiops-feedback");
        if (feedback) {
            messageElement.appendChild(feedback);
        }
    }

    upsertAIOpsProgressMessage(content, traceEntries = []) {
        const payload = {
            type: "assistant",
            content,
            meta: "aiops-progress",
            sessionId: this.sessionId,
            traceEntries: [...traceEntries],
            timestamp: new Date().toISOString(),
        };
        const existingIndex = this.currentChatHistory.findIndex(
            (entry) => entry.type === "assistant" && entry.meta === "aiops-progress" && entry.sessionId === this.sessionId,
        );
        if (existingIndex >= 0) this.currentChatHistory[existingIndex] = payload;
        else this.currentChatHistory.push(payload);
        this.saveCurrentChat();
        this.renderChatHistory();
    }

    updateAIOpsMessage(messageElement, response, traceEntries = [], persist = true, extraMeta = {}) {
        const target = messageElement || this.addMessage("assistant", response, false, false);
        target.classList.add("aiops-message");
        this.currentAIOpsMessage = target;
        this.updateAssistantMessage(target, response, { markdown: true });
        this.renderTraceTimeline(target, traceEntries);

        if (persist) {
            this.currentChatHistory = this.currentChatHistory.filter(
                (entry) => !(entry.type === "assistant" && entry.meta === "aiops-progress" && entry.sessionId === this.sessionId),
            );
            const payload = {
                type: "assistant",
                content: response,
                meta: "aiops-final",
                sessionId: this.sessionId,
                traceEntries: [...traceEntries],
                timestamp: new Date().toISOString(),
                ...extraMeta,
            };
            const existingIndex = this.currentChatHistory.findIndex(
                (entry) => entry.type === "assistant" && entry.meta === "aiops-final" && entry.sessionId === this.sessionId,
            );
            if (existingIndex >= 0) this.currentChatHistory[existingIndex] = payload;
            else this.currentChatHistory.push(payload);
            this.saveCurrentChat();
            this.renderChatHistory();
        }
        return target;
    }

    updateAIOpsFinalHistoryMeta(extraMeta = {}) {
        const existingIndex = this.currentChatHistory.findIndex(
            (entry) => entry.type === "assistant" && entry.meta === "aiops-final" && entry.sessionId === this.sessionId,
        );
        if (existingIndex < 0) return;
        this.currentChatHistory[existingIndex] = {
            ...this.currentChatHistory[existingIndex],
            ...extraMeta,
        };
        this.saveCurrentChat();
        this.renderChatHistory();
    }

    renderAIOpsProgress({ final = false, persistFinal = false } = {}) {
        const content = this.buildAIOpsMarkdown({ final });
        if (final || persistFinal) {
            this.updateAIOpsMessage(this.currentAIOpsMessage, content, this.currentAIOpsTrace, persistFinal);
        } else {
            if (!this.currentAIOpsMessage) {
                this.currentAIOpsMessage = this.addLoadingMessage(content);
                this.currentAIOpsMessage.classList.add("aiops-message");
            }
            this.updateAssistantMessage(this.currentAIOpsMessage, content, { markdown: true });
            this.renderTraceTimeline(this.currentAIOpsMessage, this.currentAIOpsTrace);
            this.upsertAIOpsProgressMessage(content, this.currentAIOpsTrace);
        }
    }

    renderAIOpsFeedbackPrompt(messageElement, feedback = {}) {
        if (!messageElement) return;
        const sessionId = feedback.sessionId || this.sessionId;
        const feedbackStatus = feedback.feedbackStatus || "pending";
        const generatedSkillDraft = feedback.generatedSkillDraft || null;
        this.currentAIOpsFeedback = { sessionId, feedbackStatus, generatedSkillDraft };

        let prompt = messageElement.querySelector(".aiops-feedback");
        if (!prompt) {
            prompt = document.createElement("div");
            prompt.className = "aiops-feedback";
            messageElement.appendChild(prompt);
        }
        messageElement.appendChild(prompt);

        if (feedbackStatus === "helpful") {
            prompt.innerHTML = `
                <div class="aiops-feedback-copy">已记录为有帮助。</div>
                ${generatedSkillDraft ? `<div class="aiops-feedback-note">已生成 Skill 草稿：${this.escapeHtml(generatedSkillDraft)}</div>` : ""}
            `;
            return;
        }

        if (feedbackStatus === "not_helpful") {
            prompt.innerHTML = `<div class="aiops-feedback-copy">已记录为未帮助到您，本次不会生成 Skill 草稿。</div>`;
            return;
        }

        prompt.innerHTML = `
            <div class="aiops-feedback-copy">请问是否帮助到您？</div>
            <div class="aiops-feedback-actions">
                <button type="button" class="aiops-feedback-btn" data-feedback="yes">是</button>
                <button type="button" class="aiops-feedback-btn secondary" data-feedback="no">否</button>
            </div>
        `;
        prompt.querySelector('[data-feedback="yes"]')?.addEventListener("click", () => this.submitAIOpsFeedback(sessionId, true, prompt));
        prompt.querySelector('[data-feedback="no"]')?.addEventListener("click", () => this.submitAIOpsFeedback(sessionId, false, prompt));
    }

    async submitAIOpsFeedback(sessionId, helpful, promptElement) {
        const buttons = Array.from(promptElement?.querySelectorAll("button") || []);
        buttons.forEach((button) => {
            button.disabled = true;
        });

        try {
            const response = await fetch("/api/agent/session-feedback", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    session_id: sessionId,
                    helpful,
                    operator: "frontend-user",
                    comment: helpful ? "helpful from web ui" : "not helpful from web ui",
                }),
            });
            const data = await response.json();
            if (!response.ok || data.code !== 200) {
                throw new Error(data.detail || data.message || `HTTP ${response.status}`);
            }

            const feedbackStatus = helpful ? "helpful" : "not_helpful";
            const generatedSkillDraft = data.data?.generated_skill_draft || null;
            this.updateAIOpsFinalHistoryMeta({ feedbackStatus, generatedSkillDraft });
            this.renderAIOpsFeedbackPrompt(this.currentAIOpsMessage, {
                sessionId,
                feedbackStatus,
                generatedSkillDraft,
            });
            this.showNotification(helpful ? "已记录反馈" : "已记录为未帮助", "success");
        } catch (error) {
            buttons.forEach((button) => {
                button.disabled = false;
            });
            this.showNotification(`提交反馈失败: ${error.message}`, "error");
        }
    }

    extractSSEBlocks(buffer) {
        const matches = buffer.split(/\r?\n\r?\n/);
        return {
            blocks: matches.slice(0, -1),
            rest: matches[matches.length - 1] || "",
        };
    }

    parseSSEPayload(block) {
        const dataLines = block
            .split(/\r?\n/)
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trimStart());
        if (!dataLines.length) return null;
        const raw = dataLines.join("\n").trim();
        if (!raw) return null;
        const payload = JSON.parse(raw);
        console.log("[AIOps SSE]", payload);
        return payload;
    }

    async processAIOpsPayload(payload) {
        if (!payload) return false;

        if (payload.type === "trace" && payload.trace) {
            this.currentAIOpsTrace.push(payload.trace);
            this.renderAIOpsProgress();
            return false;
        }

        if (payload.type === "status") {
            this.addAIOpsStatus(payload.message || "处理中...", payload.stage || "status");
            this.renderAIOpsProgress();
            return false;
        }

        if (payload.type === "plan") {
            this.ensureAIOpsState().plan = Array.isArray(payload.plan) ? payload.plan : [];
            this.renderAIOpsProgress();
            return false;
        }

        if (payload.type === "step_complete") {
            this.ensureAIOpsState().steps.push({
                current_step: payload.current_step || "执行步骤",
                result_preview: payload.result_preview || "",
                remaining_steps: payload.remaining_steps,
            });
            this.renderAIOpsProgress();
            return false;
        }

        if (payload.type === "verifier_result") {
            this.ensureAIOpsState().verifier = {
                passed: !!payload.passed,
                findings: Array.isArray(payload.findings) ? payload.findings : [],
                suggested_next_steps: Array.isArray(payload.suggested_next_steps) ? payload.suggested_next_steps : [],
            };
            this.addAIOpsStatus(payload.passed ? "Verifier 已通过" : "Verifier 未通过", "verifier");
            this.renderAIOpsProgress();
            return false;
        }

        if (payload.type === "approval_required") {
            this.addAIOpsStatus(`工具 ${payload.tool_name || "-"} 需要人工审批`, "approval_required");
            this.renderAIOpsProgress();
            this.openApprovalModal(payload);
            return true;
        }

        if (payload.type === "report") {
            this.ensureAIOpsState().report = payload.report || this.ensureAIOpsState().report;
            this.renderAIOpsProgress();
            return false;
        }

        if (payload.type === "complete") {
            const finalReport =
                payload.diagnosis?.report ||
                payload.response ||
                this.ensureAIOpsState().report ||
                "诊断已完成。";
            this.ensureAIOpsState().report = finalReport;
            this.addAIOpsStatus("AIOps 诊断完成", "complete");
            this.updateAIOpsMessage(
                this.currentAIOpsMessage,
                this.buildAIOpsMarkdown({ final: true }),
                this.currentAIOpsTrace,
                true,
                {
                    feedbackStatus: "pending",
                    generatedSkillDraft: null,
                },
            );
            this.renderAIOpsFeedbackPrompt(this.currentAIOpsMessage, {
                sessionId: this.sessionId,
                feedbackStatus: "pending",
                generatedSkillDraft: null,
            });
            this.showNotification("AIOps 诊断完成", "success");
            return true;
        }

        if (payload.type === "error") {
            throw new Error(payload.message || "AIOps 诊断失败");
        }

        return false;
    }

    async sendAIOpsRequest({ resume = false, task = "", mode = "default" } = {}) {
        if (this.isStreaming) {
            this.showNotification("当前已有诊断在执行，请稍候", "warning");
            return;
        }

        this.isStreaming = true;
        if (!resume) {
            this.currentAIOpsContext = { task, mode };
            this.currentAIOpsState = this.createAIOpsRenderState(task, mode);
            this.currentAIOpsTrace = [];
            this.currentAIOpsMessage = this.addLoadingMessage("AIOps Agent 正在启动诊断...");
            this.currentAIOpsMessage.classList.add("aiops-message");
            this.addAIOpsStatus("AIOps Agent 正在启动诊断...", "workflow_started");
            this.renderAIOpsProgress();
        } else {
            this.addAIOpsStatus("审批已处理，继续执行诊断...", "approval_resume");
            this.renderAIOpsProgress();
        }
        this.updateUI();

        try {
            const response = await fetch("/api/aiops", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    session_id: this.sessionId,
                    task: (this.currentAIOpsContext?.task || task || "").trim(),
                    mode: this.currentAIOpsContext?.mode || mode || "default",
                }),
            });

            if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);

            const reader = response.body.getReader();
            const decoder = new TextDecoder("utf-8");
            let buffer = "";

            try {
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) {
                        const { blocks, rest } = this.extractSSEBlocks(buffer);
                        buffer = rest;
                        for (const block of blocks) {
                            const payload = this.parseSSEPayload(block);
                            if (!payload) continue;
                            const shouldStop = await this.processAIOpsPayload(payload);
                            if (shouldStop) return;
                        }
                        if (buffer.trim()) {
                            const payload = this.parseSSEPayload(buffer);
                            if (payload) {
                                const shouldStop = await this.processAIOpsPayload(payload);
                                if (shouldStop) return;
                            }
                        }
                        break;
                    }

                    buffer += decoder.decode(value, { stream: true });
                    const { blocks, rest } = this.extractSSEBlocks(buffer);
                    buffer = rest;

                    for (const block of blocks) {
                        const payload = this.parseSSEPayload(block);
                        if (!payload) continue;
                        const shouldStop = await this.processAIOpsPayload(payload);
                        if (shouldStop) return;
                    }
                }
            } finally {
                reader.releaseLock();
            }
        } catch (error) {
            this.ensureAIOpsState().error = error.message || "未知错误";
            this.addAIOpsStatus(`诊断失败：${error.message}`, "error");
            this.renderAIOpsProgress();
            this.showNotification(`AIOps 执行失败: ${error.message}`, "error");
        } finally {
            this.isStreaming = false;
            this.updateUI();
        }
    }

    async triggerAIOps() {
        if (this.isStreaming) {
            this.showNotification("当前已有诊断在执行，请稍候", "warning");
            return;
        }
        const userInput = this.messageInput?.value?.trim() || "";
        const mode = userInput ? "custom" : "default";
        const task =
            userInput ||
            "请检查当前系统是否存在活跃告警。如果存在告警，请选择最高严重级别告警，结合监控指标、日志、历史工单和知识库 runbook 进行根因分析，并保留完整 Agent Trace。";
        const userMessage =
            userInput ||
            "请开始一次 AIOps 巡检，并保留完整 Agent Trace。";

        this.addMessage("user", userMessage);
        if (this.messageInput) this.messageInput.value = "";
        await this.sendAIOpsRequest({ resume: false, task, mode });
    }

    openApprovalModal(payload) {
        this.pendingApproval = payload;
        if (!this.approvalModal) return;
        this.approvalReason.textContent = payload.reason || "危险工具需要审批。";
        this.approvalToolName.textContent = payload.tool_name || "-";
        this.approvalToolArgs.textContent = payload.tool_args_summary || "-";
        this.approvalModal.classList.remove("hidden");
    }

    closeApprovalModal() {
        this.approvalModal?.classList.add("hidden");
    }

    async handleApprovalDecision(approved) {
        if (!this.pendingApproval) return;
        const endpoint = approved ? "/api/agent/approve" : "/api/agent/reject";
        const payload = {
            session_id: this.sessionId,
            action_id: this.pendingApproval.action_id,
            operator: "frontend-user",
            comment: approved ? "approved from web ui" : "rejected from web ui",
        };

        try {
            const response = await fetch(endpoint, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            const data = await response.json();
            if (!response.ok || data.code !== 200) {
                throw new Error(data.detail || data.message || `HTTP ${response.status}`);
            }
            this.closeApprovalModal();
            this.pendingApproval = null;
            this.showNotification(approved ? "已批准，继续执行" : "已拒绝本次工具调用", "success");
            await this.sendAIOpsRequest({ resume: true });
        } catch (error) {
            this.showNotification(`审批处理失败: ${error.message}`, "error");
        }
    }

    async sendQuickMessage(message) {
        const loading = this.addLoadingMessage("正在生成回复...");
        try {
            const response = await fetch(`${this.apiBaseUrl}/chat`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ Id: this.sessionId, Question: message }),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            loading.remove();
            this.addMessage("assistant", data.data?.answer || data.data?.errorMessage || "没有返回结果");
        } catch (error) {
            loading.remove();
            throw error;
        }
    }

    async sendStreamMessage(message) {
        const assistant = this.addLoadingMessage("正在生成回复...");
        let fullResponse = "";

        const response = await fetch(`${this.apiBaseUrl}/chat_stream`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ Id: this.sessionId, Question: message }),
        });
        if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);

        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");

        try {
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                const chunk = decoder.decode(value, { stream: true });
                const lines = chunk.split(/\r?\n/).filter(Boolean);
                for (const line of lines) {
                    if (!line.startsWith("data:")) continue;
                    const raw = line.slice(5).trim();
                    if (!raw) continue;
                    const payload = JSON.parse(raw);
                    if (payload.type === "content") {
                        fullResponse += payload.content || "";
                        this.updateAssistantMessage(assistant, fullResponse, { markdown: false });
                    } else if (payload.type === "done") {
                        assistant.remove();
                        this.addMessage("assistant", fullResponse || "没有返回结果");
                    } else if (payload.type === "error") {
                        throw new Error(payload.message || "流式对话失败");
                    }
                }
            }
        } finally {
            reader.releaseLock();
        }
    }

    async sendMessage() {
        if (this.isStreaming) return;
        const message = this.messageInput?.value?.trim() || "";
        if (!message) return;

        this.addMessage("user", message);
        if (this.messageInput) this.messageInput.value = "";
        this.isStreaming = true;
        this.updateUI();

        try {
            if (this.currentMode === "stream") {
                await this.sendStreamMessage(message);
            } else {
                await this.sendQuickMessage(message);
            }
        } catch (error) {
            this.showNotification(`发送失败: ${error.message}`, "error");
        } finally {
            this.isStreaming = false;
            this.updateUI();
        }
    }

    showLoadingOverlay(visible, title = "", subtitle = "") {
        if (!this.loadingOverlay) return;
        this.loadingOverlay.style.display = visible ? "flex" : "none";
        const titleNode = this.loadingOverlay.querySelector(".loading-text");
        const subtitleNode = this.loadingOverlay.querySelector(".loading-subtext");
        if (titleNode && title) titleNode.textContent = title;
        if (subtitleNode && subtitle) subtitleNode.textContent = subtitle;
    }

    showUploadOverlay(visible) {
        this.showLoadingOverlay(
            visible,
            visible ? "正在上传文件..." : "",
            visible ? "请稍候" : "",
        );
    }

    async handleFileSelect(event) {
        const file = event.target?.files?.[0];
        if (!file) return;
        try {
            this.showUploadOverlay(true);
            const formData = new FormData();
            formData.append("file", file);
            const response = await fetch("/api/upload", { method: "POST", body: formData });
            const data = await response.json();
            if (!response.ok || data.code !== 200) {
                throw new Error(data.detail || data.message || `HTTP ${response.status}`);
            }
            const indexed = data.data?.indexed !== false;
            const message = indexed
                ? `文件 \`${file.name}\` 已上传并完成索引。`
                : `文件 \`${file.name}\` 上传成功，但索引失败：${data.data?.index_error || "未知错误"}`;
            this.addMessage("assistant", message);
            this.showNotification(indexed ? "文件上传成功" : "文件上传成功，但索引失败", indexed ? "success" : "warning");
        } catch (error) {
            this.showNotification(`上传失败: ${error.message}`, "error");
        } finally {
            if (this.fileInput) this.fileInput.value = "";
            this.showUploadOverlay(false);
        }
    }
}

document.addEventListener("DOMContentLoaded", () => {
    new SuperBizAgentApp();
});
