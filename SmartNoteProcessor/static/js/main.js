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

// Add event listeners for forms
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
