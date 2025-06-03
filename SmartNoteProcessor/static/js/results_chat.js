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
            e.stopImmediatePropagation(); // Blocca altri listener sullo stesso form
        }, true);
    }

    function addMessageToChat(sender, message, isHtml = false) {
        if (!chatDisplay) return;
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
        chatForm.addEventListener('submit', function(event) {
            event.preventDefault();
            const userMessage = userInstructionInput.value.trim();
            const documentId = documentIdInput.value;
            if (!userMessage) return;
            addMessageToChat('user', userMessage);
            const originalButtonText = chatSubmitButton.innerHTML;
            userInstructionInput.disabled = true;
            chatSubmitButton.disabled = true;
            chatSubmitButton.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Invio...';
            const formData = new FormData(chatForm);
            fetch(chatForm.action, {
                method: 'POST',
                body: formData
            })
            .then(response => response.json())
            .then(data => {
                if (data.ai_message) {
                    addMessageToChat('ai', data.ai_message, true);
                } else if (data.error) {
                    addMessageToChat('ai', `Errore: ${data.error}`, false);
                }
            })
            .catch(error => {
                addMessageToChat('ai', "Errore di comunicazione con il server.", false);
            })
            .finally(() => {
                userInstructionInput.value = '';
                userInstructionInput.disabled = false;
                chatSubmitButton.disabled = false;
                chatSubmitButton.innerHTML = originalButtonText;
                userInstructionInput.focus();
            });
        });
    }
});