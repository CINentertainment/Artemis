"use strict";

function artemisReference(record) {
    return record?.reference || "Reference unavailable";
}


const state = {
    conversations: [],
    currentConversationId: null,
    sending: false,
    conversationLoad: 0,
};

const elements = {};
for (const [key, id] of Object.entries({
    conversationList: "conversation-list",
    newConversationButton: "new-conversation-button",
    welcomeNewOperationButton: "welcome-new-operation-button",
    conversationTitle: "conversation-title",
    conversationSubtitle: "conversation-subtitle",
    welcomePanel: "welcome-panel",
    messagePanel: "message-panel",
    messageForm: "message-form",
    messageInput: "message-input",
    sendButton: "send-button",
    composerStatus: "composer-status",
    notification: "notification",
    systemStatus: "system-status",
    systemStatusText: "system-status-text",
    refreshStatusButton: "refresh-status-button",
    useIntelNotes: "use-intel-notes",
    intelNotesOptions: "intel-notes-options",
    intelNotesQuery: "intel-notes-query",
    informationRecordsQuery: "information-records-query",
    useRiskTracker: "use-risk-tracker",
    riskTrackerOptions: "risk-tracker-options",
    riskCaseId: "risk-case-id",
    riskIncludeArchived: "risk-include-archived",
    riskIncludeRelated: "risk-include-related",
    useMarketLens: "use-market-lens",
    marketLensOptions: "market-lens-options",
    marketAssetId: "market-asset-id",
    marketIncludeInactive: "market-include-inactive",
    marketIncludePrices: "market-include-prices",
    marketIncludeAnalytics: "market-include-analytics",
    marketIncludeSignals: "market-include-signals",
})) {
    elements[key] = document.getElementById(id);
}

const saveNoteForm = document.getElementById("save-note-form");

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
            const data = await response.json();
            if (typeof data.detail === "string") {
                message = data.detail;
            } else if (Array.isArray(data.detail)) {
                message = data.detail.map((item) => {
                    const location = (item.loc || []).join(".");
                    return `${location}: ${item.msg}`;
                }).join("; ");
            }
        } catch {
            // Keep the HTTP status message.
        }
        throw new Error(message);
    }

    return response.status === 204 ? null : response.json();
}

function formatDate(value) {
    if (!value) return "";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
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
        const status = health.overall_status;
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

    if (!state.conversations.length) {
        const empty = document.createElement("p");
        empty.className = "muted-text";
        empty.textContent = "No conversations yet.";
        elements.conversationList.append(empty);
        return;
    }

    for (const conversation of state.conversations) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "conversation-item";
        button.classList.toggle(
            "active",
            conversation.id === state.currentConversationId,
        );

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
        button.append(copy);
        button.addEventListener("click", () => {
            if (!state.sending) selectConversation(conversation.id);
        });
        elements.conversationList.append(button);
    }
}

async function loadConversations(selectFirst = false) {
    try {
        state.conversations = await (async () => {
            const conversations = [];
            for (let offset = 0; ; offset += 200) {
                const page = await requestJson(
                    `/conversations?limit=200&offset=${offset}`,
                );
                if (!Array.isArray(page)) {
                    throw new Error("Invalid conversation archive response.");
                }
                conversations.push(...page);
                if (page.length < 200) return conversations;
            }
        })();
        renderConversationList();
        if (
            selectFirst &&
            !state.currentConversationId &&
            state.conversations.length
        ) {
            await selectConversation(state.conversations[0].id);
        }
    } catch (error) {
        elements.conversationList.replaceChildren();
        const message = document.createElement("p");
        message.className = "muted-text";
        message.textContent = "Conversations could not be loaded.";
        elements.conversationList.append(message);
        showNotification(error.message, true);
    }
}

function renderMessage(message) {
    const article = document.createElement("article");
    const isUser = message.role === "user";
    article.className = `message ${
        isUser ? "message-user" : "message-assistant"
    }`;

    const label = document.createElement("div");
    label.className = "message-label";
    label.textContent = isUser ? "Operator" : "Artemis";

    const content = document.createElement("div");
    content.className = "message-content";
    content.textContent = message.content || "";
    article.append(label, content);

    if (
        message.role === "assistant" &&
        message.id &&
        message.conversation_id &&
        saveNoteForm
    ) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = "Draft note";
        button.addEventListener("click", () => draftNote(message, button));
        article.append(button);
    }

    elements.messagePanel.append(article);
}

