# AGENTS.md - AI 协作协议

> **"如果你正在阅读此文档，你就是那个智能体 (The Intelligence)。"**
> 
> 这个文件是你的**锚点 (Anchor)**。它定义了项目的法则、领地的地图，以及记忆协议。
> 当你唤醒（开始新会话）时，**请首先阅读此文件**。

---

## 🧠 30秒恢复协议 (Quick Recovery)

**当你开始新会话或感到"迷失"时，立即执行**:

1. **读取根目录的 AGENTS.md** → 获取项目地图
2. **查看下方"当前状态"** → 找到最新架构版本
3. **读取 `.anws/v{N}/05_TASKS.md`** → 了解当前待办
4. **开始工作**

---

## 🗺️ 地图 (领地感知)

以下是这个项目的组织方式：

| 路径 | 描述 | 访问协议 |
|------|------|----------|
| `src/` | **实现层**。实际的代码库。 | 通过 Task 读/写。 |
| `.anws/` | **统一架构根目录**。包含版本化架构状态与升级记录。 | **只读**(旧版) / **写一次**(新版) / `changelog` 由 CLI 维护。 |
| `.anws/v{N}/` | **当前真理**。最新的架构定义。 | 永远寻找最大的 `v{N}`。 |
| `.anws/changelog/` | **升级记录**。`anws update` 生成的变更记录。 | 由 CLI 自动维护，请勿删除。 |
| `target-specific workflow projection` | **工作流**。`/genesis`, `/blueprint` 等。 | 读取当前 target 对应的原生投影文件。 |
| `target-specific skill projection` | **技能库**。原子能力。 | 调用当前 target 对应的原生投影文件。 |
| `.nexus-map/` | **知识库**。代码库结构映射。 | 由 nexus-mapper 生成。 |

## 🛠️ 工作流注册表

> [!IMPORTANT]
> **工作流优先原则**：当任务匹配某个工作流，或你判断当前任务**明显符合、基本符合、甚至只是疑似符合**某个工作流的适用场景时，**都必须先读取相应文件**，并严格遵循其中的步骤执行。工作流是经过精心设计的协议，而非可选参考。
>
> **触发流程**：
> 1. 用户提及工作流名称，或你判断当前任务明显符合、基本符合、甚至只是疑似符合某个工作流的适用场景时，都必须先读取相应文件
> 2. **立即读取** 相应工作流文件
> 3. **严格遵循**工作流中的步骤执行
> 4. 在检查点暂停等待用户确认

