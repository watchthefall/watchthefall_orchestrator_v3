// Dashboard JavaScript for WatchTheFall Portal

// Function to show status messages
function showStatus(message, type) {
    const statusMessage = document.getElementById('statusMessage');
    if (statusMessage) {
        statusMessage.textContent = message;
        statusMessage.className = 'status-message';
        statusMessage.classList.add(`status-${type}`);
        statusMessage.style.display = 'block';
        
        // Hide status after 5 seconds for success messages
        if (type === 'success') {
            setTimeout(() => {
                statusMessage.style.display = 'none';
            }, 5000);
        }
    }
}

// Function to display results
function showResults(results) {
    const resultsList = document.getElementById('resultsList');
    const resultsPanel = document.getElementById('resultsPanel');
    
    if (resultsList && resultsPanel) {
        resultsList.innerHTML = '';
        resultsPanel.classList.add('active');
        
        results.forEach(result => {
            const resultItem = document.createElement('div');
            resultItem.className = 'result-item';
            
            if (result.success) {
                resultItem.innerHTML = `
                    <div class="result-title">${result.filename}</div>
                    <div class="result-meta">Size: ${result.size_mb} MB</div>
                    <a href="${result.download_url}" class="download-link" target="_blank">Download Video</a>
                `;
            } else {
                resultItem.innerHTML = `
                    <div class="result-title">Download Failed</div>
                    <div class="result-meta">URL: ${result.url}</div>
                    <div style="color: #ff5555;">Error: ${result.error}</div>
                `;
            }
            
            resultsList.appendChild(resultItem);
        });
    }
}

// Function to download video
async function downloadVideo() {
    const videoUrlInput = document.getElementById('videoUrl');
    const downloadBtn = document.getElementById('downloadBtn');
    
    if (!videoUrlInput || !downloadBtn) return;
    
    const url = videoUrlInput.value.trim();
    
    if (!url) {
        showStatus('Please enter a video URL', 'error');
        return;
    }
    
    showStatus('Downloading video...', 'loading');
    downloadBtn.disabled = true;
    
    try {
        const response = await fetch('/api/videos/fetch', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ urls: [url] })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showStatus('Download completed successfully!', 'success');
            showResults(data.results);
        } else {
            showStatus(`Error: ${data.error}`, 'error');
        }
    } catch (error) {
        showStatus(`Network error: ${error.message}`, 'error');
    } finally {
        downloadBtn.disabled = false;
    }
}

// Initialize dashboard when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    const downloadBtn = document.getElementById('downloadBtn');
    if (downloadBtn) {
        downloadBtn.addEventListener('click', downloadVideo);
    }
});