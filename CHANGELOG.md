# Changelog

All notable changes to this project will be documented in this file.

## [1.0.0] — 2026-03-19

### 🎉 Enterprise Digital Employee Platform — First Release

Based on nanobot v0.1.4.post5, this release transforms the personal AI assistant into an enterprise-grade digital employee platform.

### Wave 1 — Enterprise Foundation
- **JWT Authentication** — Login/logout, password change, cookie-based sessions
- **SQLAlchemy Database** — PostgreSQL/SQLite ORM models (User, Employee, Organization, AuditLog, ChatSession, ApiKey)
- **Digital Employee Model** — Create, edit, delete employees with avatar, description, tools, system prompt, model override
- **Web Console** — Dark-themed management UI with Chat, Dashboard, Sessions, Files, Employees, Users, Audit, Config, Profile panels
- **Admin Panel** — User management with role assignment, employee CRUD, audit log viewer

### Wave 2 — Intelligent Dispatch
- **Employee-Aware Agent Loop** — System prompt injection per employee persona, model override support
- **Per-Employee Token Tracking** — Background task updates employee usage statistics (total_messages, total_tokens)
- **Employee Selector** — Chat UI allows switching between digital employees in real-time

### Wave 3 — Governance & Compliance
- **RBAC** — 5-level role hierarchy (superadmin > org_admin > team_lead > member > guest) with `require_role()` decorator
- **Sidebar RBAC** — Admin section auto-hidden for member/guest users
- **Token Quotas** — Daily/monthly token limits per user, quota progress bars in Profile
- **User Management** — Edit user role, status, and quota limits via modal
- **Data Sanitization** — IP address masking in audit logs, sensitive field filtering (password_hash, secret)
- **Session Persistence** — Chat session CRUD backed by database

### Wave 4 — Ecosystem Integration
- **API Key Management** — Create/list/revoke API keys with SHA-256 hashing, scope-based authorization
- **External API Gateway** — `POST /api/v1/chat`, `GET /api/v1/employees`, `POST /api/v1/webhook`
- **Webhook Receiver** — Accept external events, route to specified employees
- **API Key Authentication** — Dual-path auth via `X-API-Key` header or `Authorization: Bearer nb-xxx`

### API Endpoints (18 total)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/auth/login` | POST | JWT login |
| `/api/auth/logout` | POST | Clear session |
| `/api/auth/me` | GET | Current user |
| `/api/auth/change-password` | POST | Password change |
| `/api/stats` | GET | Dashboard statistics |
| `/api/settings` | GET | System settings |
| `/api/employees` | GET/POST | Employee list/create |
| `/api/employees/{id}` | GET/PUT/DELETE | Employee CRUD |
| `/api/users` | GET/POST | User list/create |
| `/api/users/{id}` | PUT | User update (role/quota) |
| `/api/audit` | GET | Audit logs (sanitized) |
| `/api/quota` | GET | Quota usage check |
| `/api/chat-sessions` | GET/POST | Session persistence |
| `/api/keys` | GET/POST | API key list/create |
| `/api/keys/{id}/revoke` | POST | Revoke API key |
| `/api/v1/chat` | POST | External chat API |
| `/api/v1/employees` | GET | External employee list |
| `/api/v1/webhook` | POST | Webhook receiver |

### Default Credentials
- **Username**: `admin`
- **Password**: `admin`
- **Role**: `superadmin`

> ⚠️ Change the default password on first login!
