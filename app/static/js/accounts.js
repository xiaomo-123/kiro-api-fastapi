// 账号管理模块
// 分页状态
let currentPage = 1;
let pageSize = 50;
let totalPages = 1;

export async function loadAccounts(page = 1) {
    try {
        const skip = (page - 1) * pageSize;
        const response = await fetch(`/api/management/accounts?skip=${skip}&limit=${pageSize}`);
        const data = await response.json();
        const accounts = Array.isArray(data) ? data : [];
        
        // 计算总页数（这里假设总记录数，实际可能需要额外的API获取总数）
        // 如果返回的数据少于pageSize，说明是最后一页
        totalPages = accounts.length < pageSize ? page : Math.ceil((skip + accounts.length + pageSize) / pageSize);
        currentPage = page;

        const tbody = document.querySelector('#accounts-table tbody');
        tbody.innerHTML = '';

        accounts.forEach(account => {
            const tr = document.createElement('tr');
            const displayAccount = account.account.length > 50 
                ? account.account.substring(0, 50) + '...' 
                : account.account;
            tr.innerHTML = `
                <td><input type="checkbox" class="account-checkbox" data-id="${account.id}" /></td>
                <td>${account.id}</td>
                <td class="account-cell" title="${account.account}">${displayAccount}</td>
                <td>${account.status === '1' ? '启用' : '禁用'}</td>
                <td>${account.description || ''}</td>
                <td>
                    <button class="btn-edit" onclick="editAccount(${account.id})">编辑</button>
                    <button class="btn-delete" onclick="deleteAccount(${account.id})">删除</button>
                </td>
            `;
            tbody.appendChild(tr);
        });

        // 重置全选复选框
        const selectAllCheckbox = document.getElementById('select-all-accounts');
        if (selectAllCheckbox) {
            selectAllCheckbox.checked = false;
        }
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
                loadAccounts(currentPage);
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
            loadAccounts(currentPage);
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

// 全选/取消全选账号
window.toggleAllAccounts = function(checkbox) {
    const checkboxes = document.querySelectorAll('.account-checkbox');
    checkboxes.forEach(cb => {
        cb.checked = checkbox.checked;
    });
};

// 批量删除账号
window.deleteSelectedAccounts = async function() {
    const checkboxes = document.querySelectorAll('.account-checkbox:checked');

    if (checkboxes.length === 0) {
        showError('请至少选择一个账号');
        return;
    }

    if (!confirm(`确定要删除选中的 ${checkboxes.length} 个账号吗？`)) {
        return;
    }

    const ids = Array.from(checkboxes).map(cb => parseInt(cb.getAttribute('data-id')));

    try {
        const response = await fetch('/api/management/accounts/batch-delete', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ ids })
        });

        if (response.ok) {
            const result = await response.json();
            showSuccess(result.message);
            loadAccounts(currentPage);
        } else {
            const data = await response.json();
            showError(data.detail || '批量删除失败');
        }
    } catch (error) {
        console.error('批量删除失败:', error);
        showError('批量删除失败');
    }
};

// 分页控制函数
window.goToPage = function(page) {
    if (page < 1 || page > totalPages) {
        return;
    }
    loadAccounts(page);
};

window.goToPreviousPage = function() {
    if (currentPage > 1) {
        loadAccounts(currentPage - 1);
    }
};

window.goToNextPage = function() {
    if (currentPage < totalPages) {
        loadAccounts(currentPage + 1);
    }
};

// 更新分页显示
function updatePaginationDisplay() {
    const paginationContainer = document.getElementById('accounts-pagination');
    if (!paginationContainer) return;
    
    paginationContainer.innerHTML = `
        <div class="pagination-info">
            第 ${currentPage} / ${totalPages} 页
        </div>
        <div class="pagination-controls">
            <button class="btn-pagination" onclick="goToPreviousPage()" ${currentPage === 1 ? 'disabled' : ''}>前一页</button>
            <button class="btn-pagination" onclick="goToNextPage()" ${currentPage === totalPages ? 'disabled' : ''}>后一页</button>
        </div>
    `;
}

// 修改loadAccounts函数，在加载数据后更新分页显示
const originalLoadAccounts = loadAccounts;
loadAccounts = async function(page = 1) {
    await originalLoadAccounts(page);
    updatePaginationDisplay();
};
