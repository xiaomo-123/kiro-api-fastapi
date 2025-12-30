// 账号管理模块
export async function loadAccounts() {
    try {
        const response = await fetch('/api/management/accounts');
        const accounts = await response.json();

        const tbody = document.querySelector('#accounts-table tbody');
        tbody.innerHTML = '';

        accounts.forEach(account => {
            const tr = document.createElement('tr');
            const displayAccount = account.account.length > 50 
                ? account.account.substring(0, 50) + '...' 
                : account.account;
            tr.innerHTML = `
                <td>${account.id}</td>
                <td class="account-cell" title="${account.account}">${displayAccount}</td>
                <td>${account.status === 'active' ? '启用' : '禁用'}</td>
                <td>${account.description || ''}</td>
                <td>
                    <button class="btn-edit" onclick="editAccount(${account.id})">编辑</button>
                    <button class="btn-delete" onclick="deleteAccount(${account.id})">删除</button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (error) {
        console.error('加载账号列表失败:', error);
        showError('加载账号列表失败');
    }
}

export function initAccountForm() {
    const form = document.getElementById('form-accounts');
    form.addEventListener('submit', async function(e) {
        e.preventDefault();

        const id = document.getElementById('accounts-id').value;
        const account = document.getElementById('accounts-account').value;
        const status = document.getElementById('accounts-status').value;
        const description = document.getElementById('accounts-description').value;

        // 验证账号字段
        if (!account || account.trim() === '') {
            showError('账号不能为空');
            return;
        }

        try {
            let response;
            if (id) {
                // 更新账号
                response = await fetch(`/api/management/accounts/${id}`, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ account: account.trim(), status, description })
                });
            } else {
                // 创建账号
                response = await fetch('/api/management/accounts', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ account: account.trim(), status, description })
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

export async function editAccount(id) {
    try {
        const response = await fetch(`/api/management/accounts/${id}`);
        const account = await response.json();

        document.getElementById('accounts-id').value = account.id;
        document.getElementById('accounts-account').value = account.account;
        document.getElementById('accounts-status').value = account.status;
        document.getElementById('accounts-description').value = account.description || '';

        document.getElementById('modal-accounts-title').textContent = '编辑账号';
        document.getElementById('modal-accounts').classList.add('active');
    } catch (error) {
        console.error('加载账号信息失败:', error);
        showError('加载账号信息失败');
    }
}

window.editAccount = editAccount;

export async function deleteAccount(id) {
    if (!confirm('确定要删除此账号吗？')) {
        return;
    }

    try {
        const response = await fetch(`/api/management/accounts/${id}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            loadAccounts();
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

window.deleteAccount = deleteAccount;
