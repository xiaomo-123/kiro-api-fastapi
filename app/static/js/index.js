// 主页面脚本
import { loadAccounts } from './accounts.js';
import { loadApiKeys, initApiKeyForm } from './apikeys.js';

document.addEventListener('DOMContentLoaded', function() {
    // 检查登录状态
    if (localStorage.getItem('isLoggedIn') !== 'true') {
        window.location.href = 'login.html';
        return;
    }

    // 显示用户名
    const username = localStorage.getItem('username');
    document.getElementById('username').textContent = username;

    // 加载初始数据
    loadUsers();
    loadAccounts();
    loadApiKeys();
    loadProxies();

    // 初始化表单提交事件
    initUserForm();
    initAccountForm();
    initApiKeyForm();
    initProxyForm();
});

// 页面切换
window.showPage = function(pageName) {
    // 隐藏所有页面
    document.querySelectorAll('.page').forEach(page => {
        page.classList.remove('active');
    });

    // 移除所有侧边栏项的active类
    document.querySelectorAll('.sidebar li').forEach(item => {
        item.classList.remove('active');
    });

    // 显示选中的页面
    document.getElementById('page-' + pageName).classList.add('active');

    // 为选中的侧边栏项添加active类
    if (event && event.target) {
        event.target.classList.add('active');
    }

    // 根据页面名称重新加载对应数据
    switch(pageName) {
        case 'users':
            loadUsers();
            break;
        case 'accounts':
            loadAccounts();
            break;
        case 'apikeys':
            loadApiKeys();
            break;
        case 'proxies':
            loadProxies();
            break;
    }
};

// 退出登录
window.logout = function() {
    localStorage.removeItem('isLoggedIn');
    localStorage.removeItem('userId');
    localStorage.removeItem('username');
    window.location.href = 'login.html';
};

// 模态框操作
window.openModal = function(type) {
    const modal = document.getElementById('modal-' + type);
    modal.classList.add('active');

    // 重置表单
    const form = document.getElementById('form-' + type);
    form.reset();
    document.getElementById(type + '-id').value = '';

    // 特殊处理API Key输入框，确保它是可编辑的
    if (type === 'apikeys') {
        document.getElementById('apikeys-api_key').disabled = false;
    }

    // 更新标题
    const title = document.getElementById('modal-' + type + '-title');
    title.textContent = '添加' + getTypeName(type);
};

window.closeModal = function(type) {
    const modal = document.getElementById('modal-' + type);
    modal.classList.remove('active');
};

function getTypeName(type) {
    const typeNames = {
        'users': '用户',
        'accounts': '账号',
        'apikeys': 'API Key',
        'proxies': '代理'
    };
    return typeNames[type] || type;
}

