"use strict";

(() => {
    const input = document.getElementById("message-input");
    const form = document.getElementById("message-form");
    const messages = document.getElementById("message-panel");

    if (!input || !form || !messages) return;

    const panel = document.createElement("div");
    panel.style.cssText =
        "padding:12px;border:1px solid #493064;border-radius:8px;" +
        "margin:10px 0;display:flex;gap:10px;flex-wrap:wrap;align-items:center";

    const record = document.createElement("button");
    record.type = "button";
    record.textContent = "Record voice";

    const speakLast = document.createElement("button");
    speakLast.type = "button";
    speakLast.textContent = "Read latest reply";

    const stop = document.createElement("button");
    stop.type = "button";
    stop.textContent = "Stop speaking";

    const automatic = document.createElement("input");
    automatic.type = "checkbox";
    automatic.id = "voice-auto-speak";

    const label = document.createElement("label");
    label.htmlFor = automatic.id;
    label.append(automatic, " Speak replies");

    const status = document.createElement("p");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    status.style.cssText = "width:100%;margin:0";
    status.textContent =
        "Record and stop to send your message automatically. " +
        "Audio is processed by local Artemis.";

    panel.append(record, speakLast, stop, label, status);
    input.before(panel);

    let recorder = null;
    let microphone = null;
    let timer = null;
    let recordingBusy = false;
    let transcribing = false;
    let playback = null;
    let audioUrl = null;
    let speechRequest = null;
    let speechVersion = 0;

    async function checkResponse(response) {
        if (response.ok) return response;

        let detail = `Voice request failed (${response.status}).`;
        try {
            const data = await response.json();
            if (typeof data.detail === "string") detail = data.detail;
        } catch {
            // Keep the HTTP error.
        }
        throw new Error(detail);
    }

    function releaseMicrophone() {
        clearTimeout(timer);
        timer = null;

        if (microphone) {
            microphone.getTracks().forEach((track) => track.stop());
            microphone = null;
        }
    }

    function stopSpeaking() {
        window.ArtemisHandsFree?.setSpeaking(false);
        speechVersion += 1;

        if (speechRequest) {
            speechRequest.abort();
            speechRequest = null;
        }

        if (playback) {
            playback.pause();
            playback.removeAttribute("src");
            playback.load();
            playback = null;
        }

        if (audioUrl) {
            URL.revokeObjectURL(audioUrl);
            audioUrl = null;
        }
    }

    function spokenText(text) {
        return text
            .replace(/```[\s\S]*?```/g, " Code block omitted. ")
            .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
            .replace(/https?:\/\/\S+/g, " link ")
            .replace(/[*#`_]/g, "")
            .trim();
    }

    async function speak(text) {
        stopSpeaking();

        const cleaned = spokenText(text);
        if (!cleaned) {
            status.textContent = "There is no reply text to speak.";
            return;
        }

        window.ArtemisHandsFree?.setSpeaking(true);
        const version = speechVersion;
        const controller = new AbortController();
        speechRequest = controller;

        const shortened = cleaned.length > 3000;
        status.textContent = "Generating local speech...";

        try {
            const response = await fetch("/voice/speak", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text: cleaned.slice(0, 3000) }),
                signal: controller.signal,
            });

            await checkResponse(response);
            const blob = await response.blob();
            if (version !== speechVersion) return;

            audioUrl = URL.createObjectURL(blob);
            playback = new Audio(audioUrl);

            playback.addEventListener("ended", () => {
                if (version !== speechVersion) return;
                stopSpeaking();
                status.textContent = "Finished speaking.";
            });

            playback.addEventListener("error", () => {
                if (version !== speechVersion) return;
                stopSpeaking();
                status.textContent = "The generated audio could not play.";
            });

            await playback.play();

            if (version === speechVersion) {
                status.textContent = shortened
                    ? "Speaking the first 3,000 characters."
                    : "Speaking...";
            }
        } catch (error) {
            if (version !== speechVersion) return;
            stopSpeaking();

            status.textContent = error.name === "NotAllowedError"
                ? "Browser blocked playback. Click Read latest reply."
                : `Speech failed: ${error.message}`;
        } finally {
            if (speechRequest === controller) speechRequest = null;
        }
    }

    async function finishRecording(chunks, mimeType, conversationId) {
        releaseMicrophone();
        recorder = null;
        recordingBusy = false;
        transcribing = true;
        record.disabled = true;
        record.textContent = "Transcribing...";
        status.textContent = "Transcribing locally...";

        try {
            const blob = new Blob(chunks, {
                type: mimeType || "audio/webm",
            });

            if (!blob.size) throw new Error("The recording was empty.");

            const body = new FormData();
            body.append("file", blob, "recording");

            const response = await fetch("/voice/transcribe", {
                method: "POST",
                body,
            });

            await checkResponse(response);
            const data = await response.json();

            if (state.currentConversationId !== conversationId) {
                status.textContent =
                    "Conversation changed. Transcript was not inserted; record again.";
                return;
            }

            if (!data.text?.trim()) {
                status.textContent =
                    "No speech detected. Try again closer to the microphone.";
                return;
            }

            const existing = input.value;
            input.value =
                existing + (existing && !/\s$/.test(existing) ? " " : "") +
                data.text.trim();

            input.dispatchEvent(new Event("input", { bubbles: true }));
            input.focus();

            status.textContent = "Transcript ready. Sending...";
            transcribing = false;
            form.requestSubmit();
        } catch (error) {
            status.textContent = `Transcription failed: ${error.message}`;
        } finally {
            transcribing = false;
            record.disabled = false;
            record.textContent = "Record voice";
        }
    }

    record.addEventListener("click", async () => {
        if (recorder?.state === "recording") {
            recorder.stop();
            releaseMicrophone();
            record.disabled = true;
            return;
        }

        if (recordingBusy || transcribing) return;

        if (state.sending) {
            status.textContent = "Wait for the current reply before recording.";
            return;
        }

        if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
            status.textContent =
                "Recording is unavailable. Open Artemis in Edge or Chrome at " +
                "http://127.0.0.1:8200/dashboard.";
            return;
        }

        stopSpeaking();
        recordingBusy = true;
        record.disabled = true;
        const conversationId = state.currentConversationId;

        try {
            microphone = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                },
            });

            const mimeType = [
                "audio/webm;codecs=opus",
                "audio/webm",
                "audio/ogg;codecs=opus",
                "audio/mp4",
            ].find((type) => MediaRecorder.isTypeSupported(type));

            recorder = mimeType
                ? new MediaRecorder(microphone, { mimeType })
                : new MediaRecorder(microphone);

            const chunks = [];
            let failed = false;
            const actualType = recorder.mimeType;

            recorder.addEventListener("dataavailable", (event) => {
                if (event.data.size) chunks.push(event.data);
            });

            recorder.addEventListener("error", () => {
                failed = true;
                releaseMicrophone();
                recordingBusy = false;
                record.disabled = false;
                record.textContent = "Record voice";
                status.textContent = "Recording failed. Try again.";
            });

            recorder.addEventListener("stop", () => {
                if (failed) return;
                void finishRecording(chunks, actualType, conversationId);
            });

            recorder.start();
            record.disabled = false;
            record.textContent = "Stop recording";
            status.textContent =
                "Listening. Click Stop recording when finished. Maximum: 85 seconds.";

            timer = setTimeout(() => {
                if (recorder?.state === "recording") {
                    recorder.stop();
                    releaseMicrophone();
                }
            }, 85000);
        } catch (error) {
            releaseMicrophone();
            recorder = null;
            recordingBusy = false;
            record.disabled = false;
            record.textContent = "Record voice";
            status.textContent = `Could not start microphone: ${error.message}`;
        }
    });

    // Keep recording/transcription separate from submitting a chat turn.
    form.addEventListener("submit", (event) => {
        if (recordingBusy || transcribing) {
            event.preventDefault();
            event.stopImmediatePropagation();
            status.textContent = "Finish recording and transcription first.";
            return;
        }

        stopSpeaking();
    }, true);

    speakLast.addEventListener("click", () => {
        if (recordingBusy || transcribing) {
            status.textContent = "Finish recording and transcription first.";
            return;
        }

        const replies = messages.querySelectorAll(
            ".message-assistant .message-content",
        );
        const latest = replies[replies.length - 1];

        if (!latest) {
            status.textContent = "There is no assistant reply to read.";
            return;
        }

        void speak(latest.textContent || "");
    });

    stop.addEventListener("click", () => {
        stopSpeaking();
        status.textContent = "Speech stopped.";
    });

    automatic.addEventListener("change", () => {
        if (!automatic.checked) stopSpeaking();
    });

    document.addEventListener("artemis:assistant-reply", (event) => {
        if (automatic.checked && !recordingBusy && !transcribing) {
            void speak(event.detail?.content || "");
        }
    });

    window.addEventListener("pagehide", () => {
        stopSpeaking();
        if (recorder?.state === "recording") {
            recorder.onstop = null;
        }
        releaseMicrophone();
    });
})();