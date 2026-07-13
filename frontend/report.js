document.addEventListener('DOMContentLoaded', () => {
    const runBtn = document.getElementById('run-benchmark-btn');
    const statusIndicator = document.getElementById('status-indicator');
    const resultsSection = document.getElementById('results-section');
    const resultsTableBody = document.querySelector('#results-table tbody');
    
    // Check if key is saved in localStorage
    const savedKey = localStorage.getItem('openai_api_key');
    if (savedKey) {
        document.getElementById('api-key').value = savedKey;
    }

    runBtn.addEventListener('click', async () => {
        const apiKey = document.getElementById('api-key').value.trim();
        const testPrompt = document.getElementById('test-prompt').value.trim();
        
        if (!apiKey) {
            alert('Please enter an OpenAI API Key.');
            return;
        }
        
        // Save key for convenience
        localStorage.setItem('openai_api_key', apiKey);
        
        const checkboxes = document.querySelectorAll('.models-grid input[type="checkbox"]:checked');
        const selectedModels = Array.from(checkboxes).map(cb => cb.value);
        
        if (selectedModels.length === 0) {
            alert('Please select at least one model to test.');
            return;
        }
        
        runBtn.disabled = true;
        statusIndicator.style.display = 'block';
        resultsSection.style.display = 'none';
        resultsTableBody.innerHTML = '';
        
        try {
            const response = await fetch('/benchmark_stream', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    api_key: apiKey,
                    models: selectedModels,
                    prompt: testPrompt || "Explain the history of artificial intelligence in 3 paragraphs."
                })
            });
            
            if (!response.ok) {
                throw new Error('Server returned ' + response.status);
            }
            
            const results = await response.json();
            
            results.forEach((result, index) => {
                const tr = document.createElement('tr');
                let rankClass = '';
                if (index === 0) rankClass = 'rank-1';
                else if (index === 1) rankClass = 'rank-2';
                else if (index === 2) rankClass = 'rank-3';
                
                if (result.error) {
                    tr.innerHTML = `
                        <td>-</td>
                        <td>${result.model}</td>
                        <td colspan="3" style="color: #ff3333;">Error: ${result.error}</td>
                    `;
                } else {
                    tr.innerHTML = `
                        <td class="${rankClass}">#${index + 1}</td>
                        <td>${result.model}</td>
                        <td>${result.ttft.toFixed(3)}</td>
                        <td>${result.tps.toFixed(2)}</td>
                        <td>${result.total_time.toFixed(2)}</td>
                    `;
                }
                
                resultsTableBody.appendChild(tr);
            });
            
            resultsSection.style.display = 'block';
            
        } catch (error) {
            console.error("Benchmark Error:", error);
            alert("An error occurred while running the benchmark: " + error.message);
        } finally {
            runBtn.disabled = false;
            statusIndicator.style.display = 'none';
        }
    });
});
