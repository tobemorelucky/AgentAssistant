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
        this.pendingApproval = null;
        this.skillDrafts = [];

        this.initializeElements();
        this.bindEvents();
        this.initMarkdown();
        this.updateUI();
        this.renderChatHistory();
        this.renderCurrentConversation();
        this.loadSkillDrafts();
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
        this.skillDraftList = document.getElementById("skillDraftList");
        this.loadingOverlay = document.getElementById("loadingOverlay");
        this.approvalModal = document.getElementById("approvalModal");
        this.approvalReason = document.getElementById("approvalReason");
        this.approvalToolName = document.getElementById("approvalToolName");
        this.approvalToolArgs = document.getElementById("approvalToolArgs");
        this.approvalApproveBtn = document.getElementById("approvalApproveBtn");
        this.approvalRejectBtn = document.getElementById("approvalRejectBtn");
        this.skillDraftModal = document.getElementById("skillDraftModal");
        this.skillDraftModalTitle = document.getElementById("skillDraftModalTitle");
        this.skillDraftModalContent = document.getElementById("skillDraftModalContent");
        this.skillDraftModalClose = document.getElementById("skillDraftModalClose");
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
        this.skillDraftModalClose?.addEventListener("click", () => this.closeSkillDraftModal());
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

    updateUI() {
        if (this.currentModeText) {
            this.currentModeText.textContent = this.currentMode === "stream" ? "流式" : "快捷";
        }
        if (this.sendButton) this.sendButton.disabled = this.isStreaming;
        if (this.messageInput) this.messageInput.disabled = this.isStreaming;
        this.modeDropdown?.querySelectorAll(".dropdown-item").forEach((item) => {
            item.classList.toggle("active", item.dataset.mode === this.currentMode);
        });
    }

    closeMenus() {
        this.toolsBtn?.closest(".tools-btn-wrapper")?.classList.remove("active");
        this.modeDropdown?.classList.remove("open");
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
        if (this.currentChatHistory.length === 0) return;
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
        notice.style.animation = "slideIn 0.2s ease";
        document.body.appendChild(notice);

        setTimeout(() => {
            notice.style.animation = "slideOut 0.2s ease forwards";
            setTimeout(() => notice.remove(), 220);
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

    buildAIOpsMarkdown({ final = false } = {}) {
        const state = this.ensureAIOpsState();
        const lines = [];
        lines.push(`# AIOps ${final ? "诊断结果" : "诊断进行中"}`);
        lines.push("");
        lines.push(`- 模式：${state.mode === "custom" ? "自定义诊断" : "默认巡检"}`);
        lines.push(`- 任务：${state.task || "未提供任务"}`);

        if (state.statusMessages.length) {
            lines.push("");
            lines.push("## 状态更新");
            state.statusMessages.slice(-8).forEach((item) => {
                lines.push(`- ${item.message}`);
            });
        }

        if (Array.isArray(state.plan) && state.plan.length) {
            lines.push("");
            lines.push("## 诊断计划");
            state.plan.forEach((step, index) => {
                lines.push(`${index + 1}. ${step}`);
            });
        }

        if (state.steps.length) {
            lines.push("");
            lines.push("## 执行步骤");
            state.steps.forEach((step, index) => {
                lines.push(`### 步骤 ${index + 1}`);
                lines.push(`- 任务：${step.current_step || "未知步骤"}`);
                lines.push(`- 摘要：${step.result_preview || "无"}`);
                if (typeof step.remaining_steps === "number") {
                    lines.push(`- 剩余步骤：${step.remaining_steps}`);
                }
                lines.push("");
            });
        }

        if (state.verifier) {
            lines.push("");
            lines.push("## Verifier 检查");
            lines.push(`- 结果：${state.verifier.passed ? "通过" : "未通过"}`);
            if (Array.isArray(state.verifier.findings) && state.verifier.findings.length) {
                state.verifier.findings.forEach((item) => lines.push(`- 发现：${item}`));
            }
            if (Array.isArray(state.verifier.suggested_next_steps) && state.verifier.suggested_next_steps.length) {
                state.verifier.suggested_next_steps.forEach((item) => lines.push(`- 建议补充：${item}`));
            }
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

    extractSSEBlocks(buffer) {
        const normalized = buffer.replace(/\r\n/g, "\n");
        const chunks = normalized.split("\n\n");
        return {
            blocks: chunks.slice(0, -1),
            rest: chunks[chunks.length - 1] || "",
        };
    }

    parseSSEPayload(block) {
        const dataLines = block
            .split("\n")
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trimStart());
        if (!dataLines.length) return null;

        const raw = dataLines.join("\n").trim();
        if (!raw) return null;

        const payload = JSON.parse(raw);
        console.log("[AIOps SSE]", payload);
        return payload;
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

    renderCurrentConversation() {
        if (!this.chatMessages) return;
        this.chatMessages.innerHTML = "";
        this.currentAIOpsMessage = null;
        this.currentAIOpsTrace = [];

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
            }
        });

        this.setConversationState();
    }

    loadChatHistory(history) {
        this.sessionId = history.id;
        this.currentChatHistory = Array.isArray(history.messages) ? [...history.messages] : [];
        this.currentAIOpsTrace = [];
        this.currentAIOpsMessage = null;
        this.currentAIOpsContext = null;
        this.currentAIOpsState = null;
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
        this.pendingApproval = null;
        if (this.messageInput) this.messageInput.value = "";
        this.renderCurrentConversation();
        this.closeApprovalModal();
        this.closeSkillDraftModal();
        this.renderChatHistory();
    }

    renderTraceTimeline(messageElement, traceEntries = []) {
        if (!messageElement) return;

        let panel = messageElement.querySelector(".trace-panel");
        if (!traceEntries.length) {
            if (panel) panel.remove();
            return;
        }

        if (!panel) {
            panel = document.createElement("div");
            panel.className = "trace-panel";
            panel.innerHTML = `
                <button class="trace-toggle" type="button">
                    <span>Agent Trace</span>
                    <span class="trace-count">0</span>
                </button>
                <div class="trace-list"></div>
            `;
            messageElement.appendChild(panel);
            const toggle = panel.querySelector(".trace-toggle");
            const list = panel.querySelector(".trace-list");
            toggle?.addEventListener("click", () => list?.classList.toggle("expanded"));
        }

        panel.querySelector(".trace-count").textContent = `${traceEntries.length}`;
        const list = panel.querySelector(".trace-list");
        list.innerHTML = "";

        traceEntries.forEach((trace) => {
            const item = document.createElement("div");
            item.className = `trace-item status-${trace.status || "success"}`;
            const meta = [trace.node, trace.tool_name, trace.duration_ms ? `${trace.duration_ms}ms` : ""]
                .filter(Boolean)
                .join(" | ");
            const argsSummary = trace.tool_args && Object.keys(trace.tool_args).length
                ? JSON.stringify(trace.tool_args)
                : "";
            item.innerHTML = `
                <div class="trace-item-title">${this.escapeHtml(trace.title || "Trace event")}</div>
                <div class="trace-item-meta">${this.escapeHtml(meta)}</div>
                <div class="trace-item-meta">${this.escapeHtml(trace.status || "")}</div>
                ${argsSummary ? `<div class="trace-item-summary">${this.escapeHtml(argsSummary)}</div>` : ""}
                <div class="trace-item-summary">${this.escapeHtml(trace.result_summary || "")}</div>
            `;
            list.appendChild(item);
        });
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

    updateAIOpsMessage(messageElement, response, traceEntries = [], persist = true) {
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
                timestamp: new Date().toISOString(),
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
            this.addMessage("assistant", data.data?.answer || data.data?.errorMessage || "暂未返回结果");
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
        let buffer = "";

        try {
            while (true) {
                const { done, value } = await reader.read();
                if (done) {
                    this.updateAssistantMessage(assistant, fullResponse, { markdown: true });
                    this.currentChatHistory.push({
                        type: "assistant",
                        content: fullResponse,
                        timestamp: new Date().toISOString(),
                    });
                    this.saveCurrentChat();
                    this.renderChatHistory();
                    return;
                }

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split("\n");
                buffer = lines.pop() || "";

                for (const line of lines) {
                    if (!line.startsWith("data:")) continue;
                    const raw = line.slice(5).trim();
                    try {
                        const payload = JSON.parse(raw);
                        if (payload.type === "content") {
                            fullResponse += payload.data || "";
                        } else if (payload.type === "done") {
                            this.updateAssistantMessage(assistant, fullResponse, { markdown: true });
                            this.currentChatHistory.push({
                                type: "assistant",
                                content: fullResponse,
                                timestamp: new Date().toISOString(),
                            });
                            this.saveCurrentChat();
                            this.renderChatHistory();
                            return;
                        } else if (payload.type === "error") {
                            throw new Error(payload.data || "流式对话失败");
                        }
                    } catch (_error) {
                        fullResponse += raw;
                    }
                    this.updateAssistantMessage(assistant, fullResponse, { markdown: true });
                }
            }
        } finally {
            reader.releaseLock();
        }
    }

    async sendMessage() {
        const message = this.messageInput?.value?.trim();
        if (!message) {
            return;
        }
        if (this.isStreaming) {
            this.showNotification("当前任务正在执行中", "warning");
            return;
        }

        this.addMessage("user", message);
        this.messageInput.value = "";
        this.isStreaming = true;
        this.updateUI();

        try {
            if (this.currentMode === "stream") await this.sendStreamMessage(message);
            else await this.sendQuickMessage(message);
        } catch (error) {
            this.showNotification(`发送失败: ${error.message}`, "error");
        } finally {
            this.isStreaming = false;
            this.updateUI();
            this.saveCurrentChat();
            this.renderChatHistory();
        }
    }

    validateFileType(file) {
        if (!file) return { valid: false, message: "未选择文件" };
        const extension = file.name.split(".").pop()?.toLowerCase() || "";
        const allowed = ["txt", "md", "markdown"];
        if (!allowed.includes(extension)) {
            return { valid: false, message: `仅支持 ${allowed.join(", ")} 文件` };
        }
        if (file.size > 10 * 1024 * 1024) {
            return { valid: false, message: "文件大小不能超过 10MB" };
        }
        return { valid: true };
    }

    async handleFileSelect(event) {
        const file = event?.target?.files?.[0];
        if (!file) return;
        const validation = this.validateFileType(file);
        if (!validation.valid) {
            this.showNotification(validation.message, "warning");
            event.target.value = "";
            return;
        }
        await this.uploadFile(file);
        event.target.value = "";
    }

    async uploadFile(file) {
        this.showUploadOverlay(true);
        try {
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
                : `文件 \`${file.name}\` 已上传，但索引失败：${data.data?.index_error || "未知原因"}`;
            this.addMessage("assistant", message);
            this.showNotification(indexed ? "文件上传成功" : "文件已上传，索引待处理", indexed ? "success" : "warning");
        } catch (error) {
            this.showNotification(`上传失败: ${error.message}`, "error");
        } finally {
            this.showUploadOverlay(false);
        }
    }

    async loadSkillDrafts() {
        if (!this.skillDraftList) return;
        try {
            const response = await fetch("/api/agent/skill-drafts");
            const data = await response.json();
            this.skillDrafts = Array.isArray(data.data) ? data.data : [];
        } catch (_error) {
            this.skillDrafts = [];
        }
        this.renderSkillDrafts();
    }

    renderSkillDrafts() {
        if (!this.skillDraftList) return;
        this.skillDraftList.innerHTML = "";

        if (this.skillDrafts.length === 0) {
            const empty = document.createElement("div");
            empty.className = "history-item-title";
            empty.textContent = "暂无 Skill 草稿";
            this.skillDraftList.appendChild(empty);
            return;
        }

        this.skillDrafts.forEach((draft) => {
            const item = document.createElement("div");
            item.className = "skill-draft-item";
            item.innerHTML = `
                <span class="skill-draft-item-title">${this.escapeHtml(draft.name)}</span>
                <button class="skill-draft-action" data-action="view">查看</button>
                <button class="skill-draft-action" data-action="enable">启用</button>
                <button class="skill-draft-action" data-action="delete">删除</button>
            `;
            item.querySelector('[data-action="view"]')?.addEventListener("click", () => this.openSkillDraftModal(draft));
            item.querySelector('[data-action="enable"]')?.addEventListener("click", () => this.enableSkillDraft(draft.name));
            item.querySelector('[data-action="delete"]')?.addEventListener("click", () => this.deleteSkillDraft(draft.name));
            this.skillDraftList.appendChild(item);
        });
    }

    openSkillDraftModal(draft) {
        if (!this.skillDraftModal) return;
        this.skillDraftModalTitle.textContent = draft.name;
        this.skillDraftModalContent.textContent = draft.content || "";
        this.skillDraftModal.classList.remove("hidden");
    }

    closeSkillDraftModal() {
        this.skillDraftModal?.classList.add("hidden");
    }

    async enableSkillDraft(draftName) {
        try {
            const response = await fetch(`/api/agent/skill-drafts/${encodeURIComponent(draftName)}/enable`, {
                method: "POST",
            });
            const data = await response.json();
            if (!response.ok || data.code !== 200) {
                throw new Error(data.detail || data.message || `HTTP ${response.status}`);
            }
            this.showNotification(`已启用 Skill 草稿: ${draftName}`, "success");
            await this.loadSkillDrafts();
        } catch (error) {
            this.showNotification(`启用失败: ${error.message}`, "error");
        }
    }

    async deleteSkillDraft(draftName) {
        try {
            const response = await fetch(`/api/agent/skill-drafts/${encodeURIComponent(draftName)}`, {
                method: "DELETE",
            });
            const data = await response.json();
            if (!response.ok || data.code !== 200) {
                throw new Error(data.detail || data.message || `HTTP ${response.status}`);
            }
            this.showNotification(`已删除 Skill 草稿: ${draftName}`, "success");
            await this.loadSkillDrafts();
            this.closeSkillDraftModal();
        } catch (error) {
            this.showNotification(`删除失败: ${error.message}`, "error");
        }
    }

    openApprovalModal(payload) {
        this.pendingApproval = payload;
        if (!this.approvalModal) return;
        this.approvalReason.textContent = payload.reason || "该工具调用需要人工审批。";
        this.approvalToolName.textContent = payload.tool_name || "-";
        this.approvalToolArgs.textContent = payload.tool_args_summary || "-";
        this.approvalModal.classList.remove("hidden");
    }

    closeApprovalModal() {
        this.approvalModal?.classList.add("hidden");
    }

    async processAIOpsPayload(payload) {
        if (!payload) return false;

        if (payload.type === "trace" && payload.trace) {
            this.currentAIOpsTrace.push(payload.trace);
            this.renderAIOpsProgress();
            return false;
        }

        if (payload.type === "status") {
            this.addAIOpsStatus(payload.message || "正在分析...", payload.stage || "status");
            this.renderAIOpsProgress();
            return false;
        }

        if (payload.type === "plan") {
            this.ensureAIOpsState().plan = Array.isArray(payload.plan) ? payload.plan : [];
            if (payload.target_alert?.service_name && payload.target_alert?.alert_name) {
                this.addAIOpsStatus(
                    `已锁定目标告警：${payload.target_alert.service_name} / ${payload.target_alert.alert_name}`,
                    "target_alert",
                );
            }
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
                suggested_next_steps: Array.isArray(payload.suggested_next_steps)
                    ? payload.suggested_next_steps
                    : [],
            };
            this.addAIOpsStatus(
                payload.passed ? "Verifier 已通过。" : "Verifier 未通过，正在补充证据。",
                "verifier",
            );
            this.renderAIOpsProgress();
            return false;
        }

        if (payload.type === "approval_required") {
            this.addAIOpsStatus(
                `危险工具 ${payload.tool_name || "-"} 需要人工审批。`,
                "approval_required",
            );
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
            this.addAIOpsStatus("AIOps 诊断完成。", "complete");
            this.renderAIOpsProgress({ final: true, persistFinal: true });
            this.showNotification("AIOps 诊断完成", "success");
            return true;
        }

        if (payload.type === "error") {
            throw new Error(payload.message || "AIOps 诊断失败");
        }

        return false;
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
            this.showNotification(approved ? "已批准执行，继续诊断" : "已拒绝执行，重新规划中", "success");
            await this.sendAIOpsRequest({ resume: true });
        } catch (error) {
            this.showNotification(`审批操作失败: ${error.message}`, "error");
        }
    }

    async sendAIOpsRequest({ resume = false, task = "", mode = "default" } = {}) {
        if (this.isStreaming) {
            this.showNotification("当前已有任务在执行中", "warning");
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
        } else if (this.currentAIOpsMessage) {
            this.addAIOpsStatus("审批已处理，正在继续执行...", "approval_resume");
            this.renderAIOpsProgress();
        } else {
            const context = this.currentAIOpsContext || { task, mode };
            this.currentAIOpsContext = context;
            this.currentAIOpsState = this.currentAIOpsState || this.createAIOpsRenderState(context.task, context.mode);
            this.currentAIOpsMessage = this.addLoadingMessage("AIOps Agent 正在继续执行...");
            this.currentAIOpsMessage.classList.add("aiops-message");
            this.addAIOpsStatus("AIOps Agent 正在继续执行...", "workflow_resume");
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

            if (!response.ok || !response.body) {
                throw new Error(`HTTP ${response.status}`);
            }

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
            this.showNotification("请等待当前任务结束后再发起新的诊断", "warning");
            return;
        }
        const userInput = this.messageInput?.value?.trim() || "";
        const mode = userInput ? "custom" : "default";
        const task = userInput || "请检查当前系统是否存在活跃告警。如果存在告警，请选择最高严重级别告警，结合监控指标、日志、历史工单和知识库 runbook 进行根因分析，并保留完整 Agent Trace。";
        const userMessage = userInput
            ? userInput
            : "请执行默认 AIOps 巡检：检查当前系统是否存在活跃告警，并保留完整 Agent Trace。";

        this.addMessage("user", userMessage);
        if (this.messageInput) this.messageInput.value = "";
        await this.sendAIOpsRequest({ resume: false, task, mode });
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
            visible ? "正在上传并索引文件..." : "",
            visible ? "系统会在上传完成后立即尝试写入知识库。" : "",
        );
    }
}

const slideStyle = document.createElement("style");
slideStyle.textContent = `
@keyframes slideIn {
    from { opacity: 0; transform: translateY(6px); }
    to { opacity: 1; transform: translateY(0); }
}

@keyframes slideOut {
    from { opacity: 1; transform: translateY(0); }
    to { opacity: 0; transform: translateY(-6px); }
}
`;
document.head.appendChild(slideStyle);

document.addEventListener("DOMContentLoaded", () => {
    new SuperBizAgentApp();
});