// 用户管理相关函数
async function loadUsers() {
    try {
        const response = await fetch('/api/management/users');
        const users = await response.json();

        const tbody = document.querySelector('#users-table tbody');
        tbody.innerHTML = '';

        users.forEach(user => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${user.id}</td>
                <td>${user.username}</td>
                <td>${user.description || ''}</td>
                <td>
                    <button class="btn-edit" onclick="editUser(${user.id})">编辑</button>
                    <button class="btn-delete" onclick="deleteUser(${user.id})">删除</button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (error) {
        console.error('加载用户列表失败:', error);
        showError('加载用户列表失败');
    }
}

function initUserForm() {
    const form = document.getElementById('form-users');
    form.addEventListener('submit', async function(e) {
        e.preventDefault();

        const id = document.getElementById('users-id').value;
        const username = document.getElementById('users-username').value;
        const password = document.getElementById('users-password').value;
        const description = document.getElementById('users-description').value;

        try {
            let response;
            if (id) {
                // 更新用户
                response = await fetch(`/api/management/users/${id}`, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ description })
                });
            } else {
                // 创建用户
                if (!password) {
                    showError('请输入密码');
                    return;
                }
                response = await fetch('/api/management/users', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ username, password, description })
                });
            }

            if (response.ok) {
                closeModal('users');
                loadUsers();
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

async function editUser(id) {
    try {
        const response = await fetch(`/api/management/users/${id}`);
        const user = await response.json();

        document.getElementById('users-id').value = user.id;
        document.getElementById('users-username').value = user.username;
        document.getElementById('users-username').disabled = true;
        document.getElementById('users-password').value = '';
        document.getElementById('users-description').value = user.description || '';

        document.getElementById('modal-users-title').textContent = '编辑用户';
        document.getElementById('modal-users').classList.add('active');
    } catch (error) {
        console.error('加载用户信息失败:', error);
        showError('加载用户信息失败');
    }
}

async function deleteUser(id) {
    if (!confirm('确定要删除此用户吗？')) {
        return;
    }

    try {
        const response = await fetch(`/api/management/users/${id}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            loadUsers();
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



function initAccountForm() {
    const form = document.getElementById('form-accounts');
    form.addEventListener('submit', async function(e) {
        e.preventDefault();

        const id = document.getElementById('accounts-id').value;
        const account = document.getElementById('accounts-account').value;
        const status = document.getElementById('accounts-status').value;
        const description = document.getElementById('accounts-description').value;

        try {
            let response;
            if (id) {
                // 更新账号
                response = await fetch(`/api/management/accounts/${id}`, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ account, status, description })
                });
            } else {
                // 创建账号
                response = await fetch('/api/management/accounts', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ account, status, description })
                });
            }

            if (response.ok) {
                closeModal('accounts');
                loadAccounts();
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



// 代理管理相关函数
async function loadProxies() {
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
                <td>${proxy.status === '1' ? '启用' : '禁用'}</td>
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

function initProxyForm() {
    const form = document.getElementById('form-proxies');
    form.addEventListener('submit', async function(e) {
        e.preventDefault();

        const id = document.getElementById('proxies-id').value;
        const proxy_type = document.getElementById('proxies-proxy_type').value;
        const proxy_url = document.getElementById('proxies-proxy_url').value;
        const proxy_port = document.getElementById('proxies-proxy_port').value;
        const username = document.getElementById('proxies-username').value;
        const password = document.getElementById('proxies-password').value;
        const status = document.getElementById('proxies-status').value;

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
                        password,
                        status 
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
                        password,
                        status 
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

async function editProxy(id) {
    try {
        const response = await fetch(`/api/management/proxies/${id}`);
        const proxy = await response.json();

        document.getElementById('proxies-id').value = proxy.id;
        document.getElementById('proxies-proxy_type').value = proxy.proxy_type;
        document.getElementById('proxies-proxy_url').value = proxy.proxy_url;
        document.getElementById('proxies-proxy_port').value = proxy.proxy_port || '';
        document.getElementById('proxies-username').value = proxy.username || '';
        document.getElementById('proxies-password').value = proxy.password || '';
        document.getElementById('proxies-status').value = proxy.status || '1';

        document.getElementById('modal-proxies-title').textContent = '编辑代理';
        document.getElementById('modal-proxies').classList.add('active');
    } catch (error) {
        console.error('加载代理信息失败:', error);
        showError('加载代理信息失败');
    }
}

async function deleteProxy(id) {
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

// 导入账号
window.importAccounts = async function(fileInput) {
    const file = fileInput.files[0];
    if (!file) {
        return;
    }

    try {
        // 读取文件内容
        const content = await file.text();
        const accounts = JSON.parse(content);

        // 验证是否为数组
        if (!Array.isArray(accounts)) {
            showError('JSON文件格式错误：必须是数组格式');
            return;
        }

        // 发送到后端
        const response = await fetch('/api/management/accounts/import', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(accounts)
        });

        if (response.ok) {
            const result = await response.json();
            showSuccess(result.message);
            // 导入成功后跳转到第一页
            if (typeof loadAccounts === 'function') {
                loadAccounts(1);
            }

            // 如果有错误，显示错误信息
            if (result.errors && result.errors.length > 0) {
                setTimeout(() => {
                    result.errors.forEach(error => {
                        showError(error);
                    });
                }, 1000);
            }
        } else {
            const data = await response.json();
            showError(data.detail || '导入失败');
        }
    } catch (error) {
        console.error('导入失败:', error);
        showError('导入失败：' + error.message);
    } finally {
        // 清空文件输入
        fileInput.value = '';
    }
}
