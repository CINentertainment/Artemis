"use strict";

(() => {
    const input = document.getElementById("message-input");
    const form = document.getElementById("message-form");
    const voicePanel = document.getElementById("voice-auto-speak")?.closest("div");

    if (!input || !form || !voicePanel) return;

    const enable = document.createElement("button");
    enable.type = "button";
    enable.textContent = "Enable hands-free";

    const disable = document.createElement("button");
    disable.type = "button";
    disable.textContent = "Stop listening";
    disable.disabled = true;

    const status = document.createElement("p");
    status.setAttribute("role", "status");
    status.style.cssText = "width:100%;margin:0";
    status.textContent = "Hands-free off. Microphone inactive.";

    voicePanel.append(enable, disable, status);

    const manualRecord = [...voicePanel.querySelectorAll("button")]
        .find((button) => button.textContent === "Record voice");

    let enabled = false;
    let speaking = false;
    let epoch = 0;
    let cycle = null;
    let opening = false;
    let transcribing = false;
    let request = null;
    let armedUntil = 0;
    let resumeAt = 0;

    function cancelCapture() {
        if (!cycle) return;

        const current = cycle;
        current.cancelled = true;
        clearInterval(current.timer);

        if (current.recorder.state === "recording") {
            current.recorder.stop();
        }

        current.stream.getTracks().forEach((track) => track.stop());
        void current.context.close().catch(() => {});
        cycle = null;
    }

    function invalidate() {
        epoch += 1;
        cancelCapture();

        if (request) {
            request.abort();
            request = null;
        }
    }

    function turnOff(message = "Hands-free off. Microphone released.") {
        enabled = false;
        armedUntil = 0;
        invalidate();

        enable.disabled = false;
        disable.disabled = true;
        if (manualRecord) manualRecord.disabled = false;
        status.textContent = message;
    }

    // Called by the existing Emma playback code.
    window.ArtemisHandsFree = {
        setSpeaking(value) {
            speaking = value;

            if (value) {
                cancelCapture();
                if (enabled) {
                    status.textContent = "Listening paused while Artemis speaks.";
                }
            } else {
                resumeAt = Date.now() + 800;
            }
        },
    };

    async function transcribe(blob, ticket, conversationId) {
        transcribing = true;
        const controller = new AbortController();
        request = controller;
        status.textContent = "Checking speech locally...";

        try {
            const body = new FormData();
            body.append("file", blob, "hands-free-recording");

            const response = await fetch("/voice/transcribe", {
                method: "POST",
                body,
                signal: controller.signal,
            });

            if (!response.ok) {
                let detail = `Transcription failed (${response.status}).`;
                try {
                    const data = await response.json();
                    if (typeof data.detail === "string") detail = data.detail;
                } catch {
                    // Keep the HTTP status message.
                }
                throw new Error(detail);
            }

            const data = await response.json();

            if (
                !enabled ||
                ticket !== epoch ||
                speaking ||
                state.sending ||
                state.currentConversationId !== conversationId
            ) return;

            const text = (data.text || "").trim();
            if (!text) return;

            // Accept "Artemis", optionally preceded by "Hey" or "Hello".
            const wake = text.match(
                /^(?:(?:hey|hello)[,\s]+)?artemis\b[\s,.:;!?-]*/i,
            );

            let command;

            if (wake) {
                command = text.slice(wake[0].length).trim();
            } else if (Date.now() < armedUntil) {
                command = text;
            } else {
                status.textContent = 'Waiting for “Artemis”…';
                return;
            }

            const normalized = command
                .toLowerCase()
                .replace(/[.!?,;:]/g, "")
                .replace(/\s+/g, " ")
                .trim();

            if (normalized === "stop listening" ||
                normalized === "please stop listening") {
                turnOff();
                return;
            }

            if (!command) {
                armedUntil = Date.now() + 12000;
                status.textContent = "Artemis heard you. Say your request now.";
                return;
            }

            armedUntil = 0;

            // Do not automatically send an unfinished typed draft.
            if (input.value.trim()) {
                turnOff(
                    "Hands-free paused because the message box contains a draft. " +
                    "Send or clear it, then enable hands-free again.",
                );
                return;
            }

            input.value = command;
            input.dispatchEvent(new Event("input", { bubbles: true }));
            status.textContent = `Sending: ${command}`;
            form.requestSubmit();
        } catch (error) {
            if (error.name !== "AbortError" && enabled && ticket === epoch) {
                turnOff(`Hands-free stopped: ${error.message}`);
            }
        } finally {
            if (request === controller) request = null;
            transcribing = false;
            resumeAt = Math.max(resumeAt, Date.now() + 400);
        }
    }

    async function startCycle() {
        if (
            !enabled || speaking || state.sending ||
            cycle || opening || transcribing ||
            Date.now() < resumeAt
        ) return;

        opening = true;
        const ticket = epoch;
        const conversationId = state.currentConversationId;
        let stream = null;
        let context = null;

        try {
            stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true,
                },
            });

            if (
                !enabled || ticket !== epoch ||
                speaking || state.sending
            ) {
                stream.getTracks().forEach((track) => track.stop());
                return;
            }

            const AudioContextClass =
                window.AudioContext || window.webkitAudioContext;
            context = new AudioContextClass();
            await context.resume();

            if (
                !enabled || ticket !== epoch ||
                speaking || state.sending
            ) {
                stream.getTracks().forEach((track) => track.stop());
                await context.close();
                return;
            }

            const source = context.createMediaStreamSource(stream);
            const analyser = context.createAnalyser();
            analyser.fftSize = 1024;
            source.connect(analyser);

            const mime = [
                "audio/webm;codecs=opus",
                "audio/webm",
                "audio/ogg;codecs=opus",
                "audio/mp4",
            ].find((type) => MediaRecorder.isTypeSupported(type));

            const recorder = mime
                ? new MediaRecorder(stream, { mimeType: mime })
                : new MediaRecorder(stream);

            const current = {
                stream,
                context,
                recorder,
                timer: null,
                cancelled: false,
            };

            cycle = current;

            const chunks = [];
            const samples = new Float32Array(analyser.fftSize);
            const started = Date.now();
            let firstSpeech = 0;
            let lastSpeech = 0;
            let voicedFrames = 0;

            recorder.addEventListener("dataavailable", (event) => {
                if (event.data.size) chunks.push(event.data);
            });

            recorder.addEventListener("error", () => {
                if (!current.cancelled) {
                    turnOff("Recording failed. Enable hands-free to try again.");
                }
            });

            recorder.addEventListener("stop", () => {
                clearInterval(current.timer);
                stream.getTracks().forEach((track) => track.stop());
                void context.close().catch(() => {});

                if (cycle === current) cycle = null;

                if (
                    current.cancelled || !enabled ||
                    ticket !== epoch || speaking || state.sending
                ) return;

                // Ignore silence and very brief noises.
                if (voicedFrames < 4) return;

                const blob = new Blob(chunks, {
                    type: recorder.mimeType || "audio/webm",
                });

                if (blob.size) {
                    void transcribe(blob, ticket, conversationId);
                }
            });

            recorder.start();

            status.textContent = Date.now() < armedUntil
                ? "Listening for your request..."
                : 'Hands-free active. Say “Artemis” and your request.';

            current.timer = setInterval(() => {
                if (current.cancelled || recorder.state !== "recording") return;

                if (
                    speaking || state.sending ||
                    state.currentConversationId !== conversationId
                ) {
                    cancelCapture();
                    return;
                }

                analyser.getFloatTimeDomainData(samples);

                let energy = 0;
                for (const sample of samples) energy += sample * sample;
                const volume = Math.sqrt(energy / samples.length);
                const now = Date.now();

                if (volume > 0.015) {
                    voicedFrames += 1;
                    firstSpeech = firstSpeech || now;
                    lastSpeech = now;
                }

                const finishedSpeaking =
                    firstSpeech && now - lastSpeech > 1300;

                const reachedSpeechLimit =
                    firstSpeech && now - firstSpeech > 25000;

                const idleReset =
                    !firstSpeech && now - started > 15000;

                if (finishedSpeaking || reachedSpeechLimit || idleReset) {
                    clearInterval(current.timer);
                    recorder.stop();
                    stream.getTracks().forEach((track) => track.stop());
                }
            }, 60);
        } catch (error) {
            if (stream) stream.getTracks().forEach((track) => track.stop());
            if (context) void context.close().catch(() => {});

            if (enabled && ticket === epoch) {
                turnOff(`Could not start hands-free: ${error.message}`);
            }
        } finally {
            opening = false;
        }
    }

    enable.addEventListener("click", () => {
        if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
            status.textContent = "This browser does not support recording.";
            return;
        }

        if (state.sending || manualRecord?.disabled) {
            status.textContent = "Finish the current action first.";
            return;
        }

        if (manualRecord?.textContent !== "Record voice" && manualRecord) {
            status.textContent = "Finish the manual recording first.";
            return;
        }

        if (input.value.trim()) {
            status.textContent = "Send or clear your typed draft first.";
            return;
        }

        invalidate();
        enabled = true;
        enable.disabled = true;
        disable.disabled = false;
        if (manualRecord) manualRecord.disabled = true;

        document.getElementById("voice-auto-speak").checked = true;
        status.textContent = "Starting hands-free...";
        void startCycle();
    });

    disable.addEventListener("click", () => turnOff());

    // A typed or voice-submitted turn cancels any old listening cycle.
    form.addEventListener("submit", () => {
        if (enabled) {
            invalidate();
            armedUntil = 0;
            status.textContent = "Listening paused while Artemis responds.";
        }
    }, true);

    setInterval(() => {
        if (enabled) void startCycle();
    }, 300);

    window.addEventListener("pagehide", () => turnOff());
})();