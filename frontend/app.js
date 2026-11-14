'use strict';

/**
 * Voice AI Agent front-end.
 *
 * Modes:
 *  - batch:      record a clip, POST it to /chat, play the returned WAV.
 *  - stream:     stream microphone audio over /ws/chat; the answer is spoken in one go.
 *  - stream_llm: like stream, but the LLM answer is synthesized sentence by sentence
 *                and played back progressively as PCM chunks arrive.
 */

const WAV_HEADER_BYTES = 44;
const RECORDER_TIMESLICE_MS = 100;
const MIME_CANDIDATES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];

/** Decode the hex-encoded UTF-8 transcript headers sent by POST /chat. */
function hexToString(hex) {
    if (!hex) return '';
    const bytes = new Uint8Array(hex.length / 2);
    for (let i = 0; i < bytes.length; i += 1) {
        bytes[i] = parseInt(hex.substr(i * 2, 2), 16);
    }
    return new TextDecoder().decode(bytes);
}

function pickMimeType() {
    if (typeof MediaRecorder === 'undefined' || !MediaRecorder.isTypeSupported) return '';
    return MIME_CANDIDATES.find((type) => MediaRecorder.isTypeSupported(type)) || '';
}

/** Plays a streamed 16-bit mono PCM WAV (header + raw frames) gaplessly via Web Audio. */
class PcmStreamPlayer {
    constructor(audioContext) {
        this.ctx = audioContext;
        this.sampleRate = 22050;
        this.headerSeen = false;
        this.leftover = null;
        this.nextTime = 0;
        this.sources = new Set();
    }

    push(arrayBuffer) {
        let bytes = new Uint8Array(arrayBuffer);
        if (!this.headerSeen) {
            if (bytes.length < WAV_HEADER_BYTES) return;
            this.sampleRate = new DataView(arrayBuffer).getUint32(24, true) || this.sampleRate;
            this.headerSeen = true;
            bytes = bytes.subarray(WAV_HEADER_BYTES);
        }
        if (this.leftover) {
            const merged = new Uint8Array(this.leftover.length + bytes.length);
            merged.set(this.leftover);
            merged.set(bytes, this.leftover.length);
            bytes = merged;
            this.leftover = null;
        }
        if (bytes.length % 2 === 1) {
            this.leftover = bytes.slice(-1);
            bytes = bytes.subarray(0, bytes.length - 1);
        }
        if (bytes.length === 0) return;

        const samples = new Int16Array(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.length));
        const buffer = this.ctx.createBuffer(1, samples.length, this.sampleRate);
        const channel = buffer.getChannelData(0);
        for (let i = 0; i < samples.length; i += 1) channel[i] = samples[i] / 32768;

        const source = this.ctx.createBufferSource();
        source.buffer = buffer;
        source.connect(this.ctx.destination);
        const startAt = Math.max(this.ctx.currentTime + 0.02, this.nextTime);
        source.start(startAt);
        this.nextTime = startAt + buffer.duration;
        this.sources.add(source);
        source.onended = () => this.sources.delete(source);
    }

    /** Seconds until queued audio finishes playing. */
    remaining() {
        return Math.max(0, this.nextTime - this.ctx.currentTime);
    }

    stop() {
        this.sources.forEach((source) => {
            try {
                source.stop();
            } catch (_) {
                /* already stopped */
            }
        });
        this.sources.clear();
        this.nextTime = 0;
    }
}

const MODE_DESCRIPTIONS = {
    batch: 'Records your whole question, then sends it in one request.',
    stream: 'Streams your voice to Deepgram while you speak, then reads the answer aloud.',
    stream_llm: 'Streams your voice in and speaks the OpenAI answer sentence by sentence as it is written.',
};

const AGENT_ICON =
    '<svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11z"/></svg>';

const IDLE_LABEL = 'Hold to speak';
const RECORDING_LABEL = 'Listening… release to send';

