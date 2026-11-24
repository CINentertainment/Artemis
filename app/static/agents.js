(() => {
    "use strict";

    function initialize() {
        const chatForm = document.getElementById("message-form");
        if (!chatForm || document.getElementById("agent-missions")) return;

        const panel = document.createElement("details");
        panel.id = "agent-missions";
        panel.innerHTML = `
            <summary>Agent Missions</summary>
            <p>Assign background research and review saved worker results.</p>

            <form data-agent-form>
                <label>
                    Mission type
                    <select name="mission_type">
                        <option value="research">Web research and briefing</option>
                        <option value="market">Web research, Market Lens, and briefing</option>
                    </select>
                </label>

                <label>
                    Assignment
                    <textarea name="assignment" maxlength="300" required
                        placeholder="Describe the research assignment..."
                        rows="3"></textarea>
                </label>

                <button type="submit">Deploy mission</button>
            </form>

            <p data-agent-status role="status" aria-live="polite"></p>

            <button type="button" data-agent-refresh>Refresh missions</button>
            <p>Recent missions</p>
            <div data-agent-list></div>

            <section data-agent-report hidden>
                <h3>Mission report</h3>
                <div data-agent-detail></div>
            </section>
        `;

        chatForm.before(panel);

        const form = panel.querySelector("[data-agent-form]");
        const assignment = form.elements.assignment;
        const missionType = form.elements.mission_type;
        const deploy = form.querySelector("button");
        const status = panel.querySelector("[data-agent-status]");
        const list = panel.querySelector("[data-agent-list]");
        const report = panel.querySelector("[data-agent-report]");
        const detail = panel.querySelector("[data-agent-detail]");
        const refreshButton = panel.querySelector("[data-agent-refresh]");

        let selectedId = null;
        let loading = false;
        let submitting = false;
        let pendingRequest = null;

        function textElement(tag, text) {
            const element = document.createElement(tag);
            element.textContent = text;
            return element;
        }

        function dateLabel(value) {
            if (!value) return "Not recorded";
            const date = new Date(value);
            return Number.isNaN(date.getTime())
                ? String(value)
                : date.toLocaleString();
        }

        async function api(path, options = {}) {
            const response = await fetch(path, {
                cache: "no-store",
                ...options,
            });

            const data = await response.json().catch(() => null);

            if (!response.ok) {
                const message = typeof data?.detail === "string"
                    ? data.detail
                    : `Request failed (${response.status}).`;
                throw new Error(message);
            }

            return data;
        }

        function active(mission) {
            return ["queued", "running"].includes(mission.status);
        }

        function showReport(mission) {
            panel.dispatchEvent(new CustomEvent("artemis:mission-opened", {
                detail: mission,
            }));
            detail.replaceChildren();
            report.hidden = false;

            detail.append(
                textElement("h4", artemisReference(mission)),
                textElement("p", mission.display_title || mission.assignment),
                textElement("p", `Status: ${mission.status}`),
                textElement("p", `Created: ${dateLabel(mission.created_at)}`),
                textElement("p", `Finished: ${dateLabel(mission.finished_at)}`)
            );

            if (mission.cancel_requested && active(mission)) {
                detail.append(textElement(
                    "p",
                    "Cancellation requested. The current operation may finish before the worker stops."
                ));
            }

            if (mission.error) {
                detail.append(textElement("p", `Mission notice: ${mission.error}`));
            }

            for (const step of mission.steps || []) {
                let stepStatus = step.status;

                if (!active(mission) && ["queued", "running"].includes(stepStatus)) {
                    stepStatus = "unfinished";
                }

                detail.append(textElement(
                    "h4",
                    `${step.reference || "Step"} | ${step.agent} | ${stepStatus}`
                ));

                if (step.agent === "briefing" && step.result?.brief) {
                    const brief = textElement("div", step.result.brief);
                    brief.style.whiteSpace = "pre-wrap";
                    brief.style.overflowWrap = "anywhere";
                    detail.append(brief);
                }

                if (step.result != null) {
                    const records = document.createElement("details");
                    records.append(textElement(
                        "summary",
                        step.agent === "briefing"
                            ? "Briefing worker record"
                            : "Worker evidence and notices"
                    ));

                    const pre = textElement(
                        "pre",
                        JSON.stringify(step.result, null, 2)
                    );
                    pre.style.whiteSpace = "pre-wrap";
                    pre.style.overflowWrap = "anywhere";
                    records.append(pre);
                    detail.append(records);
                }
            }

            detail.append(textElement(
                "p",
                "Completed means the workflow finished. Review source coverage and notices. Retrieved time does not establish the date of stored market prices."
            ));
        }

        async function openMission(id) {
            selectedId = id;
            try {
                const mission = await api(
                    `/agents/missions/${encodeURIComponent(id)}`
                );
                if (selectedId === id) showReport(mission);
            } catch (error) {
                status.textContent = error.message;
            }
        }

        async function cancelMission(id, button) {
            button.disabled = true;
            try {
                const mission = await api(
                    `/agents/missions/${encodeURIComponent(id)}/cancel`,
                    { method: "POST" }
                );

                status.textContent = active(mission)
                    ? "Cancellation requested. Waiting for the current operation to finish."
                    : `Mission status: ${mission.status}`;

                if (selectedId === id) showReport(mission);
                await refresh();
            } catch (error) {
                status.textContent = error.message;
            } finally {
                button.disabled = false;
            }
        }

        async function refresh() {
            if (loading) return;
            loading = true;
            refreshButton.disabled = true;

            try {
                const data = await api("/agents/missions?limit=20");
                const missions = Array.isArray(data)
                    ? data
                    : data?.missions ?? data?.items;

                if (!Array.isArray(missions)) {
                    throw new Error("Unexpected mission-list response.");
                }

                list.replaceChildren();

                if (!missions.length) {
                    list.append(textElement("p", "No missions saved yet."));
                }

                for (const mission of missions) {
                    const card = document.createElement("article");
                    card.style.padding = "14px 0";
                    card.style.borderBottom = "1px solid #49325f";

                    card.append(
                        textElement("strong", artemisReference(mission)),
                        textElement("p", mission.display_title || mission.assignment),
                        textElement(
                            "p",
                            `${mission.mission_type} · ${mission.status} · ${dateLabel(mission.created_at)}`
                        ),
                        textElement(
                            "p",
                            (mission.steps || []).map(step =>
                                `${step.agent}: ${
                                    !active(mission) &&
                                    ["queued", "running"].includes(step.status)
                                        ? "unfinished"
                                        : step.status
                                }`
                            ).join(" → ")
                        )
                    );

                    const open = textElement("button", "Open report");
                    open.type = "button";
                    open.addEventListener("click", () => openMission(mission.id));
                    card.append(open);

                    if (active(mission)) {
                        const cancel = textElement(
                            "button",
                            mission.cancel_requested
                                ? "Cancellation requested"
                                : "Cancel mission"
                        );
                        cancel.type = "button";
                        cancel.disabled = Boolean(mission.cancel_requested);
                        cancel.style.marginLeft = "8px";
                        cancel.addEventListener("click", () =>
                            cancelMission(mission.id, cancel)
                        );
                        card.append(cancel);
                    }

                    list.append(card);
                }

                if (selectedId) await openMission(selectedId);
            } catch (error) {
                status.textContent = error.message;
            } finally {
                loading = false;
                refreshButton.disabled = false;
            }
        }

        form.addEventListener("submit", async event => {
            event.preventDefault();
            event.stopPropagation();
            if (submitting) return;

            const text = assignment.value.trim();
            if (!text) {
                assignment.focus();
                return;
            }

            const type = missionType.value;

            // Retain the same request ID when retrying an uncertain submission.
            if (
                !pendingRequest ||
                pendingRequest.assignment !== text ||
                pendingRequest.mission_type !== type
            ) {
                pendingRequest = {
                    request_id: crypto.randomUUID(),
                    mission_type: type,
                    assignment: text,
                };
            }

            submitting = true;
            deploy.disabled = true;
            assignment.disabled = true;
            missionType.disabled = true;
            status.textContent = "Submitting mission...";

            try {
                const mission = await api("/agents/missions", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(pendingRequest),
                });

                selectedId = mission.id;
                pendingRequest = null;
                assignment.value = "";
                status.textContent = `${artemisReference(mission)} saved: ${mission.status}.`;
                showReport(mission);
                await refresh();
            } catch (error) {
                status.textContent =
                    `${error.message} Your assignment and request ID have been retained for retry.`;
            } finally {
                submitting = false;
                deploy.disabled = false;
                assignment.disabled = false;
                missionType.disabled = false;
            }
        });

        refreshButton.addEventListener("click", refresh);
        panel.addEventListener("toggle", () => {
            if (panel.open) refresh();
        });

        setInterval(() => {
            if (panel.open && !document.hidden) refresh();
        }, 5000);

        refresh();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initialize, { once: true });
    } else {
        initialize();
    }
})();