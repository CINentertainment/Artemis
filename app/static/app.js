"use strict";

const state = {
    conversations: [],
    currentConversationId: null,
    sending: false,
};

const elements = {
    conversationList: document.getElementById(
        "conversation-list"
    ),
    newConversationButton: document.getElementById(
        "new-conversation-button"
    ),
    welcomeNewOperationButton: document.getElementById(
        "welcome-new-operation-button"
    ),
    conversationTitle: document.getElementById(
        "conversation-title"
    ),
    conversationSubtitle: document.getElementById(
        "conversation-subtitle"
    ),
    welcomePanel: document.getElementById(
        "welcome-panel"
    ),
    messagePanel: document.getElementById(
        "message-panel"
    ),
    messageForm: document.getElementById(
        "message-form"
    ),
    messageInput: document.getElementById(
        "message-input"
    ),
    sendButton: document.getElementById(
        "send-button"
    ),
    composerStatus: document.getElementById(
        "composer-status"
    ),
    notification: document.getElementById(
        "notification"
    ),
    systemStatus: document.getElementById(
        "system-status"
    ),
    systemStatusText: document.getElementById(
        "system-status-text"
    ),
    refreshStatusButton: document.getElementById(
        "refresh-status-button"
    ),
    useIntelNotes: document.getElementById(
        "use-intel-notes"
    ),
    intelNotesOptions: document.getElementById(
        "intel-notes-options"
    ),
    intelNotesQuery: document.getElementById(
        "intel-notes-query"
    ),
    useRiskTracker: document.getElementById(
        "use-risk-tracker"
    ),
    riskTrackerOptions: document.getElementById(
        "risk-tracker-options"
    ),
    riskCaseId: document.getElementById(
        "risk-case-id"
    ),
    riskIncludeArchived: document.getElementById(
        "risk-include-archived"
    ),
    riskIncludeRelated: document.getElementById(
        "risk-include-related"
    ),
    useMarketLens: document.getElementById(
        "use-market-lens"
    ),
    marketLensOptions: document.getElementById(
        "market-lens-options"
    ),
    marketAssetId: document.getElementById(
        "market-asset-id"
    ),
    marketIncludeInactive: document.getElementById(
        "market-include-inactive"
    ),
    marketIncludePrices: document.getElementById(
        "market-include-prices"
    ),
    marketIncludeAnalytics: document.getElementById(
        "market-include-analytics"
    ),
    marketIncludeSignals: document.getElementById(
        "market-include-signals"
    ),
};