document.addEventListener('DOMContentLoaded', () => {
    const recordBtn = document.getElementById('record-btn');
    const recordLabel = document.querySelector('.record-label');
    const statusEl = document.getElementById('status');
    const statusText = statusEl.querySelector('.status-text');
    const chatBox = document.getElementById('chat-box');
    const chatScroll = document.getElementById('chat-scroll');
    const emptyState = document.getElementById('empty-state');
    const clearBtn = document.getElementById('clear-btn');
    const noticeEl = document.getElementById('notice');
    const languageSelect = document.getElementById('language-select');
    const modeDescription = document.getElementById('mode-description');
    const modeButtons = Array.from(document.querySelectorAll('.segment[data-mode]'));

    const state = {
        mode: 'batch',
        busy: false,
        recording: false,
        micStream: null,
        recorder: null,
        mimeType: '',
        chunks: [],
        ws: null,
        startTime: 0,
        audioCtx: null,
        player: null,
        htmlAudio: null,
        status: 'idle',
        typingEl: null,
    };

    /**
     * Update the status pill.
     * @param {string} text
     * @param {'idle'|'listening'|'busy'|'speaking'|'success'|'error'} [kind]
     */
    const setStatus = (text, kind = 'idle') => {
        statusText.textContent = text;
        statusEl.dataset.state = kind;
        state.status = kind;
    };

    const setBusy = (busy) => {
        state.busy = busy;
        recordBtn.disabled = busy;
        recordBtn.setAttribute('aria-busy', String(busy));
        modeButtons.forEach((btn) => {
            btn.disabled = busy;
        });
        languageSelect.disabled = busy;
        clearBtn.disabled = busy;
    };

    const scrollToBottom = () => {
        chatScroll.scrollTop = chatScroll.scrollHeight;
    };

    function buildMessage(sender) {
        const isUser = sender === 'user';
        const wrapper = document.createElement('div');
        wrapper.className = `message ${isUser ? 'message-user' : 'message-agent'}`;

        const avatar = document.createElement('div');
        avatar.className = 'avatar';
        avatar.setAttribute('aria-hidden', 'true');
        avatar.innerHTML = AGENT_ICON; // static SVG markup

        const body = document.createElement('div');
        body.className = 'message-body';
        const bubble = document.createElement('div');
        bubble.className = 'bubble';
        const speaker = document.createElement('span');
        speaker.className = 'visually-hidden';
        speaker.textContent = isUser ? 'You said: ' : 'Agent: ';
        const content = document.createElement('span');
        bubble.append(speaker, content);
        body.append(bubble);
        wrapper.append(avatar, body);
        return { wrapper, bubble: content };
    }

    function hideEmptyState() {
        emptyState.hidden = true;
        clearBtn.hidden = false;
    }

    function addMessage(text, sender) {
        removeTyping();
        hideEmptyState();
        const { wrapper, bubble } = buildMessage(sender);
        bubble.textContent = text;
        chatBox.appendChild(wrapper);
        scrollToBottom();
        return bubble;
    }

    /** Show an animated "agent is thinking" bubble until the answer arrives. */
    function showTyping() {
        if (state.typingEl) return;
        hideEmptyState();
        const { wrapper, bubble } = buildMessage('ai');
        wrapper.classList.add('message-typing');
        wrapper.setAttribute('aria-hidden', 'true');
        const dots = document.createElement('span');
        dots.className = 'typing';
        dots.append(document.createElement('span'), document.createElement('span'), document.createElement('span'));
        bubble.appendChild(dots);
        chatBox.appendChild(wrapper);
        state.typingEl = wrapper;
        scrollToBottom();
    }

    function removeTyping() {
        if (state.typingEl) {
            state.typingEl.remove();
            state.typingEl = null;
        }
    }

    function clearConversation() {
        stopPlayback();
        removeTyping();
        chatBox.querySelectorAll('.message').forEach((node) => node.remove());
        emptyState.hidden = false;
        clearBtn.hidden = true;
        setStatus('Ready');
        recordBtn.focus();
    }

    function showNotice(html, tone) {
        noticeEl.innerHTML = `<span>${html}</span>`; // static, trusted strings only
        noticeEl.dataset.tone = tone;
        noticeEl.hidden = false;
    }

    function stopPlayback() {
        if (state.player) state.player.stop();
        if (state.htmlAudio) {
            state.htmlAudio.pause();
            state.htmlAudio = null;
        }
    }

    function playBlob(blob, onEnded) {
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        state.htmlAudio = audio;
        audio.addEventListener(
            'ended',
            () => {
                URL.revokeObjectURL(url);
                if (onEnded) onEnded();
            },
            { once: true },
        );
        audio.play().catch((err) => {
            console.warn('Playback failed:', err);
            setStatus('Could not play the audio reply. Check your sound settings.', 'error');
        });
    }

    // ---- server health ----------------------------------------------------
    async function checkHealth() {
        try {
            const response = await fetch('/health', { cache: 'no-store' });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const health = await response.json();
            const providers = health.providers || {};
            if (!providers.deepgram) {
                showNotice(
                    '<strong>Speech-to-text is not configured.</strong> Set <code>DEEPGRAM_API_KEY</code> on the server and restart it, otherwise requests will fail.',
                    'warning',
                );
            } else if (!providers.openai && !providers.gemini) {
                showNotice(
                    '<strong>Offline answers.</strong> No OpenAI or Gemini key is configured, so the agent replies with the closest FAQ entry.',
                    'info',
                );
            }
        } catch (err) {
            showNotice(
                '<strong>Cannot reach the server.</strong> Make sure the backend is running, then reload the page.',
                'danger',
            );
        }
    }
    checkHealth();

    // ---- mode selection ---------------------------------------------------
    modeButtons.forEach((btn) => {
        btn.addEventListener('click', () => {
            state.mode = btn.dataset.mode;
            modeButtons.forEach((other) => {
                const active = other === btn;
                other.classList.toggle('active', active);
                other.setAttribute('aria-pressed', String(active));
            });
            modeDescription.textContent = MODE_DESCRIPTIONS[state.mode] || '';
        });
    });

    clearBtn.addEventListener('click', clearConversation);

    // ---- microphone -------------------------------------------------------
    async function ensureMicrophone() {
        if (state.micStream) return true;
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === 'undefined') {
            setStatus('This browser cannot record audio. Try a recent Chrome, Edge, Firefox or Safari.', 'error');
            return false;
        }
        try {
            state.micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
            state.mimeType = pickMimeType();
            return true;
        } catch (err) {
            console.warn('Microphone access denied:', err);
            setStatus('Microphone access was blocked. Allow it in your browser settings and try again.', 'error');
            return false;
        }
    }

    function createRecorder() {
        const options = state.mimeType ? { mimeType: state.mimeType } : undefined;
        const recorder = new MediaRecorder(state.micStream, options);
        recorder.addEventListener('dataavailable', (event) => {
            if (event.data.size === 0) return;
            if (state.mode === 'batch') {
                state.chunks.push(event.data);
            } else if (state.ws && state.ws.readyState === WebSocket.OPEN) {
                state.ws.send(event.data);
            }
        });
        recorder.addEventListener('stop', onRecorderStop);
        return recorder;
    }

    function ensureAudioContext() {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) return null;
        if (!state.audioCtx) state.audioCtx = new Ctx();
        if (state.audioCtx.state === 'suspended') state.audioCtx.resume();
        return state.audioCtx;
    }

    const elapsed = () => ((performance.now() - state.startTime) / 1000).toFixed(2);

    // ---- batch ------------------------------------------------------------
    async function sendBatch() {
        const type = state.mimeType || 'audio/webm';
        const blob = new Blob(state.chunks, { type });
        state.chunks = [];
        if (blob.size === 0) {
            setStatus('No audio was captured. Hold the button a little longer.', 'error');
            return;
        }

        setBusy(true);
        setStatus('Transcribing and thinking…', 'busy');
        showTyping();
        const ext = type.includes('mp4') ? 'm4a' : type.includes('ogg') ? 'ogg' : 'webm';
        const formData = new FormData();
        formData.append('audio', blob, `recording.${ext}`);
        formData.append('language', languageSelect.value);

        try {
            const response = await fetch('/chat', { method: 'POST', body: formData });
            if (!response.ok) {
                const body = await response.json().catch(() => ({}));
                throw new Error(body.error || `The server returned an error (${response.status}).`);
            }
            const userText = hexToString(response.headers.get('X-User-Transcript'));
            const agentText = hexToString(response.headers.get('X-Agent-Transcript'));
            if (userText) addMessage(userText, 'user');
            if (agentText) addMessage(agentText, 'ai');

            const audioBlob = await response.blob();
            const latency = elapsed();
            setStatus(`Answered in ${latency}s`, 'success');
            playBlob(audioBlob);
        } catch (err) {
            console.warn('Batch request failed:', err);
            removeTyping();
            const offline = err instanceof TypeError;
            setStatus(offline ? 'Network error. Is the server running?' : err.message, 'error');
        } finally {
            setBusy(false);
        }
    }

    // ---- streaming --------------------------------------------------------
    function startStreaming() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const ws = new WebSocket(`${protocol}//${window.location.host}/ws/chat`);
        ws.binaryType = 'arraybuffer';
        state.ws = ws;

        const audioCtx = ensureAudioContext();
        const player = audioCtx ? new PcmStreamPlayer(audioCtx) : null;
        state.player = player;
        const fallbackChunks = [];
        let agentBubble = null;
        let finished = false;
        let cancelled = false;
        let firstAudio = true;

        ws.addEventListener('open', () => {
            if (!state.recording) {
                // Released before the socket opened.
                cancelled = true;
                ws.close();
                setStatus('Cancelled. Hold the button a little longer.');
                return;
            }
            ws.send(JSON.stringify({ language: languageSelect.value, use_llm: state.mode === 'stream_llm' }));
            state.recorder.start(RECORDER_TIMESLICE_MS);
        });

        ws.addEventListener('message', (event) => {
            if (typeof event.data !== 'string') {
                if (firstAudio) {
                    firstAudio = false;
                    setStatus(`Speaking… first audio after ${elapsed()}s`, 'speaking');
                }
                if (player) player.push(event.data);
                else fallbackChunks.push(event.data);
                return;
            }
            let msg;
            try {
                msg = JSON.parse(event.data);
            } catch (_) {
                return;
            }
            if (msg.type === 'transcript') {
                if (msg.speaker === 'user') {
                    addMessage(msg.text, 'user');
                    setStatus('Thinking…', 'busy');
                    showTyping();
                } else if (agentBubble) {
                    agentBubble.textContent = msg.text;
                    scrollToBottom();
                } else {
                    agentBubble = addMessage(msg.text, 'ai');
                }
            } else if (msg.type === 'audio_end') {
                finished = true;
                const latency = elapsed();
                const done = () => {
                    if (state.ws === ws && !state.recording)
                        setStatus(`Answered · first audio in ${latency}s`, 'success');
                };
                if (!player && fallbackChunks.length) {
                    playBlob(new Blob(fallbackChunks, { type: 'audio/wav' }), done);
                } else {
                    window.setTimeout(done, player ? player.remaining() * 1000 : 0);
                }
            } else if (msg.type === 'error') {
                finished = true;
                removeTyping();
                setStatus(msg.message || 'Something went wrong.', 'error');
            }
        });

        ws.addEventListener('error', () => {
            removeTyping();
            setStatus('Connection error. Is the server running?', 'error');
        });

        ws.addEventListener('close', () => {
            if (state.recorder && state.recorder.state === 'recording') state.recorder.stop();
            state.recording = false;
            updateRecordingUi(false);
            setBusy(false);
            removeTyping();
            if (!finished && !cancelled && state.status !== 'error') {
                setStatus('The connection closed before a reply arrived. Please try again.', 'error');
            }
        });
    }

    function onRecorderStop() {
        if (state.mode === 'batch') {
            sendBatch();
        } else if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            // An empty binary frame tells the server the utterance is complete.
            state.ws.send(new ArrayBuffer(0));
            setBusy(true);
            setStatus('Transcribing…', 'busy');
        }
    }

    // ---- press-and-hold control --------------------------------------------
    function updateRecordingUi(recording) {
        recordBtn.classList.toggle('recording', recording);
        recordBtn.setAttribute('aria-pressed', String(recording));
        const label = recording ? RECORDING_LABEL : IDLE_LABEL;
        recordLabel.textContent = label;
        recordBtn.setAttribute('aria-label', label);
        modeButtons.forEach((btn) => {
            btn.disabled = recording || state.busy;
        });
        languageSelect.disabled = recording || state.busy;
    }

    async function startRecording() {
        if (state.recording || state.busy) return;
        state.recording = true; // set early so a quick release during permission prompt cancels
        stopPlayback();
        ensureAudioContext();

        if (!(await ensureMicrophone())) {
            state.recording = false;
            return;
        }
        if (!state.recording) {
            setStatus('Microphone ready. Hold the button to speak.');
            return;
        }
        if (!state.recorder) state.recorder = createRecorder();

        state.startTime = performance.now();
        updateRecordingUi(true);
        setStatus('Listening…', 'listening');

        if (state.mode === 'batch') {
            state.chunks = [];
            state.recorder.start();
        } else {
            startStreaming();
        }
    }

    function stopRecording() {
        if (!state.recording) return;
        state.recording = false;
        updateRecordingUi(false);
        if (state.recorder && state.recorder.state === 'recording') {
            state.recorder.stop();
        }
    }

    recordBtn.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        recordBtn.setPointerCapture(event.pointerId);
        startRecording();
    });
    recordBtn.addEventListener('pointerup', stopRecording);
    recordBtn.addEventListener('pointercancel', stopRecording);
    recordBtn.addEventListener('contextmenu', (event) => event.preventDefault());

    recordBtn.addEventListener('keydown', (event) => {
        if ((event.key === ' ' || event.key === 'Enter') && !event.repeat) {
            event.preventDefault();
            startRecording();
        }
    });
    recordBtn.addEventListener('keyup', (event) => {
        if (event.key === ' ' || event.key === 'Enter') {
            event.preventDefault();
            stopRecording();
        }
    });
    recordBtn.addEventListener('blur', stopRecording);
});
