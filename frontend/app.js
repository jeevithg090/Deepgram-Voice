document.addEventListener('DOMContentLoaded', () => {
    const recordBtn = document.getElementById('record-btn');
    const statusIndicator = document.getElementById('status');
    const chatBox = document.getElementById('chat-box');
    const languageSelect = document.getElementById('language-select');
    const modeBatchBtn = document.getElementById('mode-batch');
    const modeStreamBtn = document.getElementById('mode-stream');
    const modeStreamLlmBtn = document.getElementById('mode-stream-llm');

    let currentMode = 'batch';
    let mediaRecorder;
    let audioChunks = [];
    let isRecording = false;
    let ws = null;
    let startTime = 0;
    
    let streamAudioChunks = [];
    let streamTextBuffer = "";

    // Toggle Modes
    modeBatchBtn.addEventListener('click', () => {
        currentMode = 'batch';
        modeBatchBtn.classList.add('active');
        modeStreamBtn.classList.remove('active');
        modeStreamLlmBtn.classList.remove('active');
    });

    modeStreamBtn.addEventListener('click', () => {
        currentMode = 'stream';
        modeStreamBtn.classList.add('active');
        modeBatchBtn.classList.remove('active');
        modeStreamLlmBtn.classList.remove('active');
    });

    modeStreamLlmBtn.addEventListener('click', () => {
        currentMode = 'stream_llm';
        modeStreamLlmBtn.classList.add('active');
        modeBatchBtn.classList.remove('active');
        modeStreamBtn.classList.remove('active');
    });

    function hexToString(hex) {
        let str = '';
        for (let i = 0; i < hex.length; i += 2) {
            str += String.fromCharCode(parseInt(hex.substr(i, 2), 16));
        }
        return decodeURIComponent(escape(str));
    }

    function addMessage(text, sender) {
        const messageDiv = document.createElement('div');
        messageDiv.classList.add('message', `${sender}-message`);
        const bubble = document.createElement('div');
        bubble.classList.add('bubble');
        bubble.textContent = text;
        messageDiv.appendChild(bubble);
        chatBox.appendChild(messageDiv);
        chatBox.parentElement.scrollTop = chatBox.parentElement.scrollHeight;
    }

    // Request microphone access
    navigator.mediaDevices.getUserMedia({ audio: true })
        .then(stream => {
            mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });

            mediaRecorder.ondataavailable = event => {
                if (event.data.size > 0) {
                    if (currentMode === 'batch') {
                        audioChunks.push(event.data);
                    } else if ((currentMode === 'stream' || currentMode === 'stream_llm') && ws && ws.readyState === WebSocket.OPEN) {
                        // Stream directly to backend
                        ws.send(event.data);
                    }
                }
            };

            mediaRecorder.onstop = async () => {
                if (currentMode === 'batch') {
                    const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                    audioChunks = [];

                    statusIndicator.textContent = "Processing (Batch)...";
                    
                    const formData = new FormData();
                    formData.append('audio', audioBlob, 'recording.webm');
                    formData.append('language', languageSelect.value);

                    try {
                        const response = await fetch('/chat', {
                            method: 'POST',
                            body: formData
                        });

                        if (!response.ok) throw new Error('Server returned ' + response.status);

                        const userHex = response.headers.get('X-User-Transcript');
                        const agentHex = response.headers.get('X-Agent-Transcript');
                        
                        if (userHex) addMessage(hexToString(userHex), 'user');
                        if (agentHex) addMessage(hexToString(agentHex), 'ai');

                        const responseBlob = await response.blob();
                        const latency = ((Date.now() - startTime) / 1000).toFixed(2);
                        statusIndicator.textContent = `Batch Latency: ${latency}s`;

                        const audioUrl = URL.createObjectURL(responseBlob);
                        const audio = new Audio(audioUrl);
                        audio.play();

                    } catch (error) {
                        console.error("Error:", error);
                        statusIndicator.textContent = "Error processing audio.";
                    }
                } else if (currentMode === 'stream' || currentMode === 'stream_llm') {
                    // Send empty blob to indicate end of stream without closing socket
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(new Blob([]));
                        statusIndicator.textContent = 'Processing (Streaming)...';
                    }
                }
            };
        })
        .catch(err => {
            console.error("Microphone access denied:", err);
            statusIndicator.textContent = "Microphone access required.";
        });

    function startStreaming() {
        streamAudioChunks = [];
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        ws = new WebSocket(`${protocol}//${window.location.host}/ws/chat`);
        ws.binaryType = "blob";

        ws.onopen = () => {
            if (!isRecording) {
                // User already released the button before connection opened
                ws.close();
                return;
            }
            const use_llm = (currentMode === 'stream_llm');
            ws.send(JSON.stringify({ language: languageSelect.value, use_llm: use_llm }));
            // Start recording and sending chunks every 100ms
            mediaRecorder.start(100);
        };

        ws.onmessage = (event) => {
            if (typeof event.data === "string") {
                const msg = JSON.parse(event.data);
                if (msg.type === "transcript") {
                    addMessage(msg.text, msg.speaker === "user" ? "user" : "ai");
                } else if (msg.type === "audio_start") {
                    statusIndicator.textContent = 'Agent speaking...';
                } else if (msg.type === "audio_end") {
                    const audioBlob = new Blob(streamAudioChunks, { type: 'audio/wav' });
                    const audioUrl = URL.createObjectURL(audioBlob);
                    const audio = new Audio(audioUrl);
                    audio.play();
                }
            } else {
                // Incoming binary audio chunk
                streamAudioChunks.push(event.data);
            }
        };

        ws.onerror = (error) => {
            console.error("WebSocket Error: ", error);
            statusIndicator.textContent = 'Connection error.';
        };

        ws.onclose = () => {
            if (statusIndicator.textContent === 'Processing (Streaming)...' || statusIndicator.textContent === 'Listening...') {
                statusIndicator.textContent = 'No speech detected or connection closed.';
            }
        };
    }

    recordBtn.addEventListener('mousedown', () => {
        if (mediaRecorder && mediaRecorder.state === 'inactive') {
            startTime = Date.now();
            isRecording = true;
            recordBtn.classList.add('recording');
            recordBtn.querySelector('span').textContent = 'Recording...';
            statusIndicator.textContent = 'Listening...';
            
            if (currentMode === 'batch') {
                audioChunks = [];
                mediaRecorder.start();
            } else {
                startStreaming();
            }
        }
    });

    const stopRecording = () => {
        if (!isRecording) return;
        isRecording = false;
        recordBtn.classList.remove('recording');
        recordBtn.querySelector('span').textContent = 'Press & Hold to Speak';

        if (mediaRecorder && mediaRecorder.state === 'recording') {
            mediaRecorder.stop();
        } else if (mediaRecorder && mediaRecorder.state === 'inactive') {
            // User released before it started
            statusIndicator.textContent = 'Cancelled.';
        }
    }

    recordBtn.addEventListener('mouseup', stopRecording);
    recordBtn.addEventListener('mouseleave', stopRecording);
});
