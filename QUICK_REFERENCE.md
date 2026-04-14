# Agentica SDK - Quick Reference Diagram

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    agentica/__init__.py                      │
│  (Main module with eager & lazy imports)                     │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  EAGER IMPORTS (Always Available)                            │
│  ├── Tool, Function, FunctionCall                            │
│  ├── @tool decorator                                         │
│  ├── Agent, Workflow                                         │
│  └── Messages, Content types                                 │
│                                                               │
│  LAZY IMPORTS (On Demand)                                    │
│  ├── Tool implementations (ShellTool, CodeTool, etc.)        │
│  ├── Database implementations                                │
│  ├── Model providers (Claude, Ollama, etc.)                  │
│  ├── Skill system                                            │
│  └── Guardrails, MCP, etc.                                   │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

## Tool System Hierarchy

```
┌──────────────────────────────────────────────────────┐
│                Tool System (base.py)                  │
├──────────────────────────────────────────────────────┤
│                                                       │
│  ┌─────────────┐         ┌──────────────┐           │
│  │  Function   │         │ FunctionCall │           │
│  ├─────────────┤         ├──────────────┤           │
│  │ name        │◄────────│ function     │           │
│  │ description │         │ arguments    │           │
│  │ parameters  │         │ result       │           │
│  │ entrypoint  │         │ error        │           │
│  │ ... (25+)   │         │ call_id      │           │
│  │ properties  │         └──────────────┘           │
│  └─────────────┘                                     │
│                                                       │
│  Methods:                    Methods:                │
│  • from_callable()          • execute()              │
│  • is_available()           • get_call_str()         │
│  • to_dict()                                         │
│  • get_definition_...()                              │
│                                                       │
└──────────────────────────────────────────────────────┘
                            ▲
                            │ contains
                            │
┌──────────────────────────────────────────────────────┐
│                 Tool Container                       │
├──────────────────────────────────────────────────────┤
│                                                       │
│  class Tool:                                         │
│    - name: str                                       │
│    - description: str                                │
│    - functions: Dict[str, Function]                  │
│                                                       │
│  Methods:                                            │
│    - register(function, metadata...)                 │
│    - get_system_prompt()                             │
│                                                       │
└──────────────────────────────────────────────────────┘
                            ▲
                            │ extends
                            │
┌──────────────────────────────────────────────────────┐
│         Specific Tool Implementations                │
├──────────────────────────────────────────────────────┤
│  ├── BuiltinFileTool (ls, read, write, edit, glob)   │
│  ├── BuiltinExecuteTool (shell commands)             │
│  ├── BuiltinWebSearchTool (web search)               │
│  ├── BuiltinFetchUrlTool (fetch content)             │
│  ├── WeatherTool (weather info)                      │
│  ├── ShellTool (advanced shell)                      │
│  ├── CodeTool (code operations)                      │
│  └── ... (40+ more tools)                            │
└──────────────────────────────────────────────────────┘
```

## Function Definition & Registration

```
┌─────────────────────────────────────────────────────────┐
│         How to Define a Tool Function                    │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  Option 1: Simple @tool Decorator                       │
│  ──────────────────────────────                          │
│  @tool(                                                  │
│      name="search",                                      │
│      description="Search the web",                       │
│      concurrency_safe=False,                             │
│      is_read_only=False,                                 │
│      is_destructive=False                                │
│  )                                                        │
│  def search(query: str) -> str:                          │
│      """Search the web."""                              │
│      pass                                                │
│                                                           │
│                                                           │
│  Option 2: Tool Class                                   │
│  ──────────────────────                                 │
│  class SearchTool(Tool):                                │
│      def __init__(self):                                │
│          super().__init__(name="search_tool")           │
│          self.register(self.search,                     │
│              concurrency_safe=False)                    │
│                                                           │
│      async def search(self, query: str) -> str:         │
│          """Search the web."""                          │
│          pass                                            │
│                                                           │
│                                                           │
│  Option 3: Global Registry                             │
│  ───────────────────────────                            │
│  from agentica.tools.registry import register_tool      │
│                                                           │
│  register_tool("my_search", search_func)                │
│                                                           │
└─────────────────────────────────────────────────────────┘
```

