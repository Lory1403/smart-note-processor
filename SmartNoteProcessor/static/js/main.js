// Smart Notes Processor - Main JavaScript

// DOM elements
const fileInput = document.getElementById('file-input');
const uploadArea = document.getElementById('upload-area');
const uploadForm = document.getElementById('upload-form');
const granularitySlider = document.getElementById('granularity-slider');
const granularityValue = document.getElementById('granularity-value');
const granularityForm = document.getElementById('granularity-form');
const loadingOverlay = document.getElementById('loading-overlay');

// Initialize the application when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    initializeFileUpload();
    initializeGranularitySlider();
    initializeFormatSelector();
    addEventListeners();

    const mergeForm = document.getElementById('merge-form');
    if (mergeForm) {
        const checkboxes = mergeForm.querySelectorAll('input[type="checkbox"][name="selected_topics"]');
        const mergeBtn = document.getElementById('merge-btn');
        checkboxes.forEach(cb => {
            cb.addEventListener('change', function() {
                const checkedCount = Array.from(checkboxes).filter(c => c.checked).length;
                mergeBtn.disabled = checkedCount < 2;
            });
        });
    }
});

// Initialize file upload functionality
function initializeFileUpload() {
    if (uploadArea && fileInput) {
        // Trigger file input when upload area is clicked
        uploadArea.addEventListener('click', function() {
            fileInput.click();
        });

        // Display file names or count when selected
        fileInput.addEventListener('change', function() {
            displayFileInfo(fileInput.files); // Use helper function
        });

        // Handle drag and drop
        uploadArea.addEventListener('dragover', function(e) {
            e.preventDefault();
            uploadArea.classList.add('border-info');
        });

        uploadArea.addEventListener('dragleave', function() {
            uploadArea.classList.remove('border-info');
        });

        uploadArea.addEventListener('drop', function(e) {
            e.preventDefault();
            uploadArea.classList.remove('border-info');
            
            if (e.dataTransfer.files.length > 0) {
                fileInput.files = e.dataTransfer.files;
                displayFileInfo(fileInput.files); // Use helper function
            }
        });
    }
}

// Helper function to display file info (count or names)
function displayFileInfo(files) {
    const fileInfo = document.getElementById('file-info');
    const submitBtn = document.getElementById('upload-btn');

    if (files.length > 0) {
        if (fileInfo) {
            // Display file count or list names (showing count here)
            fileInfo.textContent = `${files.length} file(s) selected`;
            fileInfo.classList.remove('d-none');
        }
        if (submitBtn) {
            submitBtn.disabled = false;
        }
    } else {
        if (fileInfo) {
            fileInfo.textContent = '';
            fileInfo.classList.add('d-none');
        }
        if (submitBtn) {
            submitBtn.disabled = true;
        }
    }
}

// Initialize granularity slider
function initializeGranularitySlider() {
    if (granularitySlider && granularityValue) {
        granularityValue.textContent = granularitySlider.value;
        
        granularitySlider.addEventListener('input', function() {
            granularityValue.textContent = this.value;
        });
    }
}

// Initialize format selector
function initializeFormatSelector() {
    const formatSelectors = document.querySelectorAll('.format-selector .nav-link');
    const formatInput = document.getElementById('format-input');
    
    if (formatSelectors && formatInput) {
        formatSelectors.forEach(selector => {
            selector.addEventListener('click', function(e) {
                e.preventDefault();
                
                // Remove active class from all selectors
                formatSelectors.forEach(s => s.classList.remove('active'));
                
                // Add active class to clicked selector
                this.classList.add('active');
                
                // Update hidden input
                formatInput.value = this.dataset.format;
            });
        });
    }
}

// Add event listeners for forms (EXCLUDING CHAT FORM)
function addEventListeners() {
    // Show loading overlay when forms are submitted
    if (uploadForm) {
        uploadForm.addEventListener('submit', function() {
            showLoadingOverlay();
        });
    }
    
    if (granularityForm) {
        granularityForm.addEventListener('submit', function() {
            showLoadingOverlay();
        });
    }
    
    const generateForm = document.getElementById('generate-form');
    if (generateForm) {
        generateForm.addEventListener('submit', function() {
            showLoadingOverlay();
        });
    }
}

// Show loading overlay
function showLoadingOverlay() {
    if (loadingOverlay) {
        loadingOverlay.style.display = 'flex';
    }
}

// Hide loading overlay
function hideLoadingOverlay() {
    if (loadingOverlay) {
        loadingOverlay.style.display = 'none';
    }
}