function scrollMessagesToBottom() {
    elements.messagePanel.scrollTop = elements.messagePanel.scrollHeight;
}

async function selectConversation(conversationId) {
    const loadId = ++state.conversationLoad;
    state.currentConversationId = conversationId;
    renderConversationList();

    const conversation = state.conversations.find(
        (item) => item.id === conversationId,
    );
    elements.conversationTitle.textContent =
        conversation?.title || "Conversation";
    elements.conversationSubtitle.textContent =
        "Private, locally stored operation record";
    elements.welcomePanel.classList.add("hidden");
    elements.messagePanel.classList.remove("hidden");

    const loading = document.createElement("p");
    loading.className = "muted-text";
    loading.textContent = "Loading messages...";
    elements.messagePanel.replaceChildren(loading);

    try {
        const messages = await requestJson(
            `/conversations/${encodeURIComponent(conversationId)}/messages?limit=500`,
        );
        if (loadId !== state.conversationLoad) return;

        elements.messagePanel.replaceChildren();
        if (!messages.length) {
            const empty = document.createElement("p");
            empty.className = "muted-text";
            empty.textContent = "This conversation has no messages.";
            elements.messagePanel.append(empty);
        } else {
            for (const message of messages) renderMessage(message);
        }
        scrollMessagesToBottom();
    } catch (error) {
        if (loadId !== state.conversationLoad) return;
        elements.messagePanel.textContent = "Messages could not be loaded.";
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
    if (state.sending) return;
    const title = window.prompt("Operation title:", "New Artemis Operation");
    if (title === null) return;
    try {
        await createConversation(title.trim() || null);
    } catch (error) {
        showNotification(error.message, true);
    }
}

function optionalPositiveInteger(element) {
    const value = element.value.trim();
    if (!value) return null;

    const parsed = Number(value);
    if (!Number.isSafeInteger(parsed) || parsed < 1) {
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

    const query = elements.informationRecordsQuery.value.trim();
    if (query) payload.information_records_query = query;

    if (payload.use_intel_notes) {
        payload.intel_notes_query =
            elements.intelNotesQuery.value.trim() || null;
    }
    if (payload.use_risk_tracker) {
        payload.risk_tracker_case_id =
            optionalPositiveInteger(elements.riskCaseId);
        payload.risk_tracker_include_archived =
            elements.riskIncludeArchived.checked;
        payload.risk_tracker_include_related =
            elements.riskIncludeRelated.checked;
    }
    if (payload.use_market_lens) {
        payload.market_lens_asset_id =
            optionalPositiveInteger(elements.marketAssetId);
        payload.market_lens_include_inactive =
            elements.marketIncludeInactive.checked;
        payload.market_lens_include_prices =
            elements.marketIncludePrices.checked;
        payload.market_lens_include_analytics =
            elements.marketIncludeAnalytics.checked;
        payload.market_lens_include_signals =
            elements.marketIncludeSignals.checked;
    }
    return payload;
}

function setSending(sending) {
    state.sending = sending;
    elements.sendButton.disabled = sending;
    elements.messageInput.disabled = sending;
    elements.newConversationButton.disabled = sending;
    elements.welcomeNewOperationButton.disabled = sending;
    elements.sendButton.textContent = sending ? "Processing..." : "Transmit";
    elements.composerStatus.textContent = sending
        ? "ARTEMIS PROCESSING // LOCAL NODE"
        : "PRIVATE MODE // LOCAL PROCESSING";
}

async function handleMessageSubmit(event) {
    event.preventDefault();
    if (state.sending) return;

    const content = elements.messageInput.value.trim();
    if (!content) return;

    try {
        const payload = buildTurnPayload(content);
        setSending(true);

        if (!state.currentConversationId) {
            const title = content.length > 60
                ? `${content.slice(0, 57)}...`
                : content;
            await createConversation(title);
        }

        const response = await requestJson(
            `/conversations/${state.currentConversationId}/respond`,
            { method: "POST", body: JSON.stringify(payload) },
        );

        elements.messagePanel.querySelector(".muted-text")?.remove();
        renderMessage(response.user_message);
        renderMessage(response.assistant_message);
        document.dispatchEvent(new CustomEvent("artemis:assistant-reply", {
            detail: { content: response.assistant_message.content || "" },
        }));
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

function bindNoteForm() {
    if (!saveNoteForm) return;

    const titleInput = document.getElementById("save-note-title");
    const contentInput = document.getElementById("save-note-content");
    const button = document.getElementById("save-note-button");
    const status = document.getElementById("save-note-status");

    saveNoteForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (button.disabled) return;

        const title = titleInput.value.trim();
        const content = contentInput.value.trim();
        if (!title || !content) {
            status.textContent = "Enter a title and note text.";
            return;
        }

        button.disabled = true;
        status.textContent = "Saving note...";

        try {
            const record = await requestJson(
                "/information-sources/dashboard-notes/records",
                {
                    method: "POST",
                    body: JSON.stringify({ title, content }),
                },
            );
            if (
                titleInput.value.trim() === title &&
                contentInput.value.trim() === content
            ) {
                saveNoteForm.reset();
            }
            status.textContent = `Note saved. Record ID: ${record.id}`;
        } catch (error) {
            status.textContent = `Could not save note. ${error.message}`;
        } finally {
            button.disabled = false;
        }
    });
}

async function draftNote(message, button) {
    const title = document.getElementById("save-note-title");
    const content = document.getElementById("save-note-content");
    const saveButton = document.getElementById("save-note-button");
    const status = document.getElementById("save-note-status");
    const panel = saveNoteForm.closest("details");
    if (panel) panel.open = true;

    if (title.value || content.value || saveButton.disabled) {
        showNotification(
            "Save or clear the current note before drafting another.",
            true,
        );
        return;
    }

    button.disabled = true;
    button.textContent = "Drafting...";
    try {
        const conversationId = encodeURIComponent(message.conversation_id);
        const messageId = encodeURIComponent(message.id);
        const draft = await requestJson(
            `/conversations/${conversationId}/messages/${messageId}/draft-note`,
            { method: "POST" },
        );
        if (
            !button.isConnected ||
            state.currentConversationId !== message.conversation_id
        ) return;

        if (title.value || content.value || saveButton.disabled) {
            showNotification(
                "The current note was kept. Clear it to draft another.",
                true,
            );
            return;
        }

        title.value = draft.title;
        content.value = draft.content;
        status.textContent = "Review or edit the draft, then click Save note.";
        title.focus();
    } catch (error) {
        showNotification(`Could not draft note. ${error.message}`, true);
    } finally {
        button.disabled = false;
        button.textContent = "Draft note";
    }
}

function renderSources(container, sources, caseId = null) {
    container.replaceChildren();
    let hasErrors = false;

    for (const source of sources || []) {
        const item = document.createElement("li");
        const title = source.title || source.url || "Untitled source";
        let url;
        try {
            url = new URL(source.url);
        } catch {
            // Invalid URLs remain plain text.
        }

        if (url?.protocol === "https:" || url?.protocol === "http:") {
            const link = document.createElement("a");
            link.href = url.href;
            link.textContent = title;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            item.append(link);
        } else {
            item.textContent = title;
        }

        const details = [];
        if (source.record_id) {
            details.push(`Artemis record ${source.record_id}`);
        }
        if (source.intel_note_id != null) {
            details.push(`Intel Notes ID ${source.intel_note_id}`);
        }
        if (source.intel_note_error) {
            details.push(`Intel Notes import failed: ${source.intel_note_error}`);
            hasErrors = true;
        }

        if (source.risk_tracker_evidence_id != null) {
            details.push(
                `Risk Tracker case ${source.risk_tracker_case_id ?? caseId}, ` +
                `evidence ID ${source.risk_tracker_evidence_id}`,
            );
        } else if (source.risk_tracker_error || caseId != null) {
            details.push(
                "Risk Tracker attachment failed: " +
                (source.risk_tracker_error || "No evidence ID returned."),
            );
            hasErrors = true;
        }

        if (details.length) {
            item.append(document.createTextNode(` | ${details.join(" | ")}`));
        }
        container.append(item);
    }

    return hasErrors;
}

function bindResearchForm() {
    const form = document.getElementById("web-research-form");
    if (!form) return;

    const topic = document.getElementById("web-research-topic");
    const caseInput = document.getElementById("web-research-case-id");
    const button = form.querySelector('button[type="submit"]');
    const status = document.getElementById("web-research-status");
    const sources = document.getElementById("web-research-sources");
    const brief = document.getElementById("web-research-brief");

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (button.disabled) return;

        try {
            const payload = {
                topic: topic.value.trim(),
                risk_tracker_case_id: optionalPositiveInteger(caseInput),
            };
            if (!payload.topic) throw new Error("Enter a research topic.");

            button.disabled = true;
            sources.replaceChildren();
            brief.textContent = "";
            status.textContent =
                "Searching the web, saving evidence, and preparing the brief...";

            const result = await requestJson("/research", {
                method: "POST",
                body: JSON.stringify(payload),
            });
            if (
                typeof result?.brief !== "string" ||
                !Array.isArray(result.sources)
            ) {
                throw new Error("Artemis returned an unexpected research response.");
            }

            const sourceErrors = renderSources(
                sources,
                result.sources,
                payload.risk_tracker_case_id,
            );
            const eventErrors = (result.event_notes || []).some((note) =>
                note.error || note.risk_tracker_link_error
            );
            const hasWarnings = sourceErrors || eventErrors ||
                result.event_extraction_error || result.synthesis_error ||
                result.collection_errors?.length;

            brief.textContent = result.brief;
            status.textContent = hasWarnings
                ? "Brief ready with warnings. Review the brief and source details."
                : "Brief ready.";
        } catch (error) {
            status.textContent = `Research failed. ${error.message}`;
        } finally {
            button.disabled = false;
        }
    });
}

