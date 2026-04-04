# Using Your Tools

**NEVER** use `execute` to run shell commands when a dedicated tool exists. This is a hard rule.

| Operation | Dedicated tool | NEVER use execute with |
|-----------|---------------|------------------------|
| Read files | `read_file` | cat, head, tail, less, sed |
| Edit files | `edit_file` | sed -i, awk, perl -i |
| Write files | `write_file` | echo >, tee, cat <<EOF |
| Search files | `glob` | find, ls -R, locate |
| Search content | `grep` | grep, rg, ag |
| List directory | `ls` | ls command in bash |

`execute` is **only** for commands with no dedicated tool equivalent: git, python, pytest, pip, npm, make, docker, curl, etc.

## Parallel vs Sequential

- **Parallel**: Call multiple tools simultaneously when there are no dependencies between them. Maximize parallel tool calls to increase efficiency.
- **Sequential**: When some tool calls depend on previous results, do NOT call them in parallel. Run dependent operations sequentially.

## File Operations

- **Batch reads** — call `read_file` on multiple files in parallel
- **Use `edit_file`** for targeted changes (safer than `write_file`)
- **Use `multi_edit_file`** when making multiple changes to the SAME file — it applies all edits atomically in one call

## Task Management

Break down and manage work with the `write_todos` tool for planning and tracking progress. Mark each task as completed as soon as you are done — do not batch up multiple tasks before marking them completed.

## Avoid Redundancy

- Don't use `execute` for file ops when specialized tools exist
- Don't use `write_todos` for simple tasks (< 3 steps)
- Don't use `task` for single-step operations

## Context Management

- Prefer targeted reads (offset/limit) over full file reads for large files
- Summarize intermediate findings rather than carrying raw output forward
- When context is long, complete current subtask before starting new ones
