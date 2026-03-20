# Changelog

All notable changes to this project will be documented in this file.

## [1.1.0] — 2026-03-20

### 🏗️ Architecture Refactoring — Three-Phase Evolution

A major internal refactoring to improve code organization, maintainability, and deployment readiness. All 20 API endpoints verified passing after each phase.

#### Phase 0 — God File Elimination
- **Server refactoring** — `server.py` reduced from 2,363 lines to 227 lines (-90%)
- **Domain modules** — Split into 12 domain API modules under `web/api/`
- **Shared infrastructure** — `web/shared.py` for ConnectionManager, globals, RBAC helpers

#### Phase 1 — Service Layer + Repository Pattern
- **Repository layer** — 6 files (390 lines): Generic `BaseRepository[T]` with CRUD operations + domain-specific repositories (`employee_repo`, `user_repo`, `task_repo`, `knowledge_repo`, `audit_repo`)
- **Service layer** — 5 files (857 lines): Business logic encapsulation (`EmployeeService`, `UserService`, `TaskService`, `KnowledgeService`, `StatsService`)
- **API handler slimming** — Average 60% code reduction (e.g. `employees.py` 282→109, `tasks.py` 229→76, `knowledge.py` 290→95)
- **Separation of concerns** — Controllers only parse requests, call services, and return responses

#### Phase 2 — Containerization + Health Checks
- **Multi-stage Dockerfile** — Builder → runtime slim image with non-root user
- **docker-compose.yml** — Single-service orchestration with optional Nginx reverse proxy
- **`.env.example`** — Environment variable template (LLM keys, DB URL, JWT secret)
- **Nginx config** — TLS termination, WebSocket proxy, static asset caching, security headers
- **Health check endpoints** — `GET /health` (liveness) + `GET /health/db` (readiness with DB connectivity check)
- **Auth middleware update** — Health endpoints added to public paths (no authentication required)


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

### Wave 5 — Employee Memory
- **Per-Employee Memory Storage** — Isolated memory directory (`workspace/employees/{slug}/memory/`)
- **Memory API** — `GET/PUT/DELETE /api/employees/{id}/memory` for memory CRUD
- **Memory Modal UI** — 🧠 button on employee cards, in-browser editor with stats (size, last modified, history)
- **Memory Persistence** — MEMORY.md per employee, preserved across sessions

### Wave 6 — Knowledge Base MVP
- **KnowledgeBase Model** — SQLAlchemy model with name, description, type (file/vector/api), stats, org isolation
- **KnowledgeDocument Model** — Content storage with SHA-256 hash dedup, chunk tracking, file metadata
- **KB CRUD API** — Create, list, get, delete knowledge bases with document management
- **Document Upload** — Multipart file upload with auto text extraction and chunking
- **L1 Content Injection** — `/api/knowledge-bases/{id}/content` for system prompt injection
- **📚 Knowledge Panel** — Sidebar entry + management UI with card grid, stats display

### Wave 7 — Knowledge Integration
- **Agent Context Injection** — `build_system_prompt(knowledge_context=...)` injects bound KB content between Memory and Skills
- **Employee KB Binding** — Multi-select toggle in employee edit modal, persisted to `knowledge_bases` field
- **KB Document Search** — `GET /api/knowledge-bases/{id}/search?q=` keyword search with line-level snippets
- **Search Preview UI** — 🔍 Search section in KB detail modal with real-time results display

### Wave 8 — Quick Wins & Dashboard Enhancement
- **Chat Feedback** — 👍👎 buttons on assistant messages → `ChatFeedback` model + `POST /api/feedback`
- **Dashboard KB Stats** — 📚 Knowledge Bases count + 👍 Satisfaction % cards
- **Employee KB Badges** — Employee cards display 📚 KB binding count
- **Employee Templates** — 5 pre-built role templates (HR, IT, Customer Service, Analyst, Writer)
- **Conversation Export** — 📥 Download current conversation as Markdown file

### Wave 9 — Task Execution Engine
- **EmployeeTask Model** — Task with title, description, status, priority, schedule, result, token_cost
- **Task CRUD API** — Create, read, update, delete tasks with employee assignment
- **Task Execution** — `POST /api/tasks/{id}/execute` runs task through assigned employee's agent
- **Background Runner** — Async agent execution with status tracking (pending → running → completed/failed)
- **Tasks Panel** — Sidebar entry + full management UI with status badges, priority colors
- **Task Detail Modal** — View task result, ⚡ Execute, 🗑 Delete actions

### API Endpoints (32 total)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/auth/login` | POST | JWT login |
| `/api/auth/logout` | POST | Clear session |
| `/api/auth/me` | GET | Current user |
| `/api/auth/change-password` | POST | Password change |
| `/api/stats` | GET | Dashboard statistics |
| `/api/feedback` | POST | Chat feedback (👍👎) |
| `/api/settings` | GET | System settings |
| `/api/employees` | GET/POST | Employee list/create |
| `/api/employees/{id}` | GET/PUT/DELETE | Employee CRUD |
| `/api/employees/{id}/memory` | GET/PUT/DELETE | Employee memory CRUD |
| `/api/users` | GET/POST | User list/create |
| `/api/users/{id}` | PUT | User update (role/quota) |
| `/api/audit` | GET | Audit logs (sanitized) |
| `/api/quota` | GET | Quota usage check |
| `/api/chat-sessions` | GET/POST | Session persistence |
| `/api/tasks` | GET/POST | Task list/create |
| `/api/tasks/{id}` | GET/PUT/DELETE | Task CRUD |
| `/api/tasks/{id}/execute` | POST | Execute task via agent |
| `/api/keys` | GET/POST | API key list/create |
| `/api/keys/{id}/revoke` | POST | Revoke API key |
| `/api/knowledge-bases` | GET/POST | KB list/create |
| `/api/knowledge-bases/{id}` | GET/DELETE | KB detail/delete |
| `/api/knowledge-bases/{id}/documents` | POST | Upload document |
| `/api/knowledge-bases/{id}/documents/{doc_id}` | DELETE | Delete document |
| `/api/knowledge-bases/{id}/content` | GET | KB content for injection |
| `/api/knowledge-bases/{id}/search` | GET | Search KB documents |
| `/api/v1/chat` | POST | External chat API |
| `/api/v1/employees` | GET | External employee list |
| `/api/v1/webhook` | POST | Webhook receiver |

### Default Credentials
- **Username**: `admin`
- **Password**: `admin`
- **Role**: `superadmin`

> ⚠️ Change the default password on first login!
