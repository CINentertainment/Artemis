"use strict";

const state = {
    conversations: [],
    currentConversationId: null,
    sending: false,
};

const elements = {
    conversationList: document.getElementById("conversation-list"),
    newConversationButton: document.getElementById("new-conversation-button"),
    welcomeNewOperationButton: document.getElementById("welcome-new-operation-button"),
    conversationTitle: document.getElementById("conversation-title"),
    conversationSubtitle: document.getElementById("conversation-subtitle"),
    welcomePanel: document.getElementById("welcome-panel"),
    messagePanel: document.getElementById("message-panel"),
    messageForm: document.getElementById("message-form"),
    messageInput: document.getElementById("message-input"),
    sendButton: document.getElementById("send-button"),
    composerStatus: document.getElementById("composer-status"),
    notification: document.getElementById("notification"),
    systemStatus: document.getElementById("system-status"),
    systemStatusText: document.getElementById("system-status-text"),
    refreshStatusButton: document.getElementById("refresh-status-button"),
    useIntelNotes: document.getElementById("use-intel-notes"),
    intelNotesOptions: document.getElementById("intel-notes-options"),
    intelNotesQuery: document.getElementById("intel-notes-query"),
    informationRecordsQuery: document.getElementById("information-records-query"),
    useRiskTracker: document.getElementById("use-risk-tracker"),
    riskTrackerOptions: document.getElementById("risk-tracker-options"),
    riskCaseId: document.getElementById("risk-case-id"),
    riskIncludeArchived: document.getElementById("risk-include-archived"),
    riskIncludeRelated: document.getElementById("risk-include-related"),
    useMarketLens: document.getElementById("use-market-lens"),
    marketLensOptions: document.getElementById("market-lens-options"),
    marketAssetId: document.getElementById("market-asset-id"),
    marketIncludeInactive: document.getElementById("market-include-inactive"),
    marketIncludePrices: document.getElementById("market-include-prices"),
    marketIncludeAnalytics: document.getElementById("market-include-analytics"),
    marketIncludeSignals: document.getElementById("market-include-signals"),
};

async function requestJson(url, options = {}) {
    const response = await fetch(url, {
        ...options,
        headers: {
            "Content-Type": "application/json",
            ...(options.headers || {}),
        },
    });

    if (!response.ok) {
        let message = `Request failed with status ${response.status}.`;

        try {
            const errorData = await response.json();
            if (errorData.detail) {
                message = errorData.detail;
            }
        } catch {
            // Keep the status-based message.
        }

        throw new Error(message);
    }

    if (response.status === 204) {
        return null;
    }

    return response.json();
}

function formatDate(value) {
    if (!value) {
        return "";
    }

    const parsed = new Date(value);

    if (Number.isNaN(parsed.getTime())) {
        return value;
    }

    return parsed.toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
    });
}

function showNotification(message, isError = false) {
    elements.notification.textContent = message;
    elements.notification.classList.toggle("error", isError);
    elements.notification.classList.remove("hidden");

    window.clearTimeout(showNotification.timeoutId);

    showNotification.timeoutId = window.setTimeout(() => {
        elements.notification.classList.add("hidden");
    }, 4500);
}

function setSystemStatus(status, label) {
    elements.systemStatus.className = `status-pill status-${status}`;
    elements.systemStatusText.textContent = label;
}

async function refreshSystemStatus() {
    setSystemStatus("checking", "Checking");

    try {
        const health = await requestJson("/services/health");
        const status = health.overall_status || "unavailable";

        if (status === "healthy") {
            setSystemStatus("healthy", "Healthy");
        } else if (status === "degraded") {
            setSystemStatus("degraded", "Degraded");
        } else {
            setSystemStatus("unavailable", "Unavailable");
        }
    } catch {
        try {
            await requestJson("/health");
            setSystemStatus("degraded", "Artemis online");
        } catch {
            setSystemStatus("unavailable", "Offline");
        }
    }
}

function renderConversationList() {
    elements.conversationList.replaceChildren();

    if (state.conversations.length === 0) {
        const empty = document.createElement("p");
        empty.className = "muted-text";
        empty.textContent = "No conversations yet.";
        elements.conversationList.appendChild(empty);
        return;
    }

    for (const conversation of state.conversations) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "conversation-item";

        if (conversation.id === state.currentConversationId) {
            button.classList.add("active");
        }

        const copy = document.createElement("div");
        copy.className = "conversation-copy";

        const title = document.createElement("div");
        title.className = "conversation-title";
        title.textContent = conversation.title || "Untitled operation";

        const date = document.createElement("div");
        date.className = "conversation-date";
        date.textContent = formatDate(
            conversation.updated_at || conversation.created_at,
        );

        copy.append(title, date);
        button.appendChild(copy);
        button.addEventListener("click", () => {
            selectConversation(conversation.id);
        });

        elements.conversationList.appendChild(button);
    }
}