async function requestJson(
    url,
    options = {},
) {
    const response = await fetch(
        url,
        {
            ...options,
            headers: {
                "Content-Type": "application/json",
                ...(options.headers || {}),
            },
        },
    );

    if (!response.ok) {
        let message = (
            `Request failed with status `
            + `${response.status}.`
        );

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

    return parsed.toLocaleString(
        [],
        {
            month: "short",
            day: "numeric",
            hour: "numeric",
            minute: "2-digit",
        },
    );
}

function showNotification(
    message,
    isError = false,
) {
    elements.notification.textContent = message;
    elements.notification.classList.toggle(
        "error",
        isError,
    );
    elements.notification.classList.remove(
        "hidden"
    );

    window.clearTimeout(
        showNotification.timeoutId
    );

    showNotification.timeoutId = (
        window.setTimeout(
            () => {
                elements.notification.classList.add(
                    "hidden"
                );
            },
            4500,
        )
    );
}

function setSystemStatus(
    status,
    label,
) {
    elements.systemStatus.className = (
        `status-pill status-${status}`
    );

    elements.systemStatusText.textContent = label;
}

async function refreshSystemStatus() {
    setSystemStatus(
        "checking",
        "Checking",
    );

    try {
        const health = await requestJson(
            "/services/health"
        );

        const status = (
            health.overall_status
            || "unavailable"
        );

        if (status === "healthy") {
            setSystemStatus(
                "healthy",
                "Healthy",
            );
        } else if (status === "degraded") {
            setSystemStatus(
                "degraded",
                "Degraded",
            );
        } else {
            setSystemStatus(
                "unavailable",
                "Unavailable",
            );
        }
    } catch {
        try {
            await requestJson("/health");

            setSystemStatus(
                "degraded",
                "Artemis online",
            );
        } catch {
            setSystemStatus(
                "unavailable",
                "Offline",
            );
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

    for (
        const conversation
        of state.conversations
    ) {
        const button = document.createElement(
            "button"
        );

        button.type = "button";
        button.className = "conversation-item";

        if (
            conversation.id
            === state.currentConversationId
        ) {
            button.classList.add("active");
        }

        const copy = document.createElement("div");
        copy.className = "conversation-copy";

        const title = document.createElement("div");
        title.className = "conversation-title";
        title.textContent = (
            conversation.title
            || "Untitled operation"
        );

        const date = document.createElement("div");
        date.className = "conversation-date";
        date.textContent = formatDate(
            conversation.updated_at
            || conversation.created_at
        );

        copy.append(title, date);
        button.appendChild(copy);

        button.addEventListener(
            "click",
            () => {
                selectConversation(
                    conversation.id
                );
            },
        );

        elements.conversationList.appendChild(
            button
        );
    }
}

async function loadConversations(
    selectFirst = false,
) {
    try {
        state.conversations = await requestJson(
            "/conversations?limit=100"
        );

        renderConversationList();

        if (
            selectFirst
            && !state.currentConversationId
            && state.conversations.length > 0
        ) {
            await selectConversation(
                state.conversations[0].id
            );
        }
    } catch (error) {
        elements.conversationList.innerHTML = "";

        const message = document.createElement("p");
        message.className = "muted-text";
        message.textContent = (
            "Conversations could not be loaded."
        );

        elements.conversationList.appendChild(
            message
        );

        showNotification(
            error.message,
            true,
        );
    }
}

function renderMessage(message) {
    const article = document.createElement(
        "article"
    );

    const isUser = message.role === "user";

    article.className = (
        `message ${
            isUser
                ? "message-user"
                : "message-assistant"
        }`
    );

    const label = document.createElement("div");
    label.className = "message-label";
    label.textContent = (
        isUser ? "Operator" : "Artemis"
    );

    const content = document.createElement("div");
    content.className = "message-content";
    content.textContent = message.content || "";

    article.append(label, content);

    elements.messagePanel.appendChild(article);
}

function scrollMessagesToBottom() {
    elements.messagePanel.scrollTop = (
        elements.messagePanel.scrollHeight
    );
}

async function selectConversation(
    conversationId,
) {
    state.currentConversationId = conversationId;

    renderConversationList();

    const conversation = (
        state.conversations.find(
            (item) => item.id === conversationId
        )
    );

    elements.conversationTitle.textContent = (
        conversation?.title
        || "Conversation"
    );

    elements.conversationSubtitle.textContent = (
        "Private, locally stored operation record"
    );

    elements.welcomePanel.classList.add(
        "hidden"
    );

    elements.messagePanel.classList.remove(
        "hidden"
    );

    elements.messagePanel.replaceChildren();

    const loading = document.createElement("p");
    loading.className = "muted-text";
    loading.textContent = "Loading messages…";

    elements.messagePanel.appendChild(loading);

    try {
        const messages = await requestJson(
            `/conversations/${
                conversationId
            }/messages?limit=500`
        );

        elements.messagePanel.replaceChildren();

        if (messages.length === 0) {
            const empty = document.createElement("p");
            empty.className = "muted-text";
            empty.textContent = (
                "This conversation has no messages."
            );

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
        failure.textContent = (
            "Messages could not be loaded."
        );

        elements.messagePanel.appendChild(failure);

        showNotification(
            error.message,
            true,
        );
    }
}

async function createConversation(
    title,
) {
    const conversation = await requestJson(
        "/conversations",
        {
            method: "POST",
            body: JSON.stringify({
                title: title || null,
            }),
        },
    );

    state.currentConversationId = (
        conversation.id
    );

    await loadConversations();
    await selectConversation(conversation.id);

    return conversation;
}

async function handleNewConversation() {
    const title = window.prompt(
        "Operation title:",
        "New Artemis Operation",
    );

    if (title === null) {
        return;
    }

    try {
        await createConversation(
            title.trim() || null
        );
    } catch (error) {
        showNotification(
            error.message,
            true,
        );
    }
}

function optionalPositiveInteger(element) {
    const value = element.value.trim();

    if (!value) {
        return null;
    }

    const parsed = Number.parseInt(value, 10);

    if (
        !Number.isInteger(parsed)
        || parsed < 1
    ) {
        throw new Error(
            "IDs must be positive whole numbers."
        );
    }

    return parsed;
}

function buildTurnPayload(content) {
    const payload = {
        content,
        use_intel_notes: (
            elements.useIntelNotes.checked
        ),
        use_risk_tracker: (
            elements.useRiskTracker.checked
        ),
        use_market_lens: (
            elements.useMarketLens.checked
        ),
    };

    if (payload.use_intel_notes) {
        const query = (
            elements.intelNotesQuery
            .value
            .trim()
        );

        payload.intel_notes_query = (
            query || null
        );
    }

    if (payload.use_risk_tracker) {
        payload.risk_tracker_case_id = (
            optionalPositiveInteger(
                elements.riskCaseId
            )
        );

        payload.risk_tracker_include_archived = (
            elements.riskIncludeArchived.checked
        );

        payload.risk_tracker_include_related = (
            elements.riskIncludeRelated.checked
        );
    }

    if (payload.use_market_lens) {
        payload.market_lens_asset_id = (
            optionalPositiveInteger(
                elements.marketAssetId
            )
        );

        payload.market_lens_include_inactive = (
            elements
            .marketIncludeInactive
            .checked
        );

        payload.market_lens_include_prices = (
            elements.marketIncludePrices.checked
        );

        payload.market_lens_include_analytics = (
            elements
            .marketIncludeAnalytics
            .checked
        );

        payload.market_lens_include_signals = (
            elements.marketIncludeSignals.checked
        );
    }

    return payload;
}

function setSending(sending) {
    state.sending = sending;

    elements.sendButton.disabled = sending;
    elements.messageInput.disabled = sending;

    elements.sendButton.textContent = (
        sending ? "Processing…" : "Transmit"
    );

    elements.composerStatus.textContent = (
        sending
            ? "ARTEMIS PROCESSING // LOCAL NODE"
            : "PRIVATE MODE // LOCAL PROCESSING"
    );
}

async function handleMessageSubmit(event) {
    event.preventDefault();

    if (state.sending) {
        return;
    }

    const content = (
        elements.messageInput.value.trim()
    );

    if (!content) {
        return;
    }

    try {
        setSending(true);

        if (!state.currentConversationId) {
            const generatedTitle = (
                content.length > 60
                    ? `${content.slice(0, 57)}…`
                    : content
            );

            await createConversation(
                generatedTitle
            );
        }

        const payload = buildTurnPayload(
            content
        );

        const response = await requestJson(
            `/conversations/${
                state.currentConversationId
            }/respond`,
            {
                method: "POST",
                body: JSON.stringify(payload),
            },
        );

        const emptyMessage = (
            elements.messagePanel.querySelector(
                ".muted-text"
            )
        );

        if (emptyMessage) {
            emptyMessage.remove();
        }

        renderMessage(response.user_message);
        renderMessage(response.assistant_message);

        elements.messageInput.value = "";

        scrollMessagesToBottom();
        await loadConversations();
    } catch (error) {
        showNotification(
            error.message,
            true,
        );
    } finally {
        setSending(false);
        elements.messageInput.focus();
    }
}

function toggleOptions(
    checkbox,
    options,
) {
    options.classList.toggle(
        "hidden",
        !checkbox.checked,
    );
}

function bindEvents() {
    elements.newConversationButton.addEventListener(
        "click",
        handleNewConversation,
    );

    elements.welcomeNewOperationButton.addEventListener(
        "click",
        handleNewConversation,
    );

    elements.refreshStatusButton.addEventListener(
        "click",
        refreshSystemStatus,
    );

    elements.messageForm.addEventListener(
        "submit",
        handleMessageSubmit,
    );

    elements.messageInput.addEventListener(
        "keydown",
        (event) => {
            if (
                event.key === "Enter"
                && event.ctrlKey
            ) {
                event.preventDefault();
                elements.messageForm.requestSubmit();
            }
        },
    );

    elements.useIntelNotes.addEventListener(
        "change",
        () => {
            toggleOptions(
                elements.useIntelNotes,
                elements.intelNotesOptions,
            );
        },
    );

    elements.useRiskTracker.addEventListener(
        "change",
        () => {
            toggleOptions(
                elements.useRiskTracker,
                elements.riskTrackerOptions,
            );
        },
    );

    elements.useMarketLens.addEventListener(
        "change",
        () => {
            toggleOptions(
                elements.useMarketLens,
                elements.marketLensOptions,
            );
        },
    );
}

async function initialize() {
    bindEvents();

    await Promise.all([
        refreshSystemStatus(),
        loadConversations(true),
    ]);
}

initialize();