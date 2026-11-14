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

document.addEventListener('DOMContentLoaded', () => {
    const recordBtn = document.getElementById('record-btn');
    const recordLabel = recordBtn.querySelector('.record-label');
    const statusEl = document.getElementById('status');
    const chatBox = document.getElementById('chat-box');
    const chatScroll = document.getElementById('chat-scroll');
    const languageSelect = document.getElementById('language-select');
    const modeButtons = Array.from(document.querySelectorAll('.mode-btn'));

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
    };

    const setStatus = (text, isError = false) => {
        statusEl.textContent = text;
        statusEl.classList.toggle('error', isError);
    };

    const setBusy = (busy) => {
        state.busy = busy;
        recordBtn.disabled = busy;
        recordBtn.setAttribute('aria-busy', String(busy));
        modeButtons.forEach((btn) => {
            btn.disabled = busy;
        });
        languageSelect.disabled = busy;
    };

    function addMessage(text, sender) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${sender}-message`;
        const bubble = document.createElement('div');
        bubble.className = 'bubble';
        bubble.textContent = text;
        messageDiv.appendChild(bubble);
        chatBox.appendChild(messageDiv);
        chatScroll.scrollTop = chatScroll.scrollHeight;
        return bubble;
    }

    function stopPlayback() {
        if (state.player) state.player.stop();
        if (state.htmlAudio) {
            state.htmlAudio.pause();
            state.htmlAudio = null;
        }
    }

    function playBlob(blob) {
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        state.htmlAudio = audio;
        audio.addEventListener('ended', () => URL.revokeObjectURL(url), { once: true });
        audio.play().catch((err) => {
            console.error('Playback failed:', err);
            setStatus('Could not play the audio response.', true);
        });
    }

    // ---- mode selection ---------------------------------------------------
    modeButtons.forEach((btn) => {
        btn.addEventListener('click', () => {
            state.mode = btn.dataset.mode;
            modeButtons.forEach((other) => {
                const active = other === btn;
                other.classList.toggle('active', active);
                other.setAttribute('aria-pressed', String(active));
            });
        });
    });

    // ---- microphone -------------------------------------------------------
    async function ensureMicrophone() {
        if (state.micStream) return true;
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === 'undefined') {
            setStatus('This browser does not support audio recording.', true);
            return false;
        }
        try {
            state.micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
            state.mimeType = pickMimeType();
            return true;
        } catch (err) {
            console.error('Microphone access denied:', err);
            setStatus('Microphone access is required. Allow it in your browser settings.', true);
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

    // ---- batch ------------------------------------------------------------
    async function sendBatch() {
        const type = state.mimeType || 'audio/webm';
        const blob = new Blob(state.chunks, { type });
        state.chunks = [];
        if (blob.size === 0) {
            setStatus('No audio captured. Hold the button a little longer.', true);
            return;
        }

        setBusy(true);
        setStatus('Processing…');
        const ext = type.includes('mp4') ? 'm4a' : type.includes('ogg') ? 'ogg' : 'webm';
        const formData = new FormData();
        formData.append('audio', blob, `recording.${ext}`);
        formData.append('language', languageSelect.value);

        try {
            const response = await fetch('/chat', { method: 'POST', body: formData });
            if (!response.ok) {
                const body = await response.json().catch(() => ({}));
                throw new Error(body.error || `Server returned ${response.status}`);
            }
            const userText = hexToString(response.headers.get('X-User-Transcript'));
            const agentText = hexToString(response.headers.get('X-Agent-Transcript'));
            if (userText) addMessage(userText, 'user');
            if (agentText) addMessage(agentText, 'ai');

            const audioBlob = await response.blob();
            const latency = ((performance.now() - state.startTime) / 1000).toFixed(2);
            setStatus(`Batch round trip: ${latency}s`);
            playBlob(audioBlob);
        } catch (err) {
            console.error('Batch request failed:', err);
            setStatus(err.message || 'Error processing audio.', true);
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
        let firstAudio = true;

        ws.addEventListener('open', () => {
            if (!state.recording) {
                // Released before the socket opened.
                ws.close();
                setStatus('Cancelled.');
                return;
            }
            ws.send(JSON.stringify({ language: languageSelect.value, use_llm: state.mode === 'stream_llm' }));
            state.recorder.start(RECORDER_TIMESLICE_MS);
        });

        ws.addEventListener('message', (event) => {
            if (typeof event.data !== 'string') {
                if (firstAudio) {
                    firstAudio = false;
                    const latency = ((performance.now() - state.startTime) / 1000).toFixed(2);
                    setStatus(`Agent speaking… (first audio after ${latency}s)`);
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
                    setStatus('Thinking…');
                } else if (agentBubble) {
                    agentBubble.textContent = msg.text;
                    chatScroll.scrollTop = chatScroll.scrollHeight;
                } else {
                    agentBubble = addMessage(msg.text, 'ai');
                }
            } else if (msg.type === 'audio_end') {
                finished = true;
                if (!player && fallbackChunks.length) playBlob(new Blob(fallbackChunks, { type: 'audio/wav' }));
                const wait = player ? player.remaining() * 1000 : 0;
                window.setTimeout(() => {
                    if (state.ws === ws) setStatus('Ready');
                }, wait);
            } else if (msg.type === 'error') {
                finished = true;
                setStatus(msg.message || 'Something went wrong.', true);
            }
        });

        ws.addEventListener('error', () => {
            setStatus('Connection error. Is the server running?', true);
        });

        ws.addEventListener('close', () => {
            if (state.recorder && state.recorder.state === 'recording') state.recorder.stop();
            state.recording = false;
            updateRecordingUi(false);
            setBusy(false);
            if (!finished && !statusEl.classList.contains('error') && statusEl.textContent !== 'Cancelled.') {
                setStatus('Connection closed before a response was received.', true);
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
            setStatus('Transcribing…');
        }
    }

    // ---- press-and-hold control --------------------------------------------
    function updateRecordingUi(recording) {
        recordBtn.classList.toggle('recording', recording);
        recordBtn.setAttribute('aria-pressed', String(recording));
        recordLabel.textContent = recording ? 'Listening… release to send' : 'Hold to speak';
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
        setStatus('Listening…');

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
