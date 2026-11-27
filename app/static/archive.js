"use strict";

(() => {
    const list = document.getElementById("conversation-list");
    if (!list) return;

    const originalRender = renderConversationList;

    let folders = [];
    let memberships = {};
    let ready = false;
    let busy = false;
    const expanded = new Map();
    const selected = new Set();

    const toolbar = document.createElement("div");
    toolbar.className = "archive-toolbar";

    const newFolder = document.createElement("button");
    newFolder.type = "button";
    newFolder.textContent = "+ New folder";

    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.textContent = "Refresh";

    toolbar.append(newFolder, refresh);
    list.before(toolbar);
    const bulk=document.createElement('div');bulk.className='archive-toolbar archive-bulk';
    const selectAll=document.createElement('button');selectAll.type='button';selectAll.textContent='Select all chats';
    const clear=document.createElement('button');clear.type='button';clear.textContent='Clear selection';
    const deleteSelected=document.createElement('button');deleteSelected.type='button';deleteSelected.className='archive-danger';
    const bulkStatus=document.createElement('p');bulkStatus.className='archive-empty';bulkStatus.setAttribute('role','status');bulkStatus.setAttribute('aria-live','polite');
    bulk.append(selectAll,clear,deleteSelected);toolbar.after(bulk,bulkStatus);
    function updateSelection(){deleteSelected.textContent=`Delete selected (${selected.size})`;deleteSelected.disabled=busy||state.sending||!selected.size;selectAll.disabled=busy||state.sending;clear.disabled=busy||state.sending;}
    selectAll.onclick=()=>{if(busy||state.sending)return;for(const c of state.conversations)selected.add(c.id);renderArchive();};
    clear.onclick=()=>{if(busy||state.sending)return;selected.clear();renderArchive();};
    deleteSelected.onclick=()=>{
        if(busy||state.sending||!selected.size)return;
        const ids=[...selected];
        if(!window.confirm(`Delete ${ids.length} selected chats and their messages? This cannot be undone from Artemis. Separately saved notes, research, tasks and backups remain.`))return;
        void action(async()=>{
            let removed=0;const failed=[];updateSelection();
            for(const id of ids){
                try{
                    if(state.currentConversationId===id)stopCurrentVoice();
                    await requestJson(`/conversations/${id}`,{method:'DELETE'});
                    if(state.currentConversationId===id)clearCurrentConversation();
                    selected.delete(id);state.conversations=state.conversations.filter(c=>c.id!==id);removed++;
                }catch(error){failed.push(id);}
                bulkStatus.textContent=`Deleted ${removed} of ${ids.length} selected chats…`;
            }
            bulkStatus.textContent=`Deleted ${removed} chats.`+(failed.length?` ${failed.length} could not be deleted and remain selected; retry them.`:'');
        });
    };
    updateSelection();

    function isOpen(key, defaultValue) {
        if (expanded.has(key)) return expanded.get(key);

        try {
            const saved = localStorage.getItem(`artemis-folder-open:${key}`);
            if (saved !== null) return saved === "true";
        } catch {
            // Expansion still works without browser storage.
        }

        return defaultValue;
    }

    function rememberOpen(key, value) {
        expanded.set(key, value);
        try {
            localStorage.setItem(`artemis-folder-open:${key}`, String(value));
        } catch {
            // Keep the in-memory setting.
        }
    }

    async function readFolders() {
        const data = await requestJson("/conversation-folders");
        folders = data.folders;
        memberships = data.memberships;
        ready = true;
    }

    function stopCurrentVoice() {
        for (const button of document.querySelectorAll("button")) {
            if (
                button.textContent.trim() === "Stop listening" ||
                button.textContent.trim() === "Stop speaking"
            ) {
                button.click();
            }
        }
    }

    function clearCurrentConversation() {
        state.currentConversationId = null;
        state.conversationLoad += 1;

        elements.messagePanel.replaceChildren();
        elements.messagePanel.classList.add("hidden");
        elements.welcomePanel.classList.remove("hidden");
        elements.conversationTitle.textContent = "Command Overview";
        elements.conversationSubtitle.textContent =
            "Private, locally stored operation record";
    }

    async function action(work) {
        if (busy || state.sending) {
            showNotification("Finish the current action first.", true);
            return;
        }

        busy = true;
        setSending(true);
        updateSelection();
        newFolder.disabled = true;
        refresh.disabled = true;

        try {
            await work();
            await readFolders();
            await loadConversations();
        } catch (error) {
            showNotification(error.message, true);
        } finally {
            busy = false;
            setSending(false);
            newFolder.disabled = false;
            refresh.disabled = false;
            renderArchive();
        }
    }

    function conversationRow(conversation) {
        const row = document.createElement("div");
        row.className = "archive-entry";

        const open = document.createElement("button");
        open.type = "button";
        open.className = "conversation-item";
        open.classList.toggle(
            "active",
            conversation.id === state.currentConversationId,
        );
        open.disabled = busy;

        const copy = document.createElement("div");
        copy.className = "conversation-copy";

        const title = document.createElement("div");
        title.className = "conversation-title";
        title.textContent = conversation.title || "Untitled operation";
        open.title = title.textContent;

        const date = document.createElement("div");
        date.className = "conversation-date";
        date.textContent = formatDate(
            conversation.updated_at || conversation.created_at,
        );

        copy.append(title, date);
        open.append(copy);

        open.addEventListener("click", () => {
            if (!busy && !state.sending) {
                void selectConversation(conversation.id);
            }
        });

        const options = document.createElement("details");
        options.className = "archive-chat-options";

        const summary = document.createElement("summary");
        summary.textContent = "Manage chat";
        options.append(summary);

        const label = document.createElement("label");
        label.textContent = "Move to folder";

        const select = document.createElement("select");
        select.setAttribute(
            "aria-label",
            `Move ${conversation.title || "conversation"} to folder`,
        );

        select.add(new Option("Unfiled", ""));
        for (const folder of folders) {
            select.add(new Option(folder.name, folder.id));
        }

        select.value = memberships[conversation.id] || "";
        select.disabled = busy;

        select.addEventListener("change", () => {
            const folderId = select.value || null;

            void action(async () => {
                await requestJson(
                    `/conversation-folders/assign/${conversation.id}`,
                    {
                        method: "PUT",
                        body: JSON.stringify({ folder_id: folderId }),
                    },
                );

                if (folderId) rememberOpen(folderId, true);
                else rememberOpen("unfiled", true);

                showNotification("Conversation moved.");
            });
        });

        label.append(select);

        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "archive-danger";
        remove.textContent = "Delete chat";
        remove.disabled = busy;

        remove.addEventListener("click", () => {
            if (busy || state.sending) return;

            const name = conversation.title || "Untitled operation";
            const confirmed = window.confirm(
                `Delete "${name}" and its saved messages?\n\n` +
                "This cannot be undone from Artemis. Separately saved " +
                "notes, memories, tasks, research records, and backups remain.",
            );

            if (!confirmed) return;

            void action(async () => {
                if (state.currentConversationId === conversation.id) {
                    stopCurrentVoice();
                }

                await requestJson(`/conversations/${conversation.id}`, {
                    method: "DELETE",
                });

                if (state.currentConversationId === conversation.id) {
                    clearCurrentConversation();
                }

                showNotification("Conversation deleted.");
            });
        });

        options.append(label, remove);
        const selection=document.createElement('label');selection.className='archive-selection';
        const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.checked=selected.has(conversation.id);checkbox.disabled=busy||state.sending;
        checkbox.setAttribute('aria-label','Select chat: '+(conversation.title||'Untitled operation'));
        checkbox.onchange=()=>{if(checkbox.checked)selected.add(conversation.id);else selected.delete(conversation.id);updateSelection();};
        selection.append(checkbox,document.createTextNode('Select chat'));
        row.append(selection,open, options);
        return row;
    }

    function folderGroup(folder, conversations) {
        const key = folder?.id || "unfiled";
        const name = folder?.name || "Unfiled";

        const group = document.createElement("details");
        group.className = "archive-folder";
        group.open = isOpen(key, !folder);

        group.addEventListener("toggle", () => {
            rememberOpen(key, group.open);
        });

        const summary = document.createElement("summary");
        const heading = document.createElement("span");
        heading.textContent = name;

        const count = document.createElement("span");
        count.className = "archive-folder-count";
        count.textContent = String(conversations.length);

        summary.append(heading, count);
        group.append(summary);

        if (folder) {
            const controls = document.createElement("div");
            controls.className = "archive-folder-controls";

            const rename = document.createElement("button");
            rename.type = "button";
            rename.textContent = "Rename";
            rename.disabled = busy;

            rename.addEventListener("click", () => {
                if (busy || state.sending) return;

                const name = window.prompt("Folder name:", folder.name);
                if (name === null || !name.trim()) return;

                void action(async () => {
                    await requestJson(`/conversation-folders/${folder.id}`, {
                        method: "PATCH",
                        body: JSON.stringify({ name: name.trim() }),
                    });
                    showNotification("Folder renamed.");
                });
            });

            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "archive-danger";
            remove.textContent = "Remove folder";
            remove.disabled = busy;

            remove.addEventListener("click", () => {
                if (busy || state.sending) return;

                if (!window.confirm(
                    `Remove folder "${folder.name}"?\n\n` +
                    "Its conversations will be kept and moved to Unfiled.",
                )) return;

                void action(async () => {
                    await requestJson(`/conversation-folders/${folder.id}`, {
                        method: "DELETE",
                    });
                    rememberOpen("unfiled", true);
                    showNotification("Folder removed. Conversations kept.");
                });
            });

            controls.append(rename, remove);
            group.append(controls);
        }

        for (const conversation of conversations) {
            group.append(conversationRow(conversation));
        }

        if (!conversations.length) {
            const empty = document.createElement("p");
            empty.className = "archive-empty";
            empty.textContent = folder
                ? "Move a conversation here using Manage chat."
                : "No unfiled conversations.";
            group.append(empty);
        }

        return group;
    }

    function renderArchive() {
        if (!ready) {
            originalRender();
            return;
        }

        const available=new Set(state.conversations.map(c=>c.id));for(const id of selected)if(!available.has(id))selected.delete(id);
        updateSelection();
        list.replaceChildren();

        const knownFolders = new Set(folders.map((folder) => folder.id));

        for (const folder of folders) {
            list.append(folderGroup(
                folder,
                state.conversations.filter(
                    (conversation) =>
                        memberships[conversation.id] === folder.id,
                ),
            ));
        }

        list.append(folderGroup(
            null,
            state.conversations.filter(
                (conversation) =>
                    !knownFolders.has(memberships[conversation.id]),
            ),
        ));
    }

    renderConversationList = renderArchive;

    newFolder.addEventListener("click", () => {
        if (busy || state.sending) return;

        const name = window.prompt("New folder name:");
        if (name === null || !name.trim()) return;

        void action(async () => {
            const folder = await requestJson("/conversation-folders", {
                method: "POST",
                body: JSON.stringify({ name: name.trim() }),
            });

            rememberOpen(folder.id, true);
            showNotification("Folder created.");
        });
    });

    refresh.addEventListener("click", () => {
        void action(async () => {});
    });

    void readFolders().then(renderArchive).catch((error) => {
        showNotification(
            `Folders could not load. ${error.message}`,
            true,
        );
    });
})();