'use strict';

const STORAGE_KEY = 'openai_api_key';
const DEFAULT_PROMPT = 'Explain the history of artificial intelligence in 3 paragraphs.';

function safeStorage(action) {
    try {
        return action(window.localStorage);
    } catch (_) {
        return null; // storage may be unavailable (private mode, blocked cookies)
    }
}

function cell(text, className) {
    const td = document.createElement('td');
    td.textContent = text;
    if (className) td.className = className;
    return td;
}

function renderResults(tbody, results) {
    tbody.replaceChildren();
    let rank = 0;
    results.forEach((result) => {
        const tr = document.createElement('tr');
        if (result.error) {
            tr.append(cell('–'), cell(result.model));
            const err = cell(`Error: ${result.error}`, 'error-cell');
            err.colSpan = 3;
            tr.append(err);
        } else {
            rank += 1;
            tr.append(
                cell(`#${rank}`, rank <= 3 ? `rank-${rank}` : ''),
                cell(result.model),
                cell(result.ttft.toFixed(3), 'num'),
                cell(result.tps.toFixed(2), 'num'),
                cell(result.total_time.toFixed(2), 'num'),
            );
        }
        tbody.appendChild(tr);
    });
}

function setFieldError(errorEl, control, message) {
    errorEl.textContent = message || '';
    errorEl.hidden = !message;
    if (message) control.setAttribute('aria-invalid', 'true');
    else control.removeAttribute('aria-invalid');
}

document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('benchmark-form');
    const runBtn = document.getElementById('run-benchmark-btn');
    const runLabel = runBtn.querySelector('.btn-label');
    const apiKeyInput = document.getElementById('api-key');
    const apiKeyError = document.getElementById('api-key-error');
    const rememberKey = document.getElementById('remember-key');
    const promptInput = document.getElementById('test-prompt');
    const modelsGrid = document.getElementById('models-grid');
    const modelsError = document.getElementById('models-error');
    const statusEl = document.getElementById('status-indicator');
    const resultsEmpty = document.getElementById('results-empty');
    const tableWrap = document.getElementById('results-table-wrap');
    const disabledNotice = document.getElementById('benchmark-disabled');
    const tbody = document.querySelector('#results-table tbody');

    const savedKey = safeStorage((s) => s.getItem(STORAGE_KEY));
    if (savedKey) {
        apiKeyInput.value = savedKey;
        rememberKey.checked = true;
    }

    const setStatus = (text, tone = '') => {
        statusEl.textContent = text;
        statusEl.classList.toggle('error', tone === 'error');
        statusEl.classList.toggle('success', tone === 'success');
    };

    const setRunning = (running) => {
        runBtn.disabled = running;
        runBtn.setAttribute('aria-busy', String(running));
        runLabel.textContent = running ? 'Running…' : 'Run benchmark';
        form.setAttribute('aria-busy', String(running));
    };

    const selectedModels = () => Array.from(form.querySelectorAll('input[name="model"]:checked')).map((cb) => cb.value);

    // Clear validation messages as soon as the user fixes the problem.
    modelsGrid.addEventListener('change', () => {
        if (selectedModels().length > 0) setFieldError(modelsError, modelsGrid, '');
    });
    apiKeyInput.addEventListener('input', () => setFieldError(apiKeyError, apiKeyInput, ''));

    fetch('/health', { cache: 'no-store' })
        .then((response) => (response.ok ? response.json() : null))
        .then((health) => {
            if (health && health.benchmark_enabled === false) {
                disabledNotice.hidden = false;
                runBtn.disabled = true;
            }
        })
        .catch(() => {
            setStatus('Cannot reach the server. Make sure the backend is running.', 'error');
        });

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const apiKey = apiKeyInput.value.trim();
        const models = selectedModels();

        if (models.length === 0) {
            setFieldError(modelsError, modelsGrid, 'Select at least one model to test.');
            modelsGrid.querySelector('input').focus();
            return;
        }

        safeStorage((s) =>
            rememberKey.checked && apiKey ? s.setItem(STORAGE_KEY, apiKey) : s.removeItem(STORAGE_KEY),
        );

        setRunning(true);
        setStatus(`Benchmarking ${models.length} model${models.length === 1 ? '' : 's'}…`);

        try {
            const response = await fetch('/benchmark_stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    api_key: apiKey || null,
                    models,
                    prompt: promptInput.value.trim() || DEFAULT_PROMPT,
                }),
            });
            const body = await response.json().catch(() => null);
            if (!response.ok) {
                const message =
                    body && typeof body.error === 'string' ? body.error : `Server returned ${response.status}`;
                if (response.status === 400 && /api key/i.test(message)) {
                    setFieldError(
                        apiKeyError,
                        apiKeyInput,
                        'Enter an OpenAI API key. This server has no key of its own.',
                    );
                    apiKeyInput.focus();
                }
                throw new Error(message);
            }
            const results = body || [];
            renderResults(tbody, results);
            resultsEmpty.hidden = results.length > 0;
            tableWrap.hidden = results.length === 0;
            const failures = results.filter((r) => r.error).length;
            if (results.length > 0 && failures === results.length) {
                setStatus('Every model failed. Check the key and model names.', 'error');
            } else if (failures > 0) {
                setStatus(`Done, but ${failures} model${failures === 1 ? '' : 's'} failed.`, 'error');
            } else {
                setStatus('Benchmark complete.', 'success');
            }
        } catch (err) {
            console.warn('Benchmark error:', err);
            const offline = err instanceof TypeError;
            setStatus(offline ? 'Network error. Is the server running?' : `Benchmark failed: ${err.message}`, 'error');
        } finally {
            setRunning(false);
        }
    });
});