/*
 * Saved research requisitions.
 * This panel is inserted into the existing HTML.
 * All retrieved text is displayed with textContent.
 */
function bindRequisitions() {
    const consolePanel = document.querySelector(".command-console");
    if (!consolePanel) return;

    const panel = document.createElement("details");
    panel.id = "research-requisitions";
    panel.className = "source-options";
    panel.open = true;

    // Static interface markup only. Server content is never inserted here.
    panel.innerHTML = `
        <summary>Research requisitions</summary>
        <div style="padding-top:16px">
            <p>Save research questions, run collection, reopen briefs,
               and compare saved runs.</p>

            <fieldset id="req-controls"
                style="border:0;padding:0;margin:0;min-width:0">
                <form id="req-create-form">
                    <label for="req-topic">Research topic</label>
                    <input id="req-topic" type="text" maxlength="300" required>

                    <label for="req-questions">
                        Research questions — one per line, up to 10
                    </label>
                    <textarea id="req-questions" rows="4"
                        placeholder="What has changed?&#10;What information is missing?"
                    ></textarea>

                    <label for="req-case">Risk Tracker case ID (optional)</label>
                    <input id="req-case" type="number" min="1" step="1">

                    <button type="submit">Save requisition</button>
                </form>

                <hr style="margin:20px 0;opacity:.25">

                <label for="req-select">Saved requisitions</label>
                <select id="req-select" style="width:100%"></select>
                <button id="req-refresh" type="button">Refresh archive</button>

                <pre id="req-details"
                    style="white-space:pre-wrap;overflow-wrap:anywhere"></pre>

                <button id="req-run" type="button">
                    Run selected requisition
                </button>

                <p>Running research collects web evidence and can save notes
                   and attach records to the selected case.</p>

                <label for="req-run-select">Saved runs</label>
                <select id="req-run-select" style="width:100%"></select>
                <button id="req-open" type="button">Open saved run</button>

                <hr style="margin:20px 0;opacity:.25">

                <label for="req-baseline">Comparison baseline</label>
                <select id="req-baseline" style="width:100%"></select>

                <label for="req-target">Comparison target</label>
                <select id="req-target" style="width:100%"></select>

                <button id="req-compare" type="button">Compare saved runs</button>
            </fieldset>

            <p id="req-status" role="status" aria-live="polite"></p>
            <h3 id="req-output-title">Saved research output</h3>
            <ul id="req-sources"></ul>
            <pre id="req-output" tabindex="0"
                style="white-space:pre-wrap;overflow-wrap:anywhere;
                       max-height:650px;overflow:auto"></pre>
        </div>
    `;
    consolePanel.append(panel);

    const ui = {};
    for (const name of [
        "controls", "create-form", "topic", "questions", "case",
        "select", "refresh", "details", "run", "run-select", "open",
        "baseline", "target", "compare", "status", "sources",
        "output", "output-title",
    ]) {
        ui[name] = document.getElementById(`req-${name}`);
    }

    let requisitions = [];
    let runs = [];
    let busy = false;
    const pendingRequests = new Map();

    function requisitionUrl() {
        if (!ui.select.value) throw new Error("Select a saved requisition.");
        return `/research/requisitions/${encodeURIComponent(ui.select.value)}`;
    }

    function fillSelect(select, items, label, placeholder) {
        select.replaceChildren(new Option(placeholder, ""));
        for (const item of items) {
            select.add(new Option(label(item), item.id));
        }
    }

    function runLabel(run) {
        let value = run.started_at || "";
        if (value && !/(Z|[+-]\d{2}:\d{2})$/i.test(value)) {
            value += "Z";
        }
        return `${artemisReference(run)} | ${run.status} | ${formatDate(value)}`;
    }

    async function allPages(url) {
        const records = [];
        for (let offset = 0; ; offset += 100) {
            const page = await requestJson(`${url}?limit=100&offset=${offset}`);
            if (!Array.isArray(page)) {
                throw new Error("The archive returned an invalid collection.");
            }
            records.push(...page);
            if (page.length < 100) return records;
        }
    }

    async function action(work) {
        if (busy) return;
        busy = true;
        ui.controls.disabled = true;
        panel.setAttribute("aria-busy", "true");
        try {
            await work();
        } catch (error) {
            ui.status.textContent = error.message;
        } finally {
            busy = false;
            ui.controls.disabled = false;
            panel.removeAttribute("aria-busy");
        }
    }

    function clearOutput() {
        ui.sources.replaceChildren();
        ui.output.textContent = "";
        ui["output-title"].textContent = "Saved research output";
    }

    async function loadRuns() {
        runs = [];
        clearOutput();
        ui.details.textContent = "";
        for (const name of ["run-select", "baseline", "target"]) {
            fillSelect(ui[name], [], runLabel, "Select a run");
        }

        const selected = requisitions.find(
            (item) => item.id === ui.select.value,
        );
        if (!selected) return;

        ui.details.textContent = [
            selected.topic,
            `Requisition: ${artemisReference(selected)}`,
            `Risk Tracker case: ${selected.risk_tracker_case_id ?? "None"}`,
            "",
            "Research questions:",
            ...(selected.questions || []).map((question) => `- ${question}`),
        ].join("\n");

        runs = await allPages(`${requisitionUrl()}/runs`);
        fillSelect(ui["run-select"], runs, runLabel, "Select a saved run");

        const completed = runs.filter((run) => run.status === "completed");
        fillSelect(ui.baseline, completed, runLabel, "Select baseline");
        fillSelect(ui.target, completed, runLabel, "Select target");

        if (runs.length) ui["run-select"].value = runs[0].id;
        if (completed.length) ui.target.value = completed[0].id;
        if (completed.length > 1) ui.baseline.value = completed[1].id;

        ui.status.textContent = `${runs.length} saved run(s).`;
    }

    async function loadArchive(preferredId = ui.select.value) {
        requisitions = await allPages("/research/requisitions");
        fillSelect(
            ui.select,
            requisitions,
            (item) => `${artemisReference(item)} | ${item.topic}`,
            "Select a requisition",
        );

        if (requisitions.some((item) => item.id === preferredId)) {
            ui.select.value = preferredId;
        } else if (requisitions.length) {
            ui.select.value = requisitions[0].id;
        }
        await loadRuns();
        if (!requisitions.length) {
            ui.status.textContent = "No saved requisitions yet.";
        }
    }

    function displayRun(run) {
        clearOutput();
        ui["output-title"].textContent = `${artemisReference(run)} | ${run.status}`;

        const result = run.result;
        if (!result) {
            ui.output.textContent =
                run.error || "This run has no saved result yet.";
        } else {
            renderSources(
                ui.sources,
                result.sources || [],
                run.request?.risk_tracker_case_id,
            );
            ui.output.textContent = typeof result.brief === "string"
                ? result.brief
                : JSON.stringify(result, null, 2);
        }

        ui.status.textContent =
            `Run ${artemisReference(run)} | ${run.status}` +
            (run.error ? ` | ${run.error}` : "");

        if (result) {
            const warnings = [];
            if (result.event_extraction_error) {
                warnings.push("event extraction");
            }
            if (result.synthesis_error) warnings.push("brief synthesis");
            if (result.collection_errors?.length) warnings.push("collection");
            if ((result.sources || []).some((source) =>
                source.risk_tracker_error || source.intel_note_error
            )) warnings.push("source saving");
            if ((result.event_notes || []).some((note) =>
                note.error || note.risk_tracker_link_error
            )) warnings.push("event-note saving or linking");

            if (warnings.length) {
                ui.status.textContent +=
                    ` | Warnings: ${warnings.join(", ")}. Review the brief.`;
            }
        }
    }

    function pendingId(requisitionId) {
        const key = `artemis-pending-run:${requisitionId}`;
        let id = pendingRequests.get(key);
        try {
            id = id || sessionStorage.getItem(key);
        } catch {
            // In-memory tracking works when storage is unavailable.
        }
        if (!id) id = crypto.randomUUID();
        pendingRequests.set(key, id);
        try {
            sessionStorage.setItem(key, id);
        } catch {
            // Keep the in-memory request ID.
        }
        return id;
    }

    function clearPending(requisitionId) {
        const key = `artemis-pending-run:${requisitionId}`;
        pendingRequests.delete(key);
        try {
            sessionStorage.removeItem(key);
        } catch {
            // Storage may be disabled.
        }
    }

    function comparisonText(data) {
        const lines = [
            "RESEARCH RUN COMPARISON",
            `Baseline: ${artemisReference(data.baseline)}`,
            `Target: ${artemisReference(data.target)}`,
            `Research request changed: ${data.request_changed ? "Yes" : "No"}`,
            `Brief text changed: ${data.brief_changed ? "Yes" : "No"}`,
        ];

        function records(title, items) {
            lines.push("", title);
            if (!items?.length) {
                lines.push("- None");
                return;
            }
            for (const item of items) {
                lines.push(`- ${item.title || "Untitled record"}`);
                if (item.url) lines.push(`  ${item.url}`);
                if (item.intel_note_id) {
                    lines.push(`  Intel Note ${item.intel_note_id}`);
                }
            }
        }

        for (const [key, title] of [
            ["sources", "SOURCES"],
            ["event_notes", "RECORDED EVENT NOTES"],
        ]) {
            const changes = data[key];
            lines.push("", title);
            records("Added to target run", changes.added);
            records("Absent from target run", changes.absent_from_target);
            lines.push("", `Changed records: ${changes.changed.length}`);
            for (const change of changes.changed) {
                lines.push(
                    JSON.stringify(change, null, 2),
                );
            }
            lines.push(`Unchanged records: ${changes.unchanged_count}`);
        }

        lines.push("", "REPORTED COLLECTION NEEDS");
        const needs = data.collection_needs;
        if (!needs.available) {
            lines.push(needs.reason);
        } else {
            for (const [key, label] of [
                ["newly_reported", "Newly reported"],
                ["no_longer_reported", "No longer reported"],
                ["still_reported", "Still reported"],
            ]) {
                lines.push("", label);
                const items = needs[key] || [];
                lines.push(...(items.length
                    ? items.map((item) => `- ${item}`)
                    : ["- None"]));
            }
        }

        lines.push(
            "",
            "RUN WARNINGS",
            JSON.stringify(data.warnings, null, 2),
            "",
            "COMPARISON LIMITS",
            ...(data.comparison_limits || []).map((item) => `- ${item}`),
        );
        return lines.join("\n");
    }

    ui["create-form"].addEventListener("submit", (event) => {
        event.preventDefault();
        action(async () => {
            const topic = ui.topic.value.trim();
            const questions = ui.questions.value.split(/\r?\n/)
                .map((line) => line.trim()).filter(Boolean);

            if (!topic) throw new Error("Enter a research topic.");
            if (questions.length > 10) {
                throw new Error("Enter no more than 10 research questions.");
            }
            if (questions.some((question) => question.length > 500)) {
                throw new Error("Each research question must be 500 characters or fewer.");
            }

            ui.status.textContent = "Saving requisition...";
            const record = await requestJson("/research/requisitions", {
                method: "POST",
                body: JSON.stringify({
                    topic,
                    questions,
                    risk_tracker_case_id: optionalPositiveInteger(ui.case),
                }),
            });

            ui["create-form"].reset();
            await loadArchive(record.id);
            ui.status.textContent = "Requisition saved. Ready to run.";
        });
    });

    ui.refresh.addEventListener("click", () => action(() => loadArchive()));
    ui.select.addEventListener("change", () => action(() => loadRuns()));

    ui.open.addEventListener("click", () => action(async () => {
        const runId = ui["run-select"].value;
        if (!runId) throw new Error("Select a saved run.");
        ui.status.textContent = "Loading saved result...";
        const run = await requestJson(
            `${requisitionUrl()}/runs/${encodeURIComponent(runId)}`,
        );
        displayRun(run);
    }));

    ui.run.addEventListener("click", () => action(async () => {
        const url = requisitionUrl();
        const requisitionId = ui.select.value;
        const requestId = pendingId(requisitionId);

        clearOutput();
        ui.status.textContent =
            "Research is running. Keep this page open while Artemis collects " +
            "evidence and saves the result. This may take several minutes.";

        let run;
        try {
            run = await requestJson(`${url}/runs`, {
                method: "POST",
                body: JSON.stringify({ request_id: requestId }),
            });
        } catch (error) {
            throw new Error(
                `${error.message} Refresh the archive to check saved runs. ` +
                "Clicking Run again will reuse this request ID to avoid " +
                "duplicating an uncertain submission.",
            );
        }

        if (["completed", "failed", "interrupted"].includes(run.status)) {
            clearPending(requisitionId);
        }

        await loadRuns();
        ui["run-select"].value = run.id;
        displayRun(run);
    }));

    ui.compare.addEventListener("click", () => action(async () => {
        const baseline = ui.baseline.value;
        const target = ui.target.value;
        if (!baseline || !target) {
            throw new Error("Select a baseline and a target run.");
        }
        if (baseline === target) {
            throw new Error("Choose two different runs.");
        }

        ui.status.textContent = "Comparing saved runs...";
        const query = new URLSearchParams({
            baseline_run_id: baseline,
            target_run_id: target,
        });
        const data = await requestJson(
            `${requisitionUrl()}/compare?${query}`,
        );

        clearOutput();
        ui["output-title"].textContent = "Saved run comparison";
        ui.output.textContent = comparisonText(data);
        ui.status.textContent =
            "Comparison ready. Added records may describe historical events.";
    }));

    action(() => loadArchive());
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
    elements.messageForm.addEventListener("submit", handleMessageSubmit);

    elements.messageInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && event.ctrlKey) {
            event.preventDefault();
            elements.messageForm.requestSubmit();
        }
    });

    for (const [checkbox, options] of [
        [elements.useIntelNotes, elements.intelNotesOptions],
        [elements.useRiskTracker, elements.riskTrackerOptions],
        [elements.useMarketLens, elements.marketLensOptions],
    ]) {
        const update = () => {
            options.classList.toggle("hidden", !checkbox.checked);
        };
        checkbox.addEventListener("change", update);
        update();
    }

    bindNoteForm();
    bindResearchForm();
    bindRequisitions();
}

async function initialize() {
    bindEvents();
    await Promise.all([
        refreshSystemStatus(),
        loadConversations(true),
    ]);
}

initialize().catch((error) => {
    showNotification(`Dashboard initialization failed: ${error.message}`, true);
});