// Copy note content to clipboard
function copyToClipboard(elementId) {
    const element = document.getElementById(elementId);
    if (!element) return;
    
    const textArea = document.createElement('textarea');
    textArea.value = element.innerText;
    document.body.appendChild(textArea);
    textArea.select();
    document.execCommand('copy');
    document.body.removeChild(textArea);
    
    // Show tooltip
    const tooltip = document.getElementById('copy-tooltip');
    if (tooltip) {
        tooltip.classList.remove('d-none');
        setTimeout(() => {
            tooltip.classList.add('d-none');
        }, 2000);
    }
}

// Chat logic moved from results.html
document.addEventListener('DOMContentLoaded', function() {
    const chatForm = document.getElementById('chatForm');
    const userInstructionInput = document.getElementById('userInstructionInput');
    const chatSubmitButton = document.getElementById('chatSubmitButton');
    const chatDisplay = document.getElementById('chatDisplayArea');
    const emptyChatMessageElement = document.getElementById('emptyChatMessage');
    const documentIdInput = chatForm ? chatForm.querySelector('input[name="document_id"]') : null;

    // Protezione: blocca altri listener di submit (come quelli di main.js)
    if (chatForm) {
        chatForm.addEventListener('submit', function(e) {
            // e.stopImmediatePropagation(); // Consider uncommenting if other general form handlers in main.js might interfere
        }, true); // Ensure this listener is added in the capture phase if needed
    }

    function addMessageToChat(sender, message, isHtml = false) {
        if (!chatDisplay) {
            // console.error("[ChatScript-Moved] chatDisplay not found"); // Log from main.js context
            return;
        }
        if (emptyChatMessageElement && emptyChatMessageElement.parentNode === chatDisplay) {
            chatDisplay.removeChild(emptyChatMessageElement);
        }
        const messageWrapper = document.createElement('div');
        messageWrapper.classList.add('chat-message');
        const senderP = document.createElement('p');
        senderP.classList.add('message-sender');
        const messageP = document.createElement('p');
        if (sender === 'user') {
            messageWrapper.classList.add('user-message');
            senderP.textContent = 'Tu:';
            messageP.textContent = message;
        } else {
            messageWrapper.classList.add('ai-message');
            senderP.textContent = 'AI:';
            if (isHtml) messageP.innerHTML = message;
            else messageP.textContent = message;
        }
        messageWrapper.appendChild(senderP);
        messageWrapper.appendChild(messageP);
        chatDisplay.appendChild(messageWrapper);
        chatDisplay.scrollTop = chatDisplay.scrollHeight;
    }

    if (chatForm && userInstructionInput && chatSubmitButton && documentIdInput) {
        // console.log("[ChatScript-Moved] Attaching listener to chatForm from main.js");
        chatForm.addEventListener('submit', function(event) {
            // console.log("[ChatScript-Moved] Submit event triggered");
            event.preventDefault(); // ESSENTIAL
            // console.log("[ChatScript-Moved] event.preventDefault() called");

            const userMessage = userInstructionInput.value.trim();
            // const documentId = documentIdInput.value; // Already available
            if (!userMessage) {
                // console.log("[ChatScript-Moved] Empty user message");
                return;
            }
            addMessageToChat('user', userMessage);
            const originalButtonText = chatSubmitButton.innerHTML;
            userInstructionInput.disabled = true;
            chatSubmitButton.disabled = true;
            chatSubmitButton.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Invio...';
            
            const formData = new FormData(chatForm);
            
            // console.log("[ChatScript-Moved] Fetching:", chatForm.action);
            fetch(chatForm.action, {
                method: 'POST',
                body: formData
            })
            .then(response => {
                // console.log("[ChatScript-Moved] Fetch response status:", response.status);
                if (!response.ok) {
                    return response.text().then(text => {
                        throw new Error(`Server error: ${response.status} - ${text}`);
                    });
                }
                return response.json();
            })
            .then(data => {
                // console.log("[ChatScript-Moved] Fetch data received:", data);
                if (data.ai_message) {
                    addMessageToChat('ai', data.ai_message, true);
                } else if (data.error) {
                    addMessageToChat('ai', `Errore: ${data.error}`, false);
                } else {
                    addMessageToChat('ai', "Risposta inattesa dal server.", false);
                }
            })
            .catch(error => {
                // console.error("[ChatScript-Moved] Fetch error:", error);
                addMessageToChat('ai', `Errore di comunicazione: ${error.message}`, false);
            })
            .finally(() => {
                userInstructionInput.value = '';
                userInstructionInput.disabled = false;
                chatSubmitButton.disabled = false;
                chatSubmitButton.innerHTML = originalButtonText;
                userInstructionInput.focus();
                // console.log("[ChatScript-Moved] Fetch finished");
            });
        });
        // console.log("[ChatScript-Moved] Chat form listener attached from main.js.");
    } else {
        // This will log on pages other than results.html, which is expected.
        // console.log("[ChatScript-Moved] Chat elements not found on this page (expected if not results.html).");
    }
});
