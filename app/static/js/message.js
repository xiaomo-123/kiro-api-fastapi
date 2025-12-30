// 消息提示工具函数
export function showMessage(message, type = 'info', duration = 3000) {
    const container = document.getElementById('message-container');
    if (!container) {
        console.error('消息容器不存在');
        return;
    }

    // 创建消息元素
    const messageEl = document.createElement('div');
    messageEl.className = `message ${type}`;
    messageEl.textContent = message;

    // 添加到容器
    container.appendChild(messageEl);

    // 定时移除
    setTimeout(() => {
        messageEl.style.animation = 'fadeOut 0.3s ease';
        setTimeout(() => {
            if (container.contains(messageEl)) {
                container.removeChild(messageEl);
            }
        }, 300);
    }, duration);
}

// 便捷方法
export function showSuccess(message, duration) {
    showMessage(message, 'success', duration);
}

export function showError(message, duration) {
    showMessage(message, 'error', duration);
}

export function showWarning(message, duration) {
    showMessage(message, 'warning', duration);
}

export function showInfo(message, duration) {
    showMessage(message, 'info', duration);
}

// 暴露到全局作用域
window.showMessage = showMessage;
window.showSuccess = showSuccess;
window.showError = showError;
window.showWarning = showWarning;
window.showInfo = showInfo;
