"use strict";

(() => {
    const requisitionSelect = document.getElementById("req-select");
    const requisitionPanel = document.getElementById("research-requisitions");

    if (!requisitionSelect || !requisitionPanel) return;

    const section = document.createElement("section");
    section.style.cssText =
        "margin-top:24px;padding-top:16px;border-top:1px solid #49345f";

    // Static markup only; saved data is displayed using textContent.
    section.innerHTML = `
        <h3>Scheduled monitoring</h3>
        <p>
            Automatically research the selected requisition while Artemis
            and its supporting services are running.
        </p>

        <fieldset id="schedule-controls"
            style="border:0;padding:0;margin:0;min-width:0">
            <label for="schedule-interval">Research interval</label>
            <select id="schedule-interval" style="width:100%">
                <option value="5">Every 5 minutes — testing</option>
                <option value="60">Every hour</option>
                <option value="360">Every 6 hours</option>
                <option value="720">Every 12 hours</option>
                <option value="1440" selected>Every day</option>
                <option value="10080">Every week</option>
            </select>

            <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:12px">
                <button id="schedule-enable" type="button">
                    Enable / update schedule
                </button>
                <button id="schedule-pause" type="button">
                    Pause monitoring
                </button>
                <button id="schedule-refresh" type="button">
                    Refresh schedule status
                </button>
            </div>
        </fieldset>

        <p>
            Enabling starts a fresh interval. Pausing prevents future starts;
            an active run can finish. Missed intervals do not create a backlog.
        </p>

        <p id="schedule-message" role="status" aria-live="polite"></p>

        <pre id="schedule-status"
            style="white-space:pre-wrap;overflow-wrap:anywhere"></pre>

        <details>
            <summary>Latest automatic comparison</summary>
            <pre id="schedule-comparison"
                style="white-space:pre-wrap;overflow-wrap:anywhere;
                       max-height:500px;overflow:auto"></pre>
        </details>
    `;

    requisitionPanel.append(section);

    const controls = document.getElementById("schedule-controls");
    const interval = document.getElementById("schedule-interval");
    const message = document.getElementById("schedule-message");
    const status = document.getElementById("schedule-status");
    const comparison = document.getElementById("schedule-comparison");

    let currentSchedule = null;
    let busy = false;
    let revision = 0;

    async function api(url, options = {}) {
        const response = await fetch(url, {
            ...options,
            headers: {
                "Content-Type": "application/json",
                ...(options.headers || {}),
            },
        });

        if (!response.ok) {
            let detail = `Request failed with HTTP ${response.status}.`;
            try {
                const body = await response.json();
                if (typeof body.detail === "string") {
                    detail = body.detail;
                } else if (Array.isArray(body.detail)) {
                    detail = body.detail.map((item) => item.msg).join("; ");
                }
            } catch {
                // Keep the status-based message.
            }
            throw new Error(detail);
        }

        return response.json();
    }

    function url(id) {
        return `/research/requisitions/${encodeURIComponent(id)}/schedule`;
    }

    function date(value) {
        if (!value) return "None";
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime())
            ? value
            : parsed.toLocaleString();
    }

    function display(schedule) {
        currentSchedule = schedule;

        const value = String(schedule.interval_minutes);
        if (![...interval.options].some((option) => option.value === value)) {
            interval.add(new Option(`Every ${value} minutes`, value));
        }
        interval.value = value;

        status.textContent = [
            `Monitoring: ${schedule.enabled ? "Enabled" : "Paused"}`,
            `Interval: ${schedule.interval_minutes} minutes`,
            `Next scheduled attempt: ${date(schedule.next_run_at)}`,
            `Last attempt started: ${date(schedule.last_started_at)}`,
            `Last attempt finished: ${date(schedule.last_finished_at)}`,
            `Last status: ${schedule.last_status || "Not run yet"}`,
            `Last run ID: ${schedule.last_run_id || "None"}`,
            `Last error: ${schedule.last_error || "None"}`,
        ].join("\n");

        const saved = schedule.comparison;
        if (!saved) {
            comparison.textContent =
                "No automatic comparison is available for the latest attempt.";
            return;
        }

        comparison.textContent = [
            `Baseline: ${saved.baseline.id}`,
            `Target: ${saved.target.id}`,
            "",
            `Sources added: ${saved.sources.added.length}`,
            `Sources absent from target: ${saved.sources.absent_from_target.length}`,
            `Source records changed: ${saved.sources.changed.length}`,
            "",
            `Event notes added: ${saved.event_notes.added.length}`,
            `Event notes absent from target: ${saved.event_notes.absent_from_target.length}`,
            "",
            "Full comparison:",
            JSON.stringify(saved, null, 2),
        ].join("\n");
    }

    async function refresh() {
        const version = ++revision;
        const id = requisitionSelect.value;

        currentSchedule = null;
        status.textContent = "";
        comparison.textContent = "";
        controls.disabled = true;

        if (!id) {
            message.textContent = "Select a saved requisition to manage monitoring.";
            return;
        }

        message.textContent = "Loading schedule...";

        try {
            const schedule = await api(url(id));
            if (version !== revision || id !== requisitionSelect.value) return;

            display(schedule);
            message.textContent = "Schedule status loaded.";
        } catch (error) {
            if (version !== revision || id !== requisitionSelect.value) return;
            message.textContent = error.message;
        } finally {
            if (version === revision) controls.disabled = busy;
        }
    }

    async function save(enabled) {
        if (busy) return;

        const id = requisitionSelect.value;
        if (!id) {
            message.textContent = "Select a saved requisition first.";
            return;
        }

        const minutes = enabled
            ? Number(interval.value)
            : currentSchedule?.interval_minutes || Number(interval.value);

        busy = true;
        const version = ++revision;
        controls.disabled = true;
        message.textContent = enabled
            ? "Enabling scheduled research..."
            : "Pausing scheduled research...";

        try {
            const schedule = await api(url(id), {
                method: "PUT",
                body: JSON.stringify({
                    enabled,
                    interval_minutes: minutes,
                }),
            });

            if (version !== revision || id !== requisitionSelect.value) return;

            display(schedule);
            message.textContent = enabled
                ? "Monitoring enabled. The next attempt is shown below."
                : "Monitoring paused. An active run can still finish.";
        } catch (error) {
            if (version !== revision || id !== requisitionSelect.value) return;
            message.textContent =
                `${error.message} Refresh schedule status to check whether it saved.`;
        } finally {
            busy = false;
            if (version === revision) controls.disabled = false;
        }
    }

    document.getElementById("schedule-enable").addEventListener(
        "click",
        () => save(true),
    );
    document.getElementById("schedule-pause").addEventListener(
        "click",
        () => save(false),
    );
    document.getElementById("schedule-refresh").addEventListener(
        "click",
        refresh,
    );
    requisitionSelect.addEventListener("change", refresh);

    // The archive loads asynchronously and can replace the selection.
    const observer = new MutationObserver(() => refresh());
    observer.observe(requisitionSelect, { childList: true });

    refresh();
})();