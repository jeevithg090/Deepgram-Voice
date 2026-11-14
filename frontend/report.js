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
                cell(result.ttft.toFixed(3)),
                cell(result.tps.toFixed(2)),
                cell(result.total_time.toFixed(2)),
            );
        }
        tbody.appendChild(tr);
    });
}

document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('benchmark-form');
    const runBtn = document.getElementById('run-benchmark-btn');
    const apiKeyInput = document.getElementById('api-key');
    const rememberKey = document.getElementById('remember-key');
    const promptInput = document.getElementById('test-prompt');
    const statusEl = document.getElementById('status-indicator');
    const resultsSection = document.getElementById('results-section');
    const tbody = document.querySelector('#results-table tbody');

    const savedKey = safeStorage((s) => s.getItem(STORAGE_KEY));
    if (savedKey) {
        apiKeyInput.value = savedKey;
        rememberKey.checked = true;
    }

    const setStatus = (text, isError = false) => {
        statusEl.textContent = text;
        statusEl.classList.toggle('error', isError);
    };

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const apiKey = apiKeyInput.value.trim();
        const models = Array.from(form.querySelectorAll('input[name="model"]:checked')).map((cb) => cb.value);

        if (models.length === 0) {
            setStatus('Select at least one model to test.', true);
            return;
        }

        safeStorage((s) =>
            rememberKey.checked && apiKey ? s.setItem(STORAGE_KEY, apiKey) : s.removeItem(STORAGE_KEY),
        );

        runBtn.disabled = true;
        form.setAttribute('aria-busy', 'true');
        setStatus(`Running benchmark across ${models.length} model(s)… this can take a while.`);

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
                throw new Error(message);
            }
            renderResults(tbody, body || []);
            resultsSection.hidden = false;
            setStatus('Benchmark complete.');
        } catch (err) {
            console.error('Benchmark error:', err);
            setStatus(`Benchmark failed: ${err.message}`, true);
        } finally {
            runBtn.disabled = false;
            form.removeAttribute('aria-busy');
        }
    });
});
