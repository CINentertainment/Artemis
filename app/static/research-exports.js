"use strict";

(() => {
    const controls = document.getElementById("updates-controls");
    const selection = document.getElementById("updates-select");
    const status = document.getElementById("updates-status");

    if (!controls || !selection || !status) return;

    const actions = document.createElement("div");
    actions.style.cssText =
        "display:flex;flex-wrap:wrap;gap:8px;margin-top:12px";

    const pdfButton = document.createElement("button");
    pdfButton.type = "button";
    pdfButton.textContent = "Export PDF";

    const briefButton = document.createElement("button");
    briefButton.type = "button";
    briefButton.textContent = "Download Markdown";

    const dataButton = document.createElement("button");
    dataButton.type = "button";
    dataButton.textContent = "Download research data";

    const description = document.createElement("p");
    description.textContent =
        "Exports use the selected update. Exporting does not mark it reviewed.";

    actions.append(pdfButton, briefButton, dataButton);
    controls.append(actions, description);

    let exporting = false;

    async function getSelectedUpdate() {
        const runId = selection.value;

        if (!runId) {
            throw new Error("Select a research update first.");
        }

        const response = await fetch(
            `/research/updates/${encodeURIComponent(runId)}`,
        );

        if (!response.ok) {
            let message = `Export failed with HTTP ${response.status}.`;

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

    function filename(update) {
        const topic = String(update.topic || "research")
            .normalize("NFKD")
            .replace(/[^a-zA-Z0-9]+/g, "-")
            .replace(/^-+|-+$/g, "")
            .slice(0, 70) || "research";

        return `artemis-${topic}-${update.run_id}`;
    }

    function buildBrief(update, exportedAt) {
        const result = update.run?.result;
        const lines = [
            "# Artemis research update",
            "",
            `Topic: ${update.topic}`,
            `Run ID: ${update.run_id}`,
            `Requisition ID: ${update.requisition_id}`,
            `Run status: ${update.status}`,
            `Started: ${update.started_at || "Unknown"}`,
            `Finished: ${update.finished_at || "Unknown"}`,
            `Exported: ${exportedAt}`,
            `Risk Tracker case: ${update.risk_tracker_case_id ?? "None"}`,
            `Review state: ${update.reviewed ? "Reviewed" : "Unread"}`,
            "",
            "Review state records whether this update was reviewed; " +
                "it does not certify the analysis as correct.",
            "",
            "## Research questions",
            "",
            ...(update.questions?.length
                ? update.questions.map((question) => `- ${question}`)
                : ["No research questions recorded."]),
            "",
            update.changes?.brief || (
                "## What changed?\n\nNo change summary available."
            ),
            "",
            "## Saved research brief",
            "",
            typeof result?.brief === "string"
                ? result.brief
                : "No saved research brief is available.",
            "",
            "## Source references",
            "",
        ];

        const sources = result?.sources || [];

        if (!sources.length) {
            lines.push("No source references recorded in this run.");
        }

        for (const [index, source] of sources.entries()) {
            lines.push(
                `### ${index + 1}. ${source.title || "Untitled source"}`,
                "",
                `URL: ${source.url || "Not recorded"}`,
                `Artemis record: ${source.record_id || "Not recorded"}`,
            );

            if (source.risk_tracker_evidence_id != null) {
                lines.push(
                    `Risk Tracker evidence ID: ${source.risk_tracker_evidence_id}`,
                );
            }

            if (source.risk_tracker_error) {
                lines.push(
                    `Attachment error: ${source.risk_tracker_error}`,
                );
            }

            lines.push("");
        }

        lines.push("## Recorded event notes", "");

        const notes = result?.event_notes || [];

        if (!notes.length) {
            lines.push("No event notes recorded in this run.");
        }

        for (const note of notes) {
            lines.push(
                `- ${note.title || "Untitled event note"}`,
                `  Status: ${note.status || "Unknown"}`,
                `  Intel Note ID: ${note.intel_note_id ?? "Not saved"}`,
            );

            if (note.error) {
                lines.push(`  Save error: ${note.error}`);
            }

            if (note.risk_tracker_link_error) {
                lines.push(`  Link error: ${note.risk_tracker_link_error}`);
            }
        }

        lines.push(
            "",
            "## Run errors and warnings",
            "",
            "```json",
            JSON.stringify({
                run_error: update.error,
                warnings: update.warnings,
            }, null, 2),
            "```",
            "",
            "## Export scope",
            "",
            "This export uses saved Artemis records and the comparison " +
                "calculated when the export was requested. It does not " +
                "perform a new search or verify source claims.",
            "",
            "Source references are included; full copies of the linked " +
                "web pages are not included.",
            "",
        );

        return lines.join("\n");
    }

    function download(content, name, type) {
        const blob = new Blob([content], { type });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");

        link.href = url;
        link.download = name;
        document.body.append(link);
        link.click();
        link.remove();

        window.setTimeout(() => URL.revokeObjectURL(url), 60000);
    }

    function addInlineText(document, parent, text) {
        // Render basic Markdown emphasis and links without accepting HTML.
        const pattern = /(\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)\s]+)\))/g;
        let cursor = 0;
        let match;

        while ((match = pattern.exec(text)) !== null) {
            parent.append(document.createTextNode(
                text.slice(cursor, match.index),
            ));

            if (match[2] !== undefined) {
                const strong = document.createElement("strong");
                strong.textContent = match[2];
                parent.append(strong);
            } else {
                let url;

                try {
                    url = new URL(match[4]);
                } catch {
                    // Invalid links stay plain text.
                }

                if (url?.protocol === "https:" || url?.protocol === "http:") {
                    const link = document.createElement("a");
                    link.textContent = match[3];
                    link.href = url.href;
                    parent.append(link);
                } else {
                    parent.append(document.createTextNode(match[0]));
                }
            }

            cursor = pattern.lastIndex;
        }

        parent.append(document.createTextNode(text.slice(cursor)));
    }

    function renderReport(document, container, markdown) {
        let codeLines = null;
        let list = null;
        let listType = null;

        function resetList() {
            list = null;
            listType = null;
        }

        function flushCode() {
            const pre = document.createElement("pre");
            pre.textContent = codeLines.join("\n");
            container.append(pre);
            codeLines = null;
        }

        for (const line of markdown.split(/\r?\n/)) {
            if (/^\s*```/.test(line)) {
                resetList();

                if (codeLines !== null) {
                    flushCode();
                } else {
                    codeLines = [];
                }
                continue;
            }

            if (codeLines !== null) {
                codeLines.push(line);
                continue;
            }

            if (!line.trim()) {
                resetList();
                continue;
            }

            if (/^\s*---+\s*$/.test(line)) {
                resetList();
                container.append(document.createElement("hr"));
                continue;
            }

            const heading = /^(#{1,6})\s+(.+)$/.exec(line);

            if (heading) {
                resetList();
                const element = document.createElement(
                    `h${heading[1].length}`,
                );
                addInlineText(document, element, heading[2]);
                container.append(element);
                continue;
            }

            const bullet = /^\s*[-*]\s+(.+)$/.exec(line);
            const numbered = /^\s*\d+[.)]\s+(.+)$/.exec(line);

            if (bullet || numbered) {
                const type = bullet ? "ul" : "ol";

                if (!list || listType !== type) {
                    list = document.createElement(type);
                    listType = type;
                    container.append(list);
                }

                const item = document.createElement("li");
                addInlineText(document, item, (bullet || numbered)[1]);
                list.append(item);
                continue;
            }

            // Keep indented details with their preceding list item.
            if (/^\s{2,}\S/.test(line) && list?.lastElementChild) {
                const detail = document.createElement("div");
                addInlineText(document, detail, line.trim());
                list.lastElementChild.append(detail);
                continue;
            }

            resetList();

            const quote = /^\s*>\s?(.*)$/.exec(line);
            const element = document.createElement(
                quote ? "blockquote" : "p",
            );
            addInlineText(document, element, quote ? quote[1] : line);
            container.append(element);
        }

        if (codeLines !== null) flushCode();
    }

    function preparePrintWindow(printWindow, update, markdown) {
        const document = printWindow.document;

        document.documentElement.lang = "en";
        document.title = filename(update);

        const charset = document.createElement("meta");
        charset.setAttribute("charset", "utf-8");

        const style = document.createElement("style");
        style.textContent = `
            :root {
                color-scheme: light;
                font-family: Arial, Helvetica, sans-serif;
                color: #202027;
                background: #fff;
            }

            body {
                max-width: 850px;
                margin: 32px auto;
                padding: 0 24px;
                font-size: 11pt;
                line-height: 1.55;
            }

            .print-toolbar {
                padding: 16px;
                margin-bottom: 28px;
                border: 1px solid #ddd;
                background: #f6f3fa;
            }

            button {
                padding: 10px 18px;
                cursor: pointer;
            }

            h1 {
                font-size: 24pt;
                color: #48246b;
                border-bottom: 2px solid #48246b;
                padding-bottom: 12px;
            }

            h2 {
                font-size: 16pt;
                margin-top: 28px;
                color: #48246b;
            }

            h3 {
                font-size: 12pt;
                margin-top: 20px;
            }

            h1, h2, h3, h4, h5, h6 {
                break-after: avoid;
            }

            p, li, pre, blockquote {
                overflow-wrap: anywhere;
            }

            li {
                margin-bottom: 8px;
            }

            li div {
                font-size: 10pt;
                color: #444;
            }

            pre {
                white-space: pre-wrap;
                font-size: 9pt;
                border: 1px solid #ddd;
                padding: 12px;
            }

            blockquote {
                border-left: 3px solid #aaa;
                margin-left: 0;
                padding-left: 14px;
            }

            a {
                color: #352064;
                text-decoration: underline;
            }

            hr {
                border: 0;
                border-top: 1px solid #ddd;
                margin: 24px 0;
            }

            @page {
                size: auto;
                margin: 18mm;
            }

            @media print {
                body {
                    max-width: none;
                    margin: 0;
                    padding: 0;
                }

                .print-toolbar {
                    display: none;
                }
            }
        `;

        document.head.replaceChildren(charset, style);

        const toolbar = document.createElement("div");
        toolbar.className = "print-toolbar";

        const instructions = document.createElement("p");
        instructions.textContent =
            "Click Print / Save PDF, then choose Save as PDF " +
            "or Microsoft Print to PDF as the destination.";

        const button = document.createElement("button");
        button.type = "button";
        button.textContent = "Print / Save PDF";
        button.addEventListener("click", () => {
            printWindow.focus();
            printWindow.print();
        });

        toolbar.append(instructions, button);

        const report = document.createElement("main");
        renderReport(document, report, markdown);

        document.body.replaceChildren(toolbar, report);

        printWindow.focus();
    }

    async function exportUpdate(format) {
        if (exporting) return;

        if (!selection.value) {
            status.textContent = "Select a research update first.";
            return;
        }

        let printWindow = null;

        if (format === "pdf") {
            // Open directly from the click so browsers can allow the window.
            printWindow = window.open("about:blank", "_blank");

            if (!printWindow) {
                status.textContent =
                    "The browser blocked the report window. Allow pop-ups " +
                    "for this local Artemis page, then try again.";
                return;
            }

            printWindow.document.body.textContent = "Preparing Artemis report...";
        }

        exporting = true;

        for (const button of [pdfButton, briefButton, dataButton]) {
            button.disabled = true;
        }

        status.textContent = "Preparing export for the selected update...";

        try {
            const update = await getSelectedUpdate();
            const exportedAt = new Date().toISOString();
            const name = filename(update);

            if (format === "pdf") {
                if (printWindow.closed) {
                    throw new Error("The report window was closed.");
                }

                preparePrintWindow(
                    printWindow,
                    update,
                    buildBrief(update, exportedAt),
                );

                status.textContent =
                    "Report opened. Click Print / Save PDF in that window " +
                    "and choose a PDF destination.";
            } else if (format === "markdown") {
                download(
                    buildBrief(update, exportedAt),
                    `${name}.md`,
                    "text/markdown;charset=utf-8",
                );

                status.textContent = "Markdown download requested.";
            } else {
                download(
                    JSON.stringify({
                        export_version: 1,
                        exported_at: exportedAt,
                        update,
                    }, null, 2),
                    `${name}.json`,
                    "application/json;charset=utf-8",
                );

                status.textContent = "Research data download requested.";
            }
        } catch (error) {
            if (printWindow && !printWindow.closed) {
                printWindow.document.body.textContent =
                    `Could not prepare report. ${error.message}`;
            }

            status.textContent = error.message;
        } finally {
            exporting = false;

            for (const button of [pdfButton, briefButton, dataButton]) {
                button.disabled = false;
            }
        }
    }

    pdfButton.addEventListener("click", () => exportUpdate("pdf"));
    briefButton.addEventListener("click", () => exportUpdate("markdown"));
    dataButton.addEventListener("click", () => exportUpdate("json"));
})();