| 工作流 | 触发时机 | 产出 |
|--------|---------|------|
| `/quickstart` | 新用户入口 / 不知道从哪开始 | 编排其他工作流 |
| `/genesis` | 新项目 / 重大重构 | PRD, Architecture, ADRs |
| `/probe` | 变更前 / 接手项目 | `.anws/v{N}/00_PROBE_REPORT.md` |
| `/design-system` | genesis 后 | 04_SYSTEM_DESIGN/*.md |
| `/blueprint` | genesis 后 | 05_TASKS.md + AGENTS.md 初始 Wave |
| `/change` | 微调已有任务 | 更新 TASKS + SYSTEM_DESIGN (仅修改) + CHANGELOG |
| `/explore` | 调研时 | 探索报告 |
| `/challenge` | 决策前质疑 | 07_CHALLENGE_REPORT.md (含问题总览目录) |
| `/forge` | 编码执行 | 代码 + 更新 AGENTS.md Wave 块 |
| `/forge-enhanced` | 中大型项目编码执行（≥ 10 任务） | 子代理驱动 + Spec/Quality 双审 |
| `/craft` | 创建工作流/技能/提示词 | Workflow / Skill / Prompt 文档 |
| `/upgrade` | `anws update` 后做升级编排 | 判断 Minor / Major，并路由到 `/change` 或 `/genesis` |

---

## 📜 宪法 (The Constitution)

1. **版本即法律**: 不"修补"架构文档，只"演进"。变更必须创建新版本。
2. **显式上下文**: 决策写入 ADR，不留在"聊天记忆"里。
3. **交叉验证**: 编码前对照 `05_TASKS.md`。我在做计划好的事吗？
4. **美学**: 文档应该是美的。善用 Markdown 和 Emoji。

---

## ⚡ Superpowers 融合层 (Execution Enhancement)

> **融合原则**：ANWS 管规划（做什么），Superpowers 管执行（怎么做）。

### 优先级堆栈

| 优先级 | 来源 | 说明 |
|:---:|------|------|
| 1 | **用户指令** | 用户的直接要求始终最高 |
| 2 | **ANWS 规范** | PRD/ADR/WBS 定义的架构约束 |
| 3 | **Superpowers Skills** | 执行层的方法论和流程护栏 |
| 4 | 默认系统提示 | 最低优先级 |

### 🔧 引入的 Superpowers Skills（`.agents/skills/`）

| Skill | 触发场景 | 类型 |
|-------|---------|------|
| `test-driven-development` | 编写任何功能代码或修复 Bug 时 | 🔴 **刚性** — 严格遵循 |
| `systematic-debugging` | 遇到 Bug、测试失败或异常行为时 | 🔴 **刚性** — 严格遵循 |
| `verification-before-completion` | 准备宣布任务完成之前 | 🔴 **刚性** — 严格遵循 |
| `subagent-driven-development` | `/forge` 执行阶段，任务可独立分派时 | 🟡 **弹性** — 视模型能力适配 |

### 🚨 执行铁律

> [!IMPORTANT]
> 1. **先测试后代码** — 没有失败测试，不写生产代码（TDD 铁律）
> 2. **先证据后声称** — 没有验证输出，不宣称任务完成（verification 铁律）
> 3. **先根因后修复** — 遇到 Bug 先系统分析，不盲目试错（debugging 铁律）
> 4. **文档即合约** — 代码不得偏离 ANWS 规范文档（ANWS 铁律，保留）

### 融合协议：`/forge` + Subagent-Driven Development

当执行 `/forge` 工作流时，Wave 内的任务**可选**使用子代理驱动模式：
- **小型项目**（< 10 任务）：沿用原始 `/forge` 的单代理 Wave 执行
- **中大型项目**（≥ 10 任务）：在每个 Wave 内启用子代理分派 + 双审

---
## 🔄 项目状态保留区

<!-- AUTO:BEGIN — 项目状态保留区（升级时唯一保留的部分，请勿手动修改区块边界） -->

## 📍 当前状态 (由 Workflow 自动更新)

> **注意**: 这是项目文件中的保留部分，由 `/genesis`、`/blueprint` 和 `/forge` 自动维护。

- **最新架构版本**: `.anws/v{N}`
- **活动任务清单**: `尚未生成` (等待 /blueprint)
- **待办任务数**: -
- **最近一次更新**: `[由 Workflow 自动填充]`

### 🌊 Wave 1 — 待 /blueprint 或 /forge 设置
_由 `/blueprint` 或 `/forge` 自动填充_

---

## 🌳 项目结构 (Project Tree)

> **注意**: 此部分由 `/genesis` 维护。

```text
nonobot/
├── AGENTS.md           (AI 锚文件 — ANWS + Superpowers 融合)
├── README.md           (nanobot 原始 README)
├── pyproject.toml      (Python 项目配置)
├── LICENSE             (MIT)
│
├── .agents/            (ANWS + Superpowers 技能和工作流)
│   ├── workflows/      (12 + 1 个工作流，含 forge-enhanced.md)
│   └── skills/         (12 ANWS + 4 Superpowers Skills)
│
├── .anws/              (版本化架构文档)
│   ├── changelog/
│   └── v{N}/
│
├── nanobot/            (🧠 核心源码 — 改造目标)
│   ├── agent/          (核心代理逻辑: loop, context, memory, skills, subagent, tools/)
│   ├── channels/       (聊天平台: Telegram, Discord, Slack, QQ, 飞书, Matrix, WeCom...)
│   ├── providers/      (LLM 提供商: OpenRouter, Anthropic, OpenAI, DeepSeek, Gemini...)
│   ├── skills/         (内置技能: github, weather, tmux...)
│   ├── bus/            (消息路由)
│   ├── cron/           (定时任务)
│   ├── heartbeat/      (心跳唤醒)
│   ├── session/        (会话管理)
│   ├── config/         (配置管理)
│   ├── security/       (安全沙箱)
│   ├── cli/            (CLI 命令)
│   ├── utils/          (工具函数)
│   └── templates/      (模板)
│
├── bridge/             (外部集成桥接)
└── .venv/              (Python 虚拟环境)
```

---

## 🧭 导航指南 (Navigation Guide)

> **注意**: 此部分由 `/genesis` 维护。

- **在新架构就绪前**: 请勿大规模修改代码。
- **架构总览**: `.anws/v{N}/02_ARCHITECTURE_OVERVIEW.md`
- **ADR**: `.anws/v{N}/03_ADR/` (跨系统决策的唯一记录源)
- **遇到架构问题**: 请查阅 `.anws/v{N}/03_ADR/`。

---

### 技术栈决策
- [由 .anws/tech-evaluator 或 /genesis 自动填充]

### 系统边界
- [由 .anws/system-architect 或 /genesis 自动填充]

### 活跃 ADR
- [由 .anws 自动填充 ADR 摘要]

### 当前任务状态
- [由 blueprint/forge 自动更新]

<!-- AUTO:END -->

---
> **状态自检**: 准备好了？提醒用户运行 `/quickstart` 开始吧。
