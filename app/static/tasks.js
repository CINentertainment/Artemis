"use strict";

(() => {
    const container = document.querySelector(".command-console");
    if (!container) return;

    const panel = document.createElement("details");
    panel.className = "source-options";
    panel.id = "artemis-tasks";

    // Static markup only; task text is inserted using textContent.
    panel.innerHTML = `
        <summary id="tasks-heading">Tasks — loading</summary>
        <div style="padding:12px">
            <p>Due and overdue tasks use the Artemis computer's local date.</p>
            <label for="tasks-view">Show</label>
            <select id="tasks-view">
                <option value="due">Due today and overdue</option>
                <option value="open">All open tasks</option>
                <option value="completed">Completed tasks</option>
            </select>
            <button id="tasks-refresh" type="button">Refresh tasks</button>
            <p id="tasks-status" role="status" aria-live="polite"></p>
            <ul id="tasks-list"></ul>
            <button id="tasks-previous" type="button">Previous</button>
            <button id="tasks-next" type="button">Next</button>
            <p>Add tasks through chat, for example:
               Add a task to review the market brief tomorrow.</p>
        </div>
    `;

    container.prepend(panel);

    const heading = panel.querySelector("#tasks-heading");
    const view = panel.querySelector("#tasks-view");
    const refresh = panel.querySelector("#tasks-refresh");
    const status = panel.querySelector("#tasks-status");
    const list = panel.querySelector("#tasks-list");
    const previous = panel.querySelector("#tasks-previous");
    const next = panel.querySelector("#tasks-next");

    let offset = 0;
    let busy = false;
    let firstLoad = true;
    const limit = 20;

    async function request(url, options = {}) {
        const response = await fetch(url, {
            cache: "no-store",
            ...options,
        });

        if (!response.ok) {
            let message = `Request failed (${response.status}).`;
            try {
                const data = await response.json();
                if (typeof data.detail === "string") message = data.detail;
            } catch {
                // Keep the HTTP status.
            }
            throw new Error(message);
        }

        return response.json();
    }

    function setBusy(value) {
        busy = value;
        view.disabled = value;
        refresh.disabled = value;
        previous.disabled = true;
        next.disabled = true;

        for (const button of list.querySelectorAll("button")) {
            button.disabled = value;
        }
    }

    async function renderTasks() {
        const query = new URLSearchParams({
            view: view.value,
            limit: String(limit),
            offset: String(offset),
        });

        const data = await request(`/tasks?${query}`);

        heading.textContent = `Tasks — ${data.due_count} due or overdue`;

        if (firstLoad) {
            panel.open = data.due_count > 0;
            firstLoad = false;
        }

        list.replaceChildren();

        for (const task of data.items) {
            const item = document.createElement("li");
            item.style.marginBottom = "12px";

            const text = document.createElement("span");
            const due = task.due_date
                ? `due ${task.due_date}`
                : "no due date";

            text.textContent =
                `[${task.id.slice(0, 8)}] ${task.title} — ${due}` +
                (task.overdue ? " — OVERDUE" : "") +
                (task.completed ? " — completed" : "");

            item.append(text);

            if (!task.completed) {
                const complete = document.createElement("button");
                complete.type = "button";
                complete.textContent = "Mark complete";
                complete.style.marginLeft = "10px";

                complete.addEventListener("click", () => {
                    void action(async () => {
                        await request(
                            `/tasks/${encodeURIComponent(task.id)}/complete`,
                            { method: "PUT" },
                        );
                        offset = 0;
                        await renderTasks();
                    });
                });

                item.append(complete);
            }

            list.append(item);
        }

        if (!data.items.length) {
            const empty = document.createElement("li");
            empty.textContent = offset
                ? "No tasks on this page. Use Previous."
                : "No tasks in this view.";
            list.append(empty);
        }

        status.textContent = data.total_count
            ? `${data.total_count} task(s). Showing ` +
              `${data.items.length ? offset + 1 : 0}–` +
              `${offset + data.items.length}. Local date: ${data.today}.`
            : `No tasks in this view. Local date: ${data.today}.`;

        return data;
    }

    async function action(work = renderTasks) {
        if (busy) return;
        setBusy(true);
        let data;

        try {
            data = await work();
        } catch (error) {
            status.textContent = `Tasks could not refresh: ${error.message}`;
        } finally {
            setBusy(false);
            previous.disabled = offset === 0;
            next.disabled = !data ||
                offset + data.items.length >= data.total_count;
        }
    }

    refresh.addEventListener("click", () => void action());

    view.addEventListener("change", () => {
        offset = 0;
        void action();
    });

    previous.addEventListener("click", () => {
        offset = Math.max(0, offset - limit);
        void action();
    });

    next.addEventListener("click", () => {
        offset += limit;
        void action();
    });

    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) void action();
    });

    setInterval(() => {
        if (!document.hidden) void action();
    }, 30000);

    void action();
})();