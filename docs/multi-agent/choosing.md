# Choosing an Orchestration Pattern

Agentica has several multi-agent patterns. Use the smallest one that gives you the control boundary you need.

## Decision Tree

```text
Do you need more than one agent?
    |
    +-- No
    |     Use Agent.run()
    |
    +-- Yes
          |
          +-- Is the step order fixed and auditable?
          |     Use Workflow
          |
          +-- Should a parent agent choose a helper as a tool?
          |     Use Agent.as_tool()
          |
          +-- Does the helper need isolation, tool permissions, or timeout?
          |     Use Subagent
          |
          +-- Do multiple peers need to work in parallel or autonomously split work?
                Use Swarm as an advanced recipe
```

## Default Recommendation

Start with `Agent.as_tool()` for lightweight composition and `Workflow` for deterministic pipelines. Use `Subagent` when the child run needs a permission boundary, separate runtime state, or task timeout. Use `Swarm` only when parallel peer collaboration is the product requirement, not just because a task has multiple steps.

## Comparison

| Pattern | Best For | Control | Cost Predictability | Product Risk |
|---------|----------|---------|---------------------|--------------|
| `Agent.as_tool()` | Parent agent calls focused helpers | LLM chooses when to call | Medium | Low |
| `Workflow` | Fixed pipelines and mixed Python/LLM steps | Developer controls order | High | Low |
| `Subagent` | Isolated delegated tasks | Runtime enforces tool/depth/time limits | Medium | Medium |
| `Swarm` | Autonomous or parallel peer work | Coordinator prompt controls split | Lower | Higher |

## Rules of Thumb

- If a plain Python function can express the step, keep it in `Workflow` instead of asking an LLM to coordinate it.
- If the only reason for a child agent is specialization, try `Agent.as_tool()` first.
- If a child must not inherit all parent tools, use `Subagent` with explicit `allowed_tools` or `denied_tools`.
- If you cannot describe why workers must coordinate autonomously, do not use `Swarm`.
- Scheduled daily tasks should call a bounded agent preset through the cron scheduler, not a free-form `Swarm`.
