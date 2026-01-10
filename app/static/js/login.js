// 登录页面脚本
document.addEventListener('DOMContentLoaded', function() {
    const loginForm = document.getElementById('loginForm');
    const errorMessage = document.getElementById('errorMessage');

    // 检查是否已登录
    if (localStorage.getItem('isLoggedIn') === 'true') {
        window.location.href = 'index.html';
        return;
    }

    loginForm.addEventListener('submit', async function(e) {
        e.preventDefault();

        const username = document.getElementById('username').value;
        const password = document.getElementById('password').value;

        try {
            const response = await fetch('/api/management/users/login', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    username: username,
                    password: password
                })
            });

            const data = await response.json();

            if (response.ok) {
                // 登录成功，保存登录状态
                localStorage.setItem('isLoggedIn', 'true');
                localStorage.setItem('userId', data.user_id);
                localStorage.setItem('username', username);

                // 跳转到主页面
                window.location.href = 'index.html';
            } else {
                // 显示错误信息
                errorMessage.textContent = data.detail || '登录失败';
                errorMessage.style.display = 'block';
            }
        } catch (error) {
            errorMessage.textContent = '网络错误，请稍后重试';
            errorMessage.style.display = 'block';
        }
    });
});
