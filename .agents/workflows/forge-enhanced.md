---
description: "增强版 /forge 工作流：融合 ANWS Wave 执行与 Superpowers 子代理驱动开发。适用于中大型项目（≥ 10 任务），在 Wave 内启用子代理分派 + Spec/Quality 双审。小型项目请使用原版 /forge。"
---

# /forge-enhanced

> **融合 ANWS 规划 + Superpowers 执行的增强版 Forge 工作流**

## 何时使用

| 条件 | 使用工作流 |
|------|----------|
| 任务 < 10 个 | 原版 `/forge`（单代理 Wave 执行） |
| 任务 ≥ 10 个，且大部分任务独立 | ✅ 本工作流（子代理驱动 Wave 执行） |
| 任务紧密耦合，难以独立执行 | 原版 `/forge` |

---

## Step 0-2: [沿用原版 /forge]

> Step 0 (**恢复与定位**)、Step 1 (**波次规划**)、Step 2 (**上下文加载**) **完全沿用原版 `/forge`**。
>
> 请先读取 `.agents/workflows/forge.md` 执行 Step 0-2，然后回到本工作流执行 Step 3。

---

## Step 3: 子代理驱动任务执行 (Subagent-Driven Execution)

**目标**: 使用 Superpowers 的子代理驱动模式执行 Wave 内的任务。

> [!IMPORTANT]
> 本步骤替代原版 `/forge` 的 Step 3（单代理执行循环）。
> **必须先读取 `.agents/skills/subagent-driven-development/SKILL.md`**。

### 3.0 技能检查（强制）

在开始任何任务之前，**必须激活以下 Skills**：

| Skill | 触发条件 | 位置 |
|-------|---------|------|
| `test-driven-development` | 每个实现任务 | `.agents/skills/test-driven-development/SKILL.md` |
| `verification-before-completion` | 宣布任务完成前 | `.agents/skills/verification-before-completion/SKILL.md` |
| `systematic-debugging` | 测试失败或异常时 | `.agents/skills/systematic-debugging/SKILL.md` |

### 3.1 任务分派

对 Wave 中的每个任务：

1. **评估任务复杂度** → 选择模型等级：

   | 复杂度信号 | 推荐模型等级 |
   |-----------|------------|
   | 单文件、规范清晰 | 💰 低成本模型 |
   | 多文件、需要集成 | 🧠 标准模型 |
   | 架构判断、跨系统 | 🌟 最强模型 |

2. **构建子代理 Prompt**，包含：
   - 任务 **完整文本**（从 `05_TASKS.md` 提取）
   - **验收标准** (AC)
   - **输入/输出** 文件路径
   - **TDD 铁律**：先写失败测试，再写实现代码
   - 提交格式：`feat(system-id): T{X.Y.Z} — 标题`

3. **分派实现子代理**（参考 `implementer-prompt.md`）

### 3.2 处理子代理状态

| 状态 | 处理方式 |
|------|---------|
| **DONE** | → 进入 3.3 Spec 审查 |
| **DONE_WITH_CONCERNS** | → 读取 concerns，评估后进入 3.3 |
| **NEEDS_CONTEXT** | → 补充上下文，重新分派 |
| **BLOCKED** | → 分析原因：上下文不足→补充 / 任务太大→拆分 / 设计问题→🛑 报告用户 |

### 3.3 双阶段审查

#### Stage 1: Spec 合规审查

分派 Spec 审查子代理（参考 `spec-reviewer-prompt.md`）：
- ✅ 所有验收标准是否满足？
- ❌ 是否有超出任务范围的实现？
- ❌ 是否遗漏了任务要求？

不通过 → 实现子代理修复 → 重新审查

#### Stage 2: 代码质量审查

分派 Quality 审查子代理（参考 `code-quality-reviewer-prompt.md`）：
- 代码风格、命名一致性
- 测试覆盖
- 安全隐患

不通过 → 实现子代理修复 → 重新审查

### 3.4 任务完成持久化

双审通过后：
- `05_TASKS.md` 中将 `- [ ]` 改为 `- [x]`
- Git commit：`feat(system-id): T{X.Y.Z} — 标题`

→ 下一个任务 → 回到 3.1

---

## Step 4-5: [沿用原版 /forge]

> Step 4 (**波次结算**) 和 Step 5 (**里程碑结算**) **完全沿用原版 `/forge`**。

---

<completion_criteria>
- ✅ 原版 /forge 的全部完成标准
- ✅ 每个任务经过 Spec + Quality 双审
- ✅ 所有任务遵循 TDD 铁律（先测试后代码）
- ✅ 调试过程使用 systematic-debugging（如适用）
- ✅ 每个任务完成前经过 verification-before-completion 检查
</completion_criteria>
