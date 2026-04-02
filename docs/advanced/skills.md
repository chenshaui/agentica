# Skills System

Skills 是 Agentica 的"提示即能力"系统 -- 通过 Markdown 文件定义的指令，在运行时注入到 Agent 的 System Prompt 中。

## 核心概念

- Skill 是一个 `SKILL.md` 文件
- 包含 YAML frontmatter（元数据）和 Markdown body（指令内容）
- 运行时由 `SkillTool` 注入到 System Prompt
- 模型无关 -- 纯文本指令，适用于所有 LLM

## SKILL.md 格式

```markdown
---
name: code_reviewer
description: 代码审查专家
version: 1.0
tags: [code, review]
---

# Code Reviewer

你是一个代码审查专家。审查代码时遵循以下原则：

## 审查重点

1. **安全性** -- 检查潜在的安全漏洞
2. **性能** -- 识别性能瓶颈
3. **可读性** -- 代码是否易于理解
4. **最佳实践** -- 是否遵循语言惯用法

## 输出格式

对每个问题：
- 严重程度: Critical / Warning / Info
- 位置: 文件名 + 行号
- 问题描述
- 修复建议
```

### Frontmatter 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | `str` | Skill 名称（唯一标识） |
| `description` | `str` | 简短描述 |
| `version` | `str` | 版本号 |
| `tags` | `List[str]` | 标签列表 |

## 使用 Skill

### 通过 SkillTool

```python
from agentica import Agent, OpenAIChat
from agentica.tools.skill_tool import SkillTool

agent = Agent(
    model=OpenAIChat(id="gpt-4o"),
    tools=[SkillTool(skill_dir="./skills")],
)
```

### 通过 RunConfig 白名单

```python
from agentica.run_config import RunConfig

result = await agent.run("审查代码", config=RunConfig(
    enabled_skills=["code_reviewer"],
))
```

## Skill 目录

默认 Skill 目录: `AGENTICA_SKILL_DIR`（可通过 `config.py` 配置）。

```
skills/
+-- code_reviewer/
|   +-- SKILL.md
+-- data_analyst/
|   +-- SKILL.md
+-- writing_assistant/
    +-- SKILL.md
```

## 下一步

- [Agent 概念](../concepts/agent.md) -- Agent 如何加载 Skill
- [Tools](../concepts/tools.md) -- SkillTool 详解
- [RunConfig](run-config.md) -- enabled_skills 白名单
