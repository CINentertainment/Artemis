(() => {
    "use strict";

    function initialize() {
        const panel = document.getElementById("agent-missions");
        const report = panel?.querySelector("[data-agent-report]");
        if (!report || panel.querySelector("[data-agent-exports]")) return;

        let currentMission = null;

        const controls = document.createElement("div");
        controls.dataset.agentExports = "";
        controls.style.cssText =
            "display:flex;flex-wrap:wrap;gap:10px;margin:16px 0";

        const pdfButton = document.createElement("button");
        pdfButton.type = "button";
        pdfButton.textContent = "Export PDF";
        pdfButton.disabled = true;

        const jsonButton = document.createElement("button");
        jsonButton.type = "button";
        jsonButton.textContent = "Export JSON";
        jsonButton.disabled = true;

        const message = document.createElement("p");
        message.setAttribute("role", "status");
        message.setAttribute("aria-live", "polite");

        controls.append(pdfButton, jsonButton);
        report.querySelector("h3").after(controls, message);

        panel.addEventListener("artemis:mission-opened", event => {
            currentMission = JSON.parse(JSON.stringify(event.detail));

            const running = ["queued", "running"].includes(
                currentMission.status
            );

            pdfButton.disabled = running;
            jsonButton.disabled = running;

            message.textContent = running
                ? "Exports become available when this mission stops running."
                : "Export the brief and evidence as PDF, or the full record as JSON.";
        });

        function reference(mission) {
            return mission.reference || mission.id || "Artemis-mission";
        }

        function filename(mission) {
            return reference(mission).replace(/[^a-zA-Z0-9_-]/g, "-");
        }

        function date(value) {
            if (!value) return "Not recorded";
            const parsed = new Date(value);

            return Number.isNaN(parsed.getTime())
                ? String(value)
                : parsed.toLocaleString();
        }

        function downloadJson(mission) {
            const blob = new Blob(
                [JSON.stringify(mission, null, 2)],
                { type: "application/json;charset=utf-8" }
            );

            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url;
            link.download = `${filename(mission)}.json`;

            document.body.append(link);
            link.click();
            link.remove();

            window.setTimeout(() => URL.revokeObjectURL(url), 10000);
        }

        function printableReport(mission) {
            // Open synchronously from the button click to avoid popup blocking.
            const printWindow = window.open("", "_blank");

            if (!printWindow) {
                throw new Error(
                    "The print window was blocked. Allow pop-ups for Artemis and try again."
                );
            }

            printWindow.opener = null;

            try {
                const doc = printWindow.document;
                doc.documentElement.lang = "en";
                doc.title = `${filename(mission)} - Artemis Mission Report`;

                const charset = doc.createElement("meta");
                charset.charset = "utf-8";

                const style = doc.createElement("style");
                style.textContent = `
                    @page {
                        size: A4;
                        margin: 18mm;
                    }
                    body {
                        margin: 0 auto;
                        padding: 32px;
                        max-width: 900px;
                        color: #17121f;
                        background: white;
                        font: 15px/1.6 Arial, sans-serif;
                    }
                    h1 {
                        font-size: 28px;
                        color: #56327d;
                    }
                    h2 {
                        margin-top: 30px;
                        font-size: 21px;
                        border-bottom: 1px solid #ccc;
                        padding-bottom: 8px;
                    }
                    h3 {
                        margin-top: 24px;
                        font-size: 17px;
                    }
                    h1, h2, h3 {
                        break-after: avoid;
                    }
                    p, li, pre, a {
                        overflow-wrap: anywhere;
                    }
                    pre {
                        white-space: pre-wrap;
                        font: 12px/1.5 Consolas, monospace;
                    }
                    .brief {
                        white-space: pre-wrap;
                        font: inherit;
                    }
                    .notice {
                        border-left: 3px solid #775398;
                        padding-left: 14px;
                        color: #49404f;
                    }
                    .appendix {
                        break-before: page;
                    }
                    a {
                        color: #56327d;
                    }
                    button {
                        padding: 12px 20px;
                        margin-bottom: 20px;
                        cursor: pointer;
                    }
                    @media print {
                        body {
                            padding: 0;
                            max-width: none;
                        }
                        .screen-only {
                            display: none;
                        }
                    }
                `;

                doc.head.append(charset, style);
                doc.body.replaceChildren();

                function add(tag, text, className) {
                    const element = doc.createElement(tag);
                    element.textContent = text;
                    if (className) element.className = className;
                    doc.body.append(element);
                    return element;
                }

                const printAgain = add(
                    "button",
                    "Print / Save as PDF",
                    "screen-only"
                );
                printAgain.type = "button";
                printAgain.addEventListener("click", () =>
                    printWindow.print()
                );

                add("h1", "Artemis Mission Report");
                add("h2", reference(mission));
                add("p", `Assignment: ${mission.assignment}`);
                add("p", `Type: ${mission.mission_type}`);
                add("p", `Status: ${mission.status}`);
                add("p", `Created: ${date(mission.created_at)}`);
                add("p", `Started: ${date(mission.started_at)}`);
                add("p", `Finished: ${date(mission.finished_at)}`);
                add("p", `Exported: ${new Date().toLocaleString()}`);

                if (mission.cancel_requested) {
                    add("p", "Cancellation was requested for this mission.");
                }

                if (mission.error) {
                    add("p", `Mission notice: ${mission.error}`, "notice");
                }

                add(
                    "p",
                    "This report contains model-generated analysis and collected reference material. Workflow completion does not verify every claim. Retrieval time does not establish publication time or market-data freshness.",
                    "notice"
                );

                add("h2", "Research brief");

                const briefs = (mission.steps || []).filter(step =>
                    step.agent === "briefing" &&
                    typeof step.result?.brief === "string"
                );

                if (briefs.length) {
                    for (const step of briefs) {
                        add("div", step.result.brief, "brief");
                    }
                } else {
                    add("p", "No generated briefing was saved for this mission.");
                }

                add("h2", "Collected sources");

                const seen = new Set();
                let sourceCount = 0;

                for (const step of mission.steps || []) {
                    const output = step.result?.output || step.result;

                    for (const source of output?.sources || []) {
                        if (!source.url || seen.has(source.url)) continue;
                        seen.add(source.url);
                        sourceCount += 1;

                        add("h3", source.title || "Untitled source");

                        let url;
                        try {
                            url = new URL(source.url);
                        } catch {
                            // Invalid URLs remain text.
                        }

                        if (url && ["https:", "http:"].includes(url.protocol)) {
                            const link = add("a", source.url);
                            link.href = url.href;
                            link.rel = "noreferrer";
                        } else {
                            add("p", String(source.url));
                        }

                        for (const excerpt of source.excerpts || []) {
                            add("p", String(excerpt));
                        }
                    }
                }

                if (!sourceCount) {
                    add("p", "No collected web sources were saved.");
                }

                add(
                    "h2",
                    "Execution history and supporting records",
                    "appendix"
                );

                add(
                    "p",
                    "The following records preserve planning decisions, worker assignments, retrieved analytics, errors, and limitations."
                );

                for (const [index, step] of (mission.steps || []).entries()) {
                    const stepReference =
                        step.reference ||
                        `${reference(mission)}-S${String(index + 1).padStart(2, "0")}`;

                    const stepStatus = ["queued", "running"].includes(step.status)
                        ? "unfinished"
                        : step.status;

                    add(
                        "h3",
                        `${stepReference} | ${step.agent} | ${stepStatus}`
                    );

                    // Brief text is printed above; keep other briefing metadata.
                    if (
                        step.agent === "briefing" &&
                        step.result &&
                        typeof step.result === "object"
                    ) {
                        const { brief, ...metadata } = step.result;
                        add("pre", JSON.stringify(metadata, null, 2));
                    } else {
                        add(
                            "pre",
                            JSON.stringify(step.result ?? null, null, 2)
                        );
                    }
                }

                printWindow.focus();

                // Allow the newly built document to finish laying out.
                printWindow.setTimeout(() => {
                    if (!printWindow.closed) printWindow.print();
                }, 300);
            } catch (error) {
                printWindow.close();
                throw error;
            }
        }

        pdfButton.addEventListener("click", () => {
            if (!currentMission) return;

            try {
                printableReport(currentMission);
                message.textContent =
                    "Print view opened. Choose Save as PDF in the print dialog.";
            } catch (error) {
                message.textContent = error.message;
            }
        });

        jsonButton.addEventListener("click", () => {
            if (!currentMission) return;

            try {
                downloadJson(currentMission);
                message.textContent =
                    "JSON download requested for the complete saved mission.";
            } catch (error) {
                message.textContent = error.message;
            }
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initialize, { once: true });
    } else {
        initialize();
    }
})();