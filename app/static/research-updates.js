"use strict";

(() => {
    const host = document.querySelector(".command-console");
    if (!host) return;

    const panel = document.createElement("details");
    panel.className = "source-options";
    panel.id = "research-updates-inbox";

    // Static interface markup. Retrieved content uses textContent.
    panel.innerHTML = `
        <summary id="updates-heading">Research updates</summary>

        <div style="padding-top:16px">
            <fieldset id="updates-controls"
                style="border:0;padding:0;margin:0;min-width:0">
                <label>
                    <input id="updates-unread" type="checkbox" checked>
                    Show unread only
                </label>

                <div style="display:flex;flex-wrap:wrap;gap:8px;margin:12px 0">
                    <button id="updates-refresh" type="button">Refresh inbox</button>
                    <button id="updates-previous" type="button">Previous</button>
                    <button id="updates-next" type="button">Next</button>
                </div>

                <p id="updates-page"></p>

                <label for="updates-select">Saved research updates</label>
                <select id="updates-select" style="width:100%"></select>

                <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:12px">
                    <button id="updates-open" type="button">Open update</button>
                    <button id="updates-reviewed" type="button">
                        Mark reviewed
                    </button>
                    <button id="updates-mark-unread" type="button">
                        Mark unread
                    </button>
                </div>
            </fieldset>

            <p id="updates-status" role="status" aria-live="polite"></p>
            <p>Opening an update does not mark it reviewed.</p>

            <pre id="updates-details"
                style="white-space:pre-wrap;overflow-wrap:anywhere"></pre>

            <h3>What changed?</h3>
            <pre id="updates-changes" tabindex="0"
                style="white-space:pre-wrap;overflow-wrap:anywhere;
                       max-height:500px;overflow:auto"></pre>

            <h3>Research brief</h3>
            <pre id="updates-brief" tabindex="0"
                style="white-space:pre-wrap;overflow-wrap:anywhere;
                       max-height:650px;overflow:auto"></pre>

            <details>
                <summary>Run warnings</summary>
                <pre id="updates-warnings"
                    style="white-space:pre-wrap;overflow-wrap:anywhere"></pre>
            </details>
        </div>
    `;
    host.prepend(panel);

    const ui = {};
    for (const name of [
        "heading", "controls", "unread", "refresh", "previous",
        "next", "page", "select", "open", "reviewed",
        "mark-unread", "status", "details", "changes",
        "brief", "warnings",
    ]) {
        ui[name] = document.getElementById(`updates-${name}`);
    }

    const limit = 20;
    let offset = 0;
    let total = 0;
    let busy = false;
    let openedRunId = null;

    async function api(url, options = {}) {
        const response = await fetch(url, {
            ...options,
            headers: {
                "Content-Type": "application/json",
                ...(options.headers || {}),
            },
        });

        if (!response.ok) {
            let message = `Request failed with HTTP ${response.status}.`;
            try {
                const body = await response.json();
                if (typeof body.detail === "string") {
                    message = body.detail;
                }
            } catch {
                // Keep the HTTP status message.
            }
            throw new Error(message);
        }

        return response.json();
    }

    function date(value) {
        if (!value) return "Unknown";

        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime())
            ? value
            : parsed.toLocaleString();
    }

    function updateButtons() {
        ui.previous.disabled = offset === 0;
        ui.next.disabled = offset + limit >= total;

        const selected = Boolean(ui.select.value);
        ui.open.disabled = !selected;
        ui.reviewed.disabled = !selected;
        ui["mark-unread"].disabled = !selected;
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
            updateButtons();
        }
    }

    async function loadInbox() {
        const previousId = ui.select.value;
        const query = new URLSearchParams({
            unread_only: String(ui.unread.checked),
            limit: String(limit),
            offset: String(offset),
        });

        const data = await api(`/research/updates?${query}`);
        total = data.total_count;

        if (offset > 0 && offset >= total) {
            offset = Math.max(
                0,
                Math.floor((total - 1) / limit) * limit,
            );
            return loadInbox();
        }

        ui.heading.textContent =
            `Research updates — ${data.unread_count} unread`;

        ui.select.replaceChildren(
            new Option("Select a research update", ""),
        );

        for (const item of data.items) {
            const label = [
                item.reviewed ? "Reviewed" : "Unread",
                item.topic,
                item.status,
                date(item.finished_at || item.started_at),
                artemisReference(item),
            ].join(" | ");

            ui.select.add(new Option(label, item.run_id));
        }

        if (data.items.some((item) => item.run_id === previousId)) {
            ui.select.value = previousId;
        } else if (data.items.length) {
            ui.select.value = data.items[0].run_id;
        }

        ui.page.textContent = total
            ? `Showing ${offset + 1}–${Math.min(offset + limit, total)} of ${total}`
            : "No updates match this view.";

        ui.status.textContent = openedRunId
            ? "Inbox refreshed. The previously opened update remains displayed."
            : "Inbox refreshed. Select an update and click Open update.";

        updateButtons();
    }

    function displayUpdate(item) {
        openedRunId = item.run_id;

        ui.details.textContent = [
            item.topic,
            `Run: ${artemisReference(item)}`,
            `Requisition: ${item.requisition_reference || "Reference unavailable"}`,
            `Status: ${item.status}`,
            `Review state: ${item.reviewed ? "Reviewed" : "Unread"}`,
            `Finished: ${date(item.finished_at)}`,
            `Risk Tracker case: ${item.risk_tracker_case_id ?? "None"}`,
            ...(item.error ? [`Error: ${item.error}`] : []),
            "",
            "Research questions:",
            ...(item.questions || []).map(
                (question) => `- ${question}`,
            ),
        ].join("\n");

        ui.changes.textContent =
            typeof item.changes?.brief === "string"
                ? item.changes.brief
                : "Change summary unavailable. Check that Artemis was restarted with the updated inbox endpoint.";

        const result = item.run?.result;

        ui.brief.textContent = typeof result?.brief === "string"
            ? result.brief
            : item.error || "This update has no saved brief.";

        ui.warnings.textContent = item.warnings
            ? JSON.stringify(item.warnings, null, 2)
            : "No saved warning details.";

        ui.status.textContent =
            "Update opened. Use Mark reviewed when you finish reviewing it.";
    }

    async function openSelected() {
        const id = ui.select.value;
        if (!id) throw new Error("Select an update first.");

        ui.status.textContent = "Loading saved update and comparison...";

        displayUpdate(await api(
            `/research/updates/${encodeURIComponent(id)}`,
        ));
    }

    async function setReviewed(reviewed) {
        const id = ui.select.value;
        if (!id) throw new Error("Select an update first.");

        await api(
            `/research/updates/${encodeURIComponent(id)}/review`,
            {
                method: "PUT",
                body: JSON.stringify({ reviewed }),
            },
        );

        if (openedRunId === id) {
            displayUpdate(await api(
                `/research/updates/${encodeURIComponent(id)}`,
            ));
        }

        await loadInbox();

        ui.status.textContent = reviewed
            ? "Selected update marked reviewed."
            : "Selected update marked unread.";
    }

    ui.refresh.addEventListener(
        "click",
        () => action(loadInbox),
    );

    ui.unread.addEventListener("change", () => action(async () => {
        offset = 0;
        await loadInbox();
    }));

    ui.previous.addEventListener("click", () => action(async () => {
        offset = Math.max(0, offset - limit);
        await loadInbox();
    }));

    ui.next.addEventListener("click", () => action(async () => {
        if (offset + limit < total) offset += limit;
        await loadInbox();
    }));

    ui.open.addEventListener(
        "click",
        () => action(openSelected),
    );

    ui.reviewed.addEventListener(
        "click",
        () => action(() => setReviewed(true)),
    );

    ui["mark-unread"].addEventListener(
        "click",
        () => action(() => setReviewed(false)),
    );

    ui.select.addEventListener("change", () => {
        if (openedRunId && ui.select.value !== openedRunId) {
            ui.status.textContent =
                "Selection changed. Click Open update to display that run.";
        }
        updateButtons();
    });

    window.setInterval(async () => {
        if (busy || document.hidden) return;

        try {
            const data = await api("/research/updates?limit=1");

            if (!busy) {
                ui.heading.textContent =
                    `Research updates — ${data.unread_count} unread`;
            }
        } catch {
            // Explicit refresh reports errors without repeated alerts.
        }
    }, 60000);

    action(loadInbox);
})();