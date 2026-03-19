<div align="center">
  <img src="nanobot_logo.png" alt="nanobot" width="500">
  <h1>nonobot: 企业级数字员工平台</h1>
  <p>
    <a href="https://pypi.org/project/nanobot-ai/"><img src="https://img.shields.io/pypi/v/nanobot-ai" alt="PyPI"></a>
    <img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  </p>
  <p>
    <a href="./README.md">English</a> | <b>中文</b>
  </p>
</div>

🐈 **nonobot** 基于 [nanobot](https://github.com/HKUDS/nanobot)，将超轻量个人 AI 助手扩展为**企业级数字员工管理平台**。

## ✨ 核心功能

| 功能 | 说明 |
|------|------|
| 🔐 **JWT 认证** | 登录/登出、会话管理、密码修改 |
| 🤖 **数字员工** | 创建 AI 员工，自定义人设、模型、工具集 |
| 👥 **用户管理** | 五级角色体系（超级管理员 → 访客），RBAC 权限控制 |
| 📊 **仪表盘** | 实时统计 — 用户数、员工数、消息数、Token 用量 |
| 📋 **审计日志** | IP 脱敏、敏感字段过滤 |
| 📈 **Token 配额** | 按用户设置日/月用量限制，进度条可视化 |
| 🔑 **API 密钥** | 创建/吊销密钥，基于作用域的授权 |
| 🌐 **外部 API** | `POST /api/v1/chat` · `POST /api/v1/webhook` · `GET /api/v1/employees` |
| 💬 **Web 聊天** | WebSocket 实时对话，支持员工切换 |
| 📁 **文件管理** | 沙箱文件系统，上传/下载/创建目录 |
| 🧠 **员工记忆** | 每个员工独立持久化记忆，带 GUI 编辑器 |
| 📚 **知识库** | 文档上传、自动分块存储、关键词搜索 |
| 🔗 **知识集成** | 将知识库绑定到员工，自动注入对话上下文 |

## 🚀 快速开始

```bash
# 安装
git clone https://github.com/vincentwuxi/nonobot.git
cd nonobot
pip install -e .

# 配置 LLM 提供商（在 ~/.nanobot/config.json 中添加 API Key）
nanobot onboard

# 启动 Web 控制台
nanobot gateway
# 访问 http://localhost:18790
# 默认账号: admin / admin
```

## 🏗️ 架构

平台基于 **7 波迭代** 逐步构建：

| 波次 | 主题 | 关键交付物 |
|------|------|-----------|
| **Wave 1** | 企业基座 | JWT 认证、数据库 ORM、Web 控制台 |
| **Wave 2** | 智能调度 | 员工感知 Agent Loop、Token 追踪、员工选择器 |
| **Wave 3** | 治理合规 | RBAC 五级角色、Token 配额、审计脱敏 |
| **Wave 4** | 生态集成 | API 密钥、外部 API 网关、Webhook 接收器 |
| **Wave 5** | 记忆增强 | 员工独立记忆存储、Memory API、记忆编辑器 |
| **Wave 6** | 知识库 MVP | KB/Document 数据模型、CRUD API、管理面板 |
| **Wave 7** | 知识集成 | Agent 上下文注入、KB 绑定、文档搜索预览 |

## 📖 API 接口 (27 个)

完整的 API 端点列表请参阅 [CHANGELOG.md](./CHANGELOG.md)。

### 核心接口

```
POST /api/auth/login              # JWT 登录
GET  /api/stats                   # 仪表盘统计
GET  /api/employees               # 员工列表
POST /api/employees               # 创建员工
GET  /api/employees/{id}/memory   # 获取员工记忆
PUT  /api/employees/{id}/memory   # 更新员工记忆
GET  /api/knowledge-bases         # 知识库列表
POST /api/knowledge-bases         # 创建知识库
POST /api/knowledge-bases/{id}/documents  # 上传文档
GET  /api/knowledge-bases/{id}/search     # 搜索文档
POST /api/v1/chat                 # 外部聊天 API
```

## 🧠 记忆与知识管理

### 员工记忆

每个数字员工拥有独立的持久化记忆，存储在 `workspace/employees/{slug}/memory/MEMORY.md`。

- **查看/编辑**：在员工卡片点击 🧠 Memory 按钮
- **统计信息**：记忆大小、最后修改时间、历史条目数
- **持久化**：记忆跨会话保留，支持手动编辑

### 知识库

知识库允许你上传参考文档，AI 员工可在对话中引用这些知识。

- **创建知识库**：📚 Knowledge → + New KB
- **上传文档**：支持 `.txt`、`.md`、`.csv`、`.json` 格式
- **自动分块**：文档自动分块存储，支持 SHA-256 去重
- **搜索预览**：在 KB 详情中使用 🔍 搜索功能预览内容
- **绑定员工**：在员工编辑页面选择要绑定的知识库
- **上下文注入**：绑定的 KB 内容会自动注入到 Agent 的 system prompt 中

## 🔧 配置

配置文件位于 `~/.nanobot/config.json`。

### LLM 提供商

支持 20+ 提供商，推荐使用 [OpenRouter](https://openrouter.ai)（全球用户）：

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    }
  },
  "agents": {
    "defaults": {
      "model": "anthropic/claude-sonnet-4",
      "provider": "openrouter"
    }
  }
}
```

### 聊天渠道

支持 Telegram、Discord、WhatsApp、飞书、钉钉、Slack、QQ、企业微信、Email、Matrix 等 10+ 平台。

详见 [README.md](./README.md#-chat-apps) 中的聊天应用配置指南。

## 🐳 Docker 部署

```bash
docker build -t nonobot .
docker run -d -p 18790:18790 -v ~/.nanobot:/root/.nanobot nonobot
```

## 📜 许可证

[MIT License](./LICENSE)

## 🙏 致谢

基于 [nanobot](https://github.com/HKUDS/nanobot) — 超轻量个人 AI 助手。原始 README 备份于 [README.nanobot.md](./README.nanobot.md)。
