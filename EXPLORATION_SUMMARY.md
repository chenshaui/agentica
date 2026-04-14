# Agentica SDK Exploration - Executive Summary

**Date:** April 14, 2026  
**Project:** Agentica SDK v1.3.5  
**Scope:** Core tool system architecture and integration patterns

---

## Overview

The Agentica SDK is built around a sophisticated **tool system** that enables AI agents to perform actions in the world. This document summarizes findings from exploring the core architecture and provides patterns for integrating new capabilities.

## 1. Key Findings

### Architecture Philosophy
- **Two-Tier Import System**: Eager imports for fast module loading + lazy imports for heavy dependencies
- **Metadata-Driven Tools**: Tool behavior controlled by properties, not just code logic
- **Type-Safe Schema Generation**: JSON Schema automatically generated from Python type hints
- **Async-First Design**: Supports both sync and async functions with automatic execution handling

### Core Components
1. **`Function` Class** - Represents a single callable with 25+ configurable properties
2. **`FunctionCall` Class** - Represents invocation with execution pipeline
3. **`Tool` Class** - Container for grouping related functions
4. **Global Registry** - Named tool lookup and management

### Tool System Capabilities
✅ Concurrency control (safe vs. serialized operations)  
✅ Safety classification (read-only, destructive)  
✅ Execution hooks (pre/post processing)  
✅ Input validation with early rejection  
✅ Dynamic availability checking  
✅ Result size-based persistence  
✅ Timeout management  
✅ Interrupt behavior control  
✅ Deferred discovery for tool optimization  

---

## 2. Module Structure

### Import Strategy
```
agentica/__init__.py
├── EAGER IMPORTS (always available)
│   ├── Tool, Function, FunctionCall
│   ├── @tool decorator
│   ├── Agent, Workflow
│   └── Messages, Content types
│
└── LAZY IMPORTS (on demand)
    ├── Tool implementations (70+ tools)
    ├── Database implementations
    ├── Model providers
    ├── Skill system
    └── Guardrails, MCP
```

### Configuration
- **File**: `agentica/config.py`
- **Env Variables**: All settings customizable via `.env`
- **Defaults**: Sensible defaults under `~/.agentica/`

### Version Management
- **File**: `agentica/version.py` (single line: `__version__ = "1.3.5"`)
- **Pattern**: Simple to update, imported in `__init__.py`

---

## 3. Tool Definitions - Three Approaches

### Approach 1: Simple Function with @tool Decorator
```python
from agentica.tools.decorators import tool

@tool(name="search", concurrency_safe=False, is_read_only=False)
def search(query: str) -> str:
    """Search the web."""
    pass
```
**Best for**: Single functions, minimal setup

### Approach 2: Tool Class
```python
from agentica.tools.base import Tool

class SearchTool(Tool):
    def __init__(self):
        super().__init__(name="search_tool")
        self.register(self.search)
    
    async def search(self, query: str) -> str:
        """Search the web."""
        pass
```
**Best for**: Multiple related functions, state sharing, system prompts

### Approach 3: Global Registry
```python
from agentica.tools.registry import register_tool

register_tool("my_search", search_function)
```
**Best for**: Dynamic tool discovery, plugin systems

---

## 4. Function Properties: The Control Layer

### Safety & Concurrency (Performance)
- `concurrency_safe: bool` - Allow parallel execution with other read-only tools
- `is_read_only: bool` - Never modifies state
- `is_destructive: bool` - Irreversible operations (delete, send, execute)

### Execution Control (Behavior)
- `show_result: bool` - Show result to user (vs. LLM only)
- `stop_after_tool_call: bool` - Halt agent after execution
- `timeout: int` - Execution timeout in seconds
- `manages_own_timeout: bool` - Tool handles its own timeout

### Validation & Hooks (Processing)
- `validate_input: Callable` - Pre-execution validation
- `pre_hook: Callable` - Execute before function
- `post_hook: Callable` - Execute after function
- `available_when: Callable` - Dynamic availability check

### Discovery & Availability (Schema)
- `deferred: bool` - Lazy-load discovery (not in default schema)
- `interrupt_behavior: str` - "cancel" or "block"

### Result Management (Storage)
- `max_result_size_chars: int` - Persist to disk if exceeds size

---

## 5. Execution Pipeline

Every function call follows this pipeline:

```
1. Input Validation       → validate_input(arguments)
2. Pre-Hook              → pre_hook(fc or nothing)
3. Argument Building     → Add special agent/fc params
4. Function Call         → await async OR run_in_executor sync
5. Post-Hook             → post_hook(fc or nothing)
6. Return                → Result + success flag
```

### Exception Handling
- `ToolCallException` - Skip execution, return error
- `RetryAgentRun` - Retry this tool call
- `StopAgentRun` - Halt agent entirely

---

## 6. Built-in Tools Reference

The SDK includes 7 built-in tools:

| Tool | Purpose | Functions |
|------|---------|-----------|
| `BuiltinFileTool` | File operations | ls, read, write, edit, glob, grep |
| `BuiltinExecuteTool` | Shell commands | execute |
| `BuiltinWebSearchTool` | Web search | web_search |
| `BuiltinFetchUrlTool` | URL content | fetch_url |
| `BuiltinTodoTool` | Task management | write_todos, read_todos |
| `BuiltinTaskTool` | Sub-agents | task |
| `BuiltinMemoryTool` | Memory ops | memory_search, memory_write |

Plus 70+ additional tools (weather, shell, code analysis, search providers, etc.)

---

## 7. Real-World Example: WeatherTool

The `WeatherTool` demonstrates best practices:

```python
class WeatherTool(Tool):
    def __init__(self):
        super().__init__(name="get_weather_tool")
        self.register(self.get_weather)
        self.openweather_api_key = os.getenv("OPENWEATHER_API_KEY")
    
    async def get_weather(self, city: str = None) -> str:
        """Get weather with fallback sources."""
        # Try OpenWeatherMap (if API key)
        # Fall back to wttr.in (free)
        # Fall back to Open-Meteo (free)
```

**Patterns demonstrated:**
- Async function support
- Environment variable configuration
- Multiple fallback mechanisms
- Markdown-formatted results
- Error handling and logging

---

## 8. Global Tool Registry API

```python
from agentica.tools.registry import (
    register_tool,      # Add tool
    get_tool,          # Retrieve by name
    list_tools,        # List all names
    unregister_tool,   # Remove tool
    clear_registry,    # Clear all
)
```

**Use case**: Dynamic tool discovery, plugin systems

---

## 9. Integration Checklist for New Capabilities

To add new tools to the SDK:

1. **Create tool file** → `agentica/tools/my_tool.py`
2. **Define class** → Extend `Tool` or use `@tool`
3. **Register functions** → `self.register(func, metadata...)`
4. **Add type hints** → Python type hints → JSON Schema
5. **Mark properties** → `is_read_only`, `is_destructive`, etc.
6. **Support async/sync** → Async preferred, sync auto-wrapped
7. **Export** → Add to `agentica/tools/__init__.py` or `_LAZY_IMPORTS`
8. **Test with Agent** → Create agent with tool, run query
9. **Update version** → Bump in `agentica/version.py` if new capability

---

## 10. Key Architectural Patterns

### Pattern 1: Lazy Import Optimization
```python
_LAZY_IMPORTS = {"Claude": "agentica.model.anthropic.claude"}
# Framework dynamically imports on demand with thread-safe caching
```

### Pattern 2: Metadata-Driven Configuration
```python
@tool(concurrency_safe=True, is_read_only=True, timeout=30)
def read_file(path: str) -> str:
    # Behavior controlled by properties, not code logic
```

### Pattern 3: Type-Safe Schema Generation
```python
def search(query: str, max_results: int = 5) -> str:
    # Automatically generates JSON Schema from type hints
```

### Pattern 4: Hook-Based Extensibility
```python
func = Function(
    entrypoint=my_func,
    validate_input=validate_fn,
    pre_hook=pre_fn,
    post_hook=post_fn,
)
```

### Pattern 5: Execution Pipeline
```python
# FunctionCall.execute() follows strict pipeline:
# validate → pre-hook → execute → post-hook → return
```

---

## 11. Best Practices

### Concurrency & Performance
✅ Mark read-only operations with `concurrency_safe=True`  
✅ Keep write operations serialized (default)  
✅ Use async/await for I/O operations  

### Safety & Security
✅ Validate all inputs via `validate_input`  
✅ Use sandbox config to restrict file access  
✅ Mark destructive ops with `is_destructive=True`  

### Type Safety
✅ Always include type hints for parameters  
✅ Provide return type hints  
✅ Use `Optional` for nullable parameters  

### Documentation
✅ Clear docstrings (become LLM descriptions)  
✅ Markdown in descriptions  
✅ Document all parameters and return types  

### Configuration
✅ Use environment variables for API keys  
✅ Support fallback mechanisms  
✅ Document required configuration  

---

## 12. Files Reference

| Path | Purpose | Size |
|------|---------|------|
| `agentica/__init__.py` | Main exports (eager + lazy) | 403 lines |
| `agentica/version.py` | Version constant | 2 lines |
| `agentica/config.py` | Global configuration | 49 lines |
| `agentica/tools/__init__.py` | Tool exports | 52 lines |
| `agentica/tools/base.py` | Function/FunctionCall/Tool | 664 lines |
| `agentica/tools/decorators.py` | @tool decorator | 82 lines |
| `agentica/tools/registry.py` | Global registry | 74 lines |
| `agentica/tools/buildin_tools.py` | Built-in tools | 2000+ lines |

---

## 13. Document Index

📄 **SDK_EXPLORATION.md** (14 sections)
- Complete deep-dive into tool system
- Base classes, registration patterns
- Advanced patterns (hooks, validation)
- Integration examples
- Best practices

📄 **QUICK_REFERENCE.md** (Visual diagrams)
- Architecture overview
- Tool system hierarchy
- Execution flow
- Property cheat sheet
- Configuration patterns

📄 **EXPLORATION_SUMMARY.md** (This document)
- Executive summary
- Key findings
- Quick patterns
- Checklist

---

## 14. Next Steps for Integration

### For New Tool Development:
1. Review `WeatherTool` as implementation example
2. Use `@tool` decorator for single functions or `Tool` class for multiple
3. Follow the property checklist for concurrency/safety
4. Test with Agent: `agent = Agent(tools=[my_tool])`
5. Update version when releasing

### For Framework Extension:
1. Understand lazy import pattern
2. Add to `_LAZY_IMPORTS` dict if heavy dependency
3. Update `__all__` for public API
4. Consider tool registry for dynamic discovery

---

## Conclusion

The Agentica SDK tool system is **sophisticated yet accessible**:

- **Simple API** for basic use cases (@tool decorator)
- **Powerful configuration** for advanced scenarios (25+ properties)
- **Performance-oriented** (lazy imports, concurrency control)
- **Type-safe** (automatic JSON Schema generation)
- **Extensible** (hooks, validation, custom behavior)
- **Production-ready** (error handling, timeout management)

The patterns documented here enable building reliable, performant AI agent tools while maintaining safety and security best practices.

---

**Prepared by:** AI Analysis System  
**For:** Agentica SDK Development Team  
**Version:** 1.3.5
