// API Key管理模块

// 生成随机API Key
function generateApiKey() {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    let result = '';
    for (let i = 0; i < 32; i++) {
        result += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    document.getElementById('apikeys-api_key').value = result;
}

// 立即将函数暴露到全局作用域
window.generateApiKey = generateApiKey;

export async function loadApiKeys() {
    try {
        const response = await fetch('/api/management/apikeys');
        const apikeys = await response.json();

        const tbody = document.querySelector('#apikeys-table tbody');
        tbody.innerHTML = '';

        apikeys.forEach(apikey => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${apikey.id}</td>
                <td>${apikey.api_key}</td>
                <td>${apikey.description || ''}</td>
                <td>${apikey.status === '1' ? '启用' : '禁用'}</td>
                <td>
                    <button class="btn-edit" onclick="editApiKey(${apikey.id})">编辑</button>
                    <button class="btn-delete" onclick="deleteApiKey(${apikey.id})">删除</button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (error) {
        console.error('加载API Key列表失败:', error);
        showError('加载API Key列表失败');
    }
}

export function initApiKeyForm() {
    const form = document.getElementById('form-apikeys');

    form.addEventListener('submit', async function(e) {
        e.preventDefault();

        const id = document.getElementById('apikeys-id').value;
        const api_key = document.getElementById('apikeys-api_key').value;
        const description = document.getElementById('apikeys-description').value;
        const status = document.getElementById('apikeys-status').value;

        // 验证API Key字段
        if (!api_key || api_key.trim() === '') {
            showError('API Key不能为空');
            return;
        }

        try {
            let response;
            if (id) {
                // 更新API Key
                response = await fetch(`/api/management/apikeys/${id}`, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ api_key: api_key.trim(), description, status })
                });
            } else {
                // 创建API Key
                response = await fetch('/api/management/apikeys', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ api_key: api_key.trim(), description, status })
                });
            }

            if (response.ok) {
                closeModal('apikeys');
                loadApiKeys();
                showSuccess('操作成功');
            } else {
                const data = await response.json();
                showError(data.detail || '操作失败');
            }
        } catch (error) {
            console.error('操作失败:', error);
            showError('操作失败');
        }
    });
}

export async function editApiKey(id) {
    try {
        const response = await fetch(`/api/management/apikeys/${id}`);
        const apikey = await response.json();

        document.getElementById('apikeys-id').value = apikey.id;
        document.getElementById('apikeys-api_key').value = apikey.api_key;
        document.getElementById('apikeys-api_key').disabled = false;
        document.getElementById('apikeys-description').value = apikey.description || '';
        document.getElementById('apikeys-status').value = apikey.status || '1';

        document.getElementById('modal-apikeys-title').textContent = '编辑API Key';
        document.getElementById('modal-apikeys').classList.add('active');
    } catch (error) {
        console.error('加载API Key信息失败:', error);
        showError('加载API Key信息失败');
    }
}

window.editApiKey = editApiKey;

export async function deleteApiKey(id) {
    if (!confirm('确定要删除此API Key吗？')) {
        return;
    }

    try {
        const response = await fetch(`/api/management/apikeys/${id}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            loadApiKeys();
            showSuccess('删除成功');
        } else {
            const data = await response.json();
            showError(data.detail || '删除失败');
        }
    } catch (error) {
        console.error('删除失败:', error);
        showError('删除失败');
    }
}

window.deleteApiKey = deleteApiKey;
