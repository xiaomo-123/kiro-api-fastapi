# 管理系统使用说明

## 功能概述

本系统提供完整的管理功能，包括：

1. **用户管理** - 管理系统登录用户
2. **账号管理** - 管理各种账号及其状态
3. **API Key管理** - 管理API密钥
4. **代理管理** - 管理代理服务器配置

## 数据库表结构

### 用户表 (users)
- id: 自增主键
- username: 用户名（唯一）
- password: 加密密码
- description: 用户描述

### 账号表 (accounts)
- id: 自增主键
- account: 账号（唯一）
- status: 账号状态（active/inactive）
- description: 账号描述

### API Key表 (api_keys)
- id: 自增主键
- api_key: API密钥（唯一）
- description: 密钥描述

### 代理表 (proxies)
- id: 自增主键
- proxy_type: 代理类型（http/https/socks5）
- proxy_url: 代理服务器URL
- proxy_port: 代理端口
- username: 代理用户名（可选）
- password: 代理密码（可选）

## 安装依赖

确保已安装以下Python包：

```bash
pip install fastapi uvicorn sqlalchemy passlib bcrypt
```

## 初始化系统

1. **初始化数据库**

数据库会在首次启动时自动创建，包含所有必要的表。

2. **创建默认管理员用户**

运行以下命令创建默认管理员用户：

```bash
python -m app.db.init_data
```

默认管理员账号：
- 用户名: admin
- 密码: admin123

**重要：首次登录后请立即修改默认密码！**

## 启动系统

```bash
python main.py
```

或使用uvicorn：

```bash
uvicorn main:app --reload
```

## 访问管理系统

启动后，通过浏览器访问：

```
http://localhost:5431/static/login.html
```

## API接口

所有管理API都以 `/api/management` 为前缀：

### 用户管理
- POST `/api/management/users/login` - 用户登录
- POST `/api/management/users` - 创建用户
- GET `/api/management/users` - 获取用户列表
- GET `/api/management/users/{user_id}` - 获取单个用户
- PUT `/api/management/users/{user_id}` - 更新用户
- DELETE `/api/management/users/{user_id}` - 删除用户

### 账号管理
- POST `/api/management/accounts` - 创建账号
- GET `/api/management/accounts` - 获取账号列表
- GET `/api/management/accounts/{account_id}` - 获取单个账号
- PUT `/api/management/accounts/{account_id}` - 更新账号
- DELETE `/api/management/accounts/{account_id}` - 删除账号

### API Key管理
- POST `/api/management/apikeys` - 创建API Key
- GET `/api/management/apikeys` - 获取API Key列表
- GET `/api/management/apikeys/{apikey_id}` - 获取单个API Key
- PUT `/api/management/apikeys/{apikey_id}` - 更新API Key
- DELETE `/api/management/apikeys/{apikey_id}` - 删除API Key

### 代理管理
- POST `/api/management/proxies` - 创建代理
- GET `/api/management/proxies` - 获取代理列表
- GET `/api/management/proxies/{proxy_id}` - 获取单个代理
- PUT `/api/management/proxies/{proxy_id}` - 更新代理
- DELETE `/api/management/proxies/{proxy_id}` - 删除代理

## 文件结构

```
app/
├── db/
│   ├── models.py          # 数据库模型定义
│   ├── database.py       # 数据库配置和初始化
│   ├── schemas.py        # Pydantic模型定义
│   └── init_data.py     # 初始化默认数据
├── api/
│   └── management.py     # 管理API路由
└── static/
    ├── login.html        # 登录页面
    ├── index.html        # 主管理页面
    └── js/
        ├── login.js      # 登录脚本
        ├── index.js      # 主页面脚本
        ├── users.js      # 用户管理模块
        ├── accounts.js   # 账号管理模块
        ├── apikeys.js   # API Key管理模块
        └── proxies.js   # 代理管理模块
```

## 安全建议

1. 修改默认管理员密码
2. 使用强密码策略
3. 定期备份数据库文件
4. 在生产环境中使用HTTPS
5. 限制API访问权限
6. 定期更新依赖包

## 技术栈

- 后端：FastAPI + SQLAlchemy + SQLite
- 前端：原生JavaScript + HTML5 + CSS3
- 认证：bcrypt密码加密
- 数据库：SQLite3