## Function Execution Flow

```
┌─────────────────────────────────────────────────────────┐
│       FunctionCall.execute() Pipeline                    │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  1. Validate Input                                       │
│     └─► validate_input(arguments) → None or error       │
│                                                           │
│  2. Run Pre-Hook                                         │
│     └─► pre_hook(fc) or pre_hook()                      │
│                                                           │
│  3. Build Arguments                                      │
│     ├─► agent param (if function accepts)               │
│     ├─► fc param (if function accepts)                  │
│     └─► merge passed arguments                          │
│                                                           │
│  4. Call Function                                        │
│     ├─► If async: await entrypoint(**args)              │
│     └─► If sync: run_in_executor(entrypoint, **args)   │
│                                                           │
│  5. Run Post-Hook                                        │
│     └─► post_hook(fc) or post_hook()                    │
│                                                           │
│  6. Return Result                                        │
│     ├─► fc.result = ...                                 │
│     └─► success = True/False                            │
│                                                           │
│  Exceptions:                                             │
│     ├─► ToolCallException → skip execution              │
│     ├─► RetryAgentRun → retry this call                 │
│     └─► StopAgentRun → halt agent                       │
│                                                           │
└─────────────────────────────────────────────────────────┘
```

## Function Properties Cheat Sheet

```
┌──────────────────────────────────────────────────────────┐
│              Function Property Flags                      │
├──────────────────────────────────────────────────────────┤
│                                                            │
│  SAFETY & CONCURRENCY                                    │
│  ─────────────────────                                   │
│  concurrency_safe: True        ← Read-only operations    │
│                   False        ← Default, serialized      │
│                                                            │
│  is_read_only: True            ← Never modifies state    │
│               False             ← Default                 │
│                                                            │
│  is_destructive: True          ← Irreversible operations │
│                 False           ← Default                 │
│                                                            │
│  EXECUTION CONTROL                                        │
│  ──────────────────                                       │
│  show_result: True             ← Show to user            │
│              False              ← Default, LLM only       │
│                                                            │
│  stop_after_tool_call: True    ← Stop agent              │
│                       False     ← Default, continue       │
│                                                            │
│  timeout: int                  ← Seconds (None = 120s)    │
│                                                            │
│  manages_own_timeout: True     ← Tool handles timeout    │
│                      False      ← Framework wraps         │
│                                                            │
│  DISCOVERY & AVAILABILITY                                │
│  ────────────────────────                                │
│  deferred: True                ← Lazy-loaded discovery   │
│           False                 ← Default, always shown   │
│                                                            │
│  available_when: callable()    ← Dynamic check           │
│                 None            ← Always available        │
│                                                            │
│  interrupt_behavior: "cancel"  ← Can be terminated       │
│                     "block"     ← Must complete           │
│                                                            │
│  RESULT STORAGE                                           │
│  ──────────────                                           │
│  max_result_size_chars: None   ← Never persist           │
│                        int      ← Persist if > size       │
│                                                            │
└──────────────────────────────────────────────────────────┘
```

## Integration with Agent

```
┌──────────────────────────────────────────────────────────┐
│             Using Tools with Agent                       │
├──────────────────────────────────────────────────────────┤
│                                                            │
│  from agentica import Agent, OpenAIChat                   │
│                                                            │
│  # Create tools                                           │
│  from agentica.tools.base import Tool                     │
│  from agentica.tools import BuiltinFileTool              │
│                                                            │
│  my_tool = MyTool()                   # Custom Tool      │
│  file_tool = BuiltinFileTool()        # Built-in Tool    │
│                                                            │
│  # Create agent with tools                               │
│  agent = Agent(                                           │
│      model=OpenAIChat(),                                  │
│      tools=[                                              │
│          my_function,      # Simple function             │
│          my_tool,          # Tool instance               │
│          file_tool,        # Built-in Tool               │
│      ]                                                    │
│  )                                                        │
│                                                            │
│  # Run agent                                              │
│  response = agent.run("Use my tools...")                  │
│                                                            │
└──────────────────────────────────────────────────────────┘
```

