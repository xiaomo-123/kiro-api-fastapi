// 代理管理模块
export async function loadProxies() {
    try {
        const response = await fetch('/api/management/proxies');
        const proxies = await response.json();

        const tbody = document.querySelector('#proxies-table tbody');
        tbody.innerHTML = '';

        proxies.forEach(proxy => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${proxy.id}</td>
                <td>${proxy.proxy_type}</td>
                <td>${proxy.proxy_url}</td>
                <td>${proxy.proxy_port || ''}</td>
                <td>${proxy.username || ''}</td>
                <td>
                    <button class="btn-edit" onclick="editProxy(${proxy.id})">编辑</button>
                    <button class="btn-delete" onclick="deleteProxy(${proxy.id})">删除</button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (error) {
        console.error('加载代理列表失败:', error);
        showError('加载代理列表失败');
    }
}

export function initProxyForm() {
    const form = document.getElementById('form-proxies');
    form.addEventListener('submit', async function(e) {
        e.preventDefault();

        const id = document.getElementById('proxies-id').value;
        const proxy_type = document.getElementById('proxies-proxy_type').value;
        const proxy_url = document.getElementById('proxies-proxy_url').value;
        const proxy_port = document.getElementById('proxies-proxy_port').value;
        const username = document.getElementById('proxies-username').value;
        const password = document.getElementById('proxies-password').value;

        // 验证必填字段
        if (!proxy_type || proxy_type.trim() === '') {
            alert('代理类型不能为空');
            return;
        }
        if (!proxy_url || proxy_url.trim() === '') {
            alert('代理URL不能为空');
            return;
        }

        try {
            let response;
            if (id) {
                // 更新代理
                response = await fetch(`/api/management/proxies/${id}`, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ 
                        proxy_type, 
                        proxy_url, 
                        proxy_port: proxy_port ? parseInt(proxy_port) : null,
                        username, 
                        password 
                    })
                });
            } else {
                // 创建代理
                response = await fetch('/api/management/proxies', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ 
                        proxy_type, 
                        proxy_url, 
                        proxy_port: proxy_port ? parseInt(proxy_port) : null,
                        username, 
                        password 
                    })
                });
            }

            if (response.ok) {
                closeModal('proxies');
                loadProxies();
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

export async function editProxy(id) {
    try {
        const response = await fetch(`/api/management/proxies/${id}`);
        const proxy = await response.json();

        document.getElementById('proxies-id').value = proxy.id;
        document.getElementById('proxies-proxy_type').value = proxy.proxy_type;
        document.getElementById('proxies-proxy_url').value = proxy.proxy_url;
        document.getElementById('proxies-proxy_port').value = proxy.proxy_port || '';
        document.getElementById('proxies-username').value = proxy.username || '';
        document.getElementById('proxies-password').value = proxy.password || '';

        document.getElementById('modal-proxies-title').textContent = '编辑代理';
        document.getElementById('modal-proxies').classList.add('active');
    } catch (error) {
        console.error('加载代理信息失败:', error);
        showError('加载代理信息失败');
    }
}

export async function deleteProxy(id) {
    if (!confirm('确定要删除此代理吗？')) {
        return;
    }

    try {
        const response = await fetch(`/api/management/proxies/${id}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            loadProxies();
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