async function loadConversations(selectFirst = false) {
    try {
        state.conversations = await requestJson("/conversations?limit=100");
        renderConversationList();

        if (
            selectFirst
            && !state.currentConversationId
            && state.conversations.length > 0
        ) {
            await selectConversation(state.conversations[0].id);
        }
    } catch (error) {
        elements.conversationList.innerHTML = "";

        const message = document.createElement("p");
        message.className = "muted-text";
        message.textContent = "Conversations could not be loaded.";
        elements.conversationList.appendChild(message);

        showNotification(error.message, true);
    }
}

function renderMessage(message) {
    const article = document.createElement("article");
    const isUser = message.role === "user";

    article.className = `message ${isUser ? "message-user" : "message-assistant"}`;

    const label = document.createElement("div");
    label.className = "message-label";
    label.textContent = isUser ? "Operator" : "Artemis";

    const content = document.createElement("div");
    content.className = "message-content";
    content.textContent = message.content || "";

    article.append(label, content);

    if (
        message.role === "assistant"
        && message.id
        && message.conversation_id
        && saveNoteForm
    ) {
        const draftButton = document.createElement("button");
        draftButton.type = "button";
        draftButton.textContent = "Draft note";
        draftButton.addEventListener("click", () => {
            draftNote(message, draftButton);
        });
        article.appendChild(draftButton);
    }

    elements.messagePanel.appendChild(article);
}

function scrollMessagesToBottom() {
    elements.messagePanel.scrollTop = elements.messagePanel.scrollHeight;
}

async function selectConversation(conversationId) {
    state.currentConversationId = conversationId;
    renderConversationList();

    const conversation = state.conversations.find(
        (item) => item.id === conversationId,
    );

    elements.conversationTitle.textContent = conversation?.title || "Conversation";
    elements.conversationSubtitle.textContent = "Private, locally stored operation record";

    elements.welcomePanel.classList.add("hidden");
    elements.messagePanel.classList.remove("hidden");
    elements.messagePanel.replaceChildren();

    const loading = document.createElement("p");
    loading.className = "muted-text";
    loading.textContent = "Loading messages…";
    elements.messagePanel.appendChild(loading);

    try {
        const messages = await requestJson(
            `/conversations/${conversationId}/messages?limit=500`,
        );

        elements.messagePanel.replaceChildren();

        if (messages.length === 0) {
            const empty = document.createElement("p");
            empty.className = "muted-text";
            empty.textContent = "This conversation has no messages.";
            elements.messagePanel.appendChild(empty);
        } else {
            for (const message of messages) {
                renderMessage(message);
            }
        }

        scrollMessagesToBottom();
        elements.messageInput.focus();
    } catch (error) {
        elements.messagePanel.replaceChildren();

        const failure = document.createElement("p");
        failure.className = "muted-text";
        failure.textContent = "Messages could not be loaded.";
        elements.messagePanel.appendChild(failure);

        showNotification(error.message, true);
    }
}

async function createConversation(title) {
    const conversation = await requestJson("/conversations", {
        method: "POST",
        body: JSON.stringify({ title: title || null }),
    });

    state.currentConversationId = conversation.id;

    await loadConversations();
    await selectConversation(conversation.id);

    return conversation;
}

async function handleNewConversation() {
    const title = window.prompt("Operation title:", "New Artemis Operation");

    if (title === null) {
        return;
    }

    try {
        await createConversation(title.trim() || null);
    } catch (error) {
        showNotification(error.message, true);
    }
}

function optionalPositiveInteger(element) {
    const value = element.value.trim();

    if (!value) {
        return null;
    }

    const parsed = Number.parseInt(value, 10);

    if (!Number.isInteger(parsed) || parsed < 1) {
        throw new Error("IDs must be positive whole numbers.");
    }

    return parsed;
}

function buildTurnPayload(content) {
    const payload = {
        content,
        use_intel_notes: elements.useIntelNotes.checked,
        use_risk_tracker: elements.useRiskTracker.checked,
        use_market_lens: elements.useMarketLens.checked,
    };

    const informationRecordsQuery = elements.informationRecordsQuery.value.trim();

    if (informationRecordsQuery) {
        payload.information_records_query = informationRecordsQuery;
    }

    if (payload.use_intel_notes) {
        const query = elements.intelNotesQuery.value.trim();
        payload.intel_notes_query = query || null;
    }

    if (payload.use_risk_tracker) {
        payload.risk_tracker_case_id = optionalPositiveInteger(elements.riskCaseId);
        payload.risk_tracker_include_archived = elements.riskIncludeArchived.checked;
        payload.risk_tracker_include_related = elements.riskIncludeRelated.checked;
    }

    if (payload.use_market_lens) {
        payload.market_lens_asset_id = optionalPositiveInteger(elements.marketAssetId);
        payload.market_lens_include_inactive = elements.marketIncludeInactive.checked;
        payload.market_lens_include_prices = elements.marketIncludePrices.checked;
        payload.market_lens_include_analytics = elements.marketIncludeAnalytics.checked;
        payload.market_lens_include_signals = elements.marketIncludeSignals.checked;
    }

    return payload;
}