## File Organization

```
agentica/
├── tools/                          ← Tool implementations
│   ├── __init__.py                    (exports)
│   ├── base.py                        (Function, FunctionCall, Tool)
│   ├── decorators.py                  (@tool decorator)
│   ├── registry.py                    (global tool registry)
│   ├── buildin_tools.py               (7 built-in tools)
│   ├── weather_tool.py                (example tool)
│   ├── shell_tool.py                  (shell execution)
│   ├── code_tool.py                   (code operations)
│   └── ... (40+ more tool files)
│
├── __init__.py                    ← Main module (eager & lazy imports)
├── version.py                     ← Version constant
├── config.py                      ← Global configuration
└── ... (other subsystems)
```

## Configuration Pattern

```
┌──────────────────────────────────────────────────────────┐
│           Configuration Hierarchy                         │
├──────────────────────────────────────────────────────────┤
│                                                            │
│  1. Environment Variables (highest priority)              │
│     └─► AGENTICA_HOME, AGENTICA_LOG_LEVEL, etc.         │
│                                                            │
│  2. Project .env file                                     │
│     └─► ./env                                             │
│                                                            │
│  3. User .env file                                        │
│     └─► ~/.agentica/.env                                  │
│                                                            │
│  4. Hardcoded defaults (lowest priority)                  │
│     └─► ~/.agentica, INFO level, etc.                     │
│                                                            │
│  From config.py:                                          │
│  • AGENTICA_HOME = ~/.agentica                            │
│  • AGENTICA_LOG_LEVEL = INFO                              │
│  • AGENTICA_LOG_FILE = (auto-created in DEBUG)            │
│  • AGENTICA_WORKSPACE_DIR = ~/.agentica/workspace        │
│  • AGENTICA_PROJECTS_DIR = ~/.agentica/projects          │
│                                                            │
└──────────────────────────────────────────────────────────┘
```

## Lazy Import Pattern

```
┌──────────────────────────────────────────────────────────┐
│         How Lazy Imports Work                             │
├──────────────────────────────────────────────────────────┤
│                                                            │
│  1. Define lazy imports in __init__.py                   │
│     _LAZY_IMPORTS = {                                    │
│         "Claude": "agentica.model.anthropic.claude",     │
│         "ShellTool": "agentica.tools.shell_tool",        │
│     }                                                     │
│                                                            │
│  2. User requests from agentica import Claude            │
│     └─► __getattr__("Claude") is called                  │
│                                                            │
│  3. Framework imports on demand                          │
│     module_path = _LAZY_IMPORTS["Claude"]                │
│     module = importlib.import_module(module_path)        │
│     _LAZY_CACHE["Claude"] = getattr(module, "Claude")   │
│                                                            │
│  4. Thread-safe caching with lock                        │
│     └─► Uses _LAZY_LOCK to prevent race conditions       │
│                                                            │
│  Benefits:                                               │
│  • Fast module import (no heavy dependencies)            │
│  • Only load what's needed                               │
│  • Thread-safe for concurrent access                     │
│                                                            │
└──────────────────────────────────────────────────────────┘
```

---

## Key Takeaways

✅ **Tool System**: Built on `Function`, `FunctionCall`, and `Tool` base classes
✅ **Two Ways to Define**: Simple `@tool` decorator or `Tool` class
✅ **Metadata-Driven**: Properties control concurrency, safety, execution
✅ **Async-First**: Supports both sync and async functions
✅ **Extensible**: Hooks, validation, dynamic availability, custom behavior
✅ **Modular**: Lazy imports for performance, registry for discovery
✅ **Safe**: Concurrency control, permission systems, input validation

---
