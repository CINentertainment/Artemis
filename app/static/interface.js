"use strict";

(() => {
    const messages = document.getElementById("message-panel");
    const actions = document.querySelector(".topbar-actions");
    if (!messages) return;

    function showLatestReply() {
        const latest = messages.querySelector(
            ".message:last-of-type",
        );
        if (!latest) return;

        latest.scrollIntoView({
            behavior: "auto",
            block: "start",
        });
    }

    // app.js calls this after loading conversations and new replies.
    if (typeof scrollMessagesToBottom === "function") {
        scrollMessagesToBottom = showLatestReply;
    }

    if (actions) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = "Latest reply";
        button.addEventListener("click", showLatestReply);
        actions.prepend(button);
    }
})();