function setSending(sending) {
    state.sending = sending;

    elements.sendButton.disabled = sending;
    elements.messageInput.disabled = sending;
    elements.sendButton.textContent = sending ? "Processing…" : "Transmit";

    elements.composerStatus.textContent = sending
        ? "ARTEMIS PROCESSING // LOCAL NODE"
        : "PRIVATE MODE // LOCAL PROCESSING";
}

async function handleMessageSubmit(event) {
    event.preventDefault();

    if (state.sending) {
        return;
    }

    const content = elements.messageInput.value.trim();

    if (!content) {
        return;
    }

    try {
        setSending(true);

        if (!state.currentConversationId) {
            const generatedTitle = content.length > 60
                ? `${content.slice(0, 57)}…`
                : content;

            await createConversation(generatedTitle);
        }

        const payload = buildTurnPayload(content);
        const response = await requestJson(
            `/conversations/${state.currentConversationId}/respond`,
            {
                method: "POST",
                body: JSON.stringify(payload),
            },
        );

        const emptyMessage = elements.messagePanel.querySelector(".muted-text");

        if (emptyMessage) {
            emptyMessage.remove();
        }

        renderMessage(response.user_message);
        renderMessage(response.assistant_message);

        elements.messageInput.value = "";

        scrollMessagesToBottom();
        await loadConversations();
    } catch (error) {
        showNotification(error.message, true);
    } finally {
        setSending(false);
        elements.messageInput.focus();
    }
}

function toggleOptions(checkbox, options) {
    options.classList.toggle("hidden", !checkbox.checked);
}

function bindEvents() {
    elements.newConversationButton.addEventListener("click", handleNewConversation);
    elements.welcomeNewOperationButton.addEventListener("click", handleNewConversation);
    elements.refreshStatusButton.addEventListener("click", refreshSystemStatus);
    elements.messageForm.addEventListener("submit", handleMessageSubmit);

    elements.messageInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && event.ctrlKey) {
            event.preventDefault();
            elements.messageForm.requestSubmit();
        }
    });

    elements.useIntelNotes.addEventListener("change", () => {
        toggleOptions(elements.useIntelNotes, elements.intelNotesOptions);
    });

    elements.useRiskTracker.addEventListener("change", () => {
        toggleOptions(elements.useRiskTracker, elements.riskTrackerOptions);
    });

    elements.useMarketLens.addEventListener("change", () => {
        toggleOptions(elements.useMarketLens, elements.marketLensOptions);
    });

    bindResearchForm();
}

async function initialize() {
    bindEvents();

    await Promise.all([
        refreshSystemStatus(),
        loadConversations(true),
    ]);
}

const saveNoteForm = document.getElementById("save-note-form");

if (saveNoteForm) {
    const titleInput = document.getElementById("save-note-title");
    const contentInput = document.getElementById("save-note-content");
    const saveButton = document.getElementById("save-note-button");
    const saveStatus = document.getElementById("save-note-status");

    saveNoteForm.addEventListener("submit", async (event) => {
        event.preventDefault();

        if (saveButton.disabled) {
            return;
        }

        const title = titleInput.value.trim();
        const content = contentInput.value.trim();

        if (!title || !content) {
            saveStatus.textContent = "Enter a title and note text.";
            return;
        }

        saveButton.disabled = true;
        saveStatus.textContent = "Saving note…";

        try {
            const record = await requestJson(
                "/information-sources/dashboard-notes/records",
                {
                    method: "POST",
                    body: JSON.stringify({ title, content }),
                },
            );

            if (
                titleInput.value.trim() === title
                && contentInput.value.trim() === content
            ) {
                saveNoteForm.reset();
            }

            saveStatus.textContent = `Note saved. Record ID: ${record.id}`;
        } catch (error) {
            saveStatus.textContent = `Could not save note. ${error.message}`;
        } finally {
            saveButton.disabled = false;
        }
    });
}

async function draftNote(message, button) {
    const titleInput = document.getElementById("save-note-title");
    const contentInput = document.getElementById("save-note-content");
    const saveButton = document.getElementById("save-note-button");
    const saveStatus = document.getElementById("save-note-status");
    const notePanel = saveNoteForm.closest("details");

    if (notePanel) {
        notePanel.open = true;
    }

    if (titleInput.value || contentInput.value || saveButton.disabled) {
        showNotification("Save or clear the current note before drafting another.", true);
        titleInput.focus();
        return;
    }

    button.disabled = true;
    button.textContent = "Drafting…";

    try {
        const conversationId = encodeURIComponent(message.conversation_id);
        const messageId = encodeURIComponent(message.id);
        const draft = await requestJson(
            `/conversations/${conversationId}/messages/${messageId}/draft-note`,
            { method: "POST" },
        );

        if (
            !button.isConnected
            || state.currentConversationId !== message.conversation_id
        ) {
            return;
        }

        if (titleInput.value || contentInput.value || saveButton.disabled) {
            showNotification("The current note was kept. Clear it to draft another.", true);
            return;
        }

        titleInput.value = draft.title;
        contentInput.value = draft.content;
        saveStatus.textContent = "Review or edit the draft, then click Save note.";
        titleInput.focus();
    } catch (error) {
        showNotification(`Could not draft note. ${error.message}`, true);
    } finally {
        button.disabled = false;
        button.textContent = "Draft note";
    }
}

function bindResearchForm() {
    const form = document.getElementById("web-research-form");

    if (!form) {
        return;
    }

    const topicInput = document.getElementById("web-research-topic");
    const caseInput = document.getElementById("web-research-case-id");
    const runButton = form.querySelector('button[type="submit"]');
    const status = document.getElementById("web-research-status");
    const sourceList = document.getElementById("web-research-sources");
    const briefOutput = document.getElementById("web-research-brief");

    form.addEventListener("submit", async (event) => {
        event.preventDefault();

        if (runButton.disabled) {
            return;
        }

        const topic = topicInput.value.trim();
        if (!topic) {
            status.textContent = "Enter a research topic.";
            topicInput.focus();
            return;
        }

        const payload = { topic };
        const caseValue = caseInput.value.trim();

        if (caseValue) {
            const caseId = Number(caseValue);
            if (!Number.isSafeInteger(caseId) || caseId < 1) {
                status.textContent = "Enter a positive whole-number Risk Tracker case ID.";
                caseInput.focus();
                return;
            }
            payload.risk_tracker_case_id = caseId;
        }

        runButton.disabled = true;
        sourceList.replaceChildren();
        briefOutput.textContent = "";
        status.textContent = "Searching the web, saving evidence, and preparing the brief...";

        try {
            const result = await requestJson("/research", {
                method: "POST",
                body: JSON.stringify(payload),
            });

            if (
                !result ||
                typeof result.brief !== "string" ||
                !Array.isArray(result.sources)
            ) {
                throw new Error("Artemis returned an unexpected research response.");
            }

            const sourceItems = document.createDocumentFragment();
            let hasSaveError = false;

            for (const source of result.sources) {
                const item = document.createElement("li");
                const title = source.title || source.url || "Untitled source";
                let url;

                try {
                    url = new URL(source.url);
                } catch {
                    // Display malformed URLs as plain text.
                }

                if (url?.protocol === "https:") {
                    const link = document.createElement("a");
                    link.href = url.href;
                    link.textContent = title;
                    link.target = "_blank";
                    link.rel = "noopener noreferrer";
                    item.appendChild(link);
                } else {
                    item.textContent = title;
                }

                const details = [`Artemis record ${source.record_id}`];

                if (source.intel_note_id != null) {
                    details.push(`Intel Notes ID ${source.intel_note_id}`);
                } else if (source.intel_note_error) {
                    details.push(`Intel Notes import failed: ${source.intel_note_error}`);
                    hasSaveError = true;
                }

                if (payload.risk_tracker_case_id !== undefined) {
                    if (source.risk_tracker_evidence_id != null) {
                        details.push(
                            `Risk Tracker case ${payload.risk_tracker_case_id}, ` +
                            `evidence ID ${source.risk_tracker_evidence_id}`
                        );
                    } else {
                        const reason = source.risk_tracker_error || "No evidence ID returned.";
                        details.push(`Risk Tracker attachment failed: ${reason}`);
                        hasSaveError = true;
                    }
                }

                item.append(document.createTextNode(` | ${details.join(" | ")}`));
                sourceItems.appendChild(item);
            }

            sourceList.replaceChildren(sourceItems);
            briefOutput.textContent = result.brief;
            status.textContent = hasSaveError
                ? "Brief ready. See the source list for save errors."
                : "Brief ready.";
        } catch (error) {
            status.textContent = `Research failed. ${error.message}`;
        } finally {
            runButton.disabled = false;
        }
    });
}
initialize();