// 用户管理模块
export async function loadUsers() {
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

export function initUserForm() {
    const form = document.getElementById('form-users');
    form.addEventListener('submit', async function(e) {
        e.preventDefault();

        const id = document.getElementById('users-id').value;
        const username = document.getElementById('users-username').value;
        const password = document.getElementById('users-password').value;
        const description = document.getElementById('users-description').value;

        // 验证用户名
        if (!username || username.trim() === '') {
            showError('用户名不能为空');
            return;
        }

        // 创建用户时验证密码
        if (!id) {
            if (!password || password.trim() === '') {
                showError('请输入密码');
                return;
            }
            if (password.length < 6) {
                showError('密码长度不能少于6位');
                return;
            }
        }

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
                response = await fetch('/api/management/users', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ username: username.trim(), password, description })
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

export async function editUser(id) {
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

window.editUser = editUser;

export async function deleteUser(id) {
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

window.deleteUser = deleteUser;
