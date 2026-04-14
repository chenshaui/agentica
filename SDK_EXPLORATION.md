# Agentica SDK - Core Tool System & Architecture Exploration

**Date:** 2026-04-14  
**Project:** `/Users/xuming/Documents/Codes/agentica`  
**SDK Version:** 1.3.5

---

## 1. Version Management

### Location & Pattern
- **File:** `agentica/version.py`
- **Content:** Simple single-line version string
```python
__version__ = "1.3.5"
```

- **Import Pattern:** Imported in `__init__.py` via:
```python
from agentica.version import __version__  # noqa: F401
```

- **Export:** Included in module `__all__` for public API

**Key Insight:** Version is a simple string constant, making it easy to update. For SDK versioning, this should be updated when adding new capabilities.

---

## 2. Module Architecture Overview

### Directory Structure
```
agentica/
├── __init__.py          # Main module with eager & lazy imports
├── version.py           # Version constant
├── config.py            # Global configuration
├── tools/               # Tool implementations (48 files)
├── agent/               # Agent core logic
├── model/               # LLM provider integrations
├── db/                  # Database implementations
├── memory/              # Memory management
├── skills/              # Skill system
├── guardrails/          # Safety guardrails
├── mcp/                 # MCP (Model Context Protocol)
├── embedding/           # Embedding providers
├── vectordb/            # Vector database implementations
├── knowledge/           # Knowledge management
└── ... (other subsystems)
```

### Import Strategy

The `__init__.py` uses a **two-tier import pattern** for performance:

1. **Eager Imports** (fast, always available):
   - Core types: `Tool`, `Function`, `FunctionCall`
   - Message types: `Message`, `UserMessage`, `AssistantMessage`
   - Agent: `Agent`, `Workflow`
   - Config: Version, paths, logging

2. **Lazy Imports** (loaded on demand):
   - Heavy dependencies: `Claude`, `Ollama`, `LiteLLM`
   - Database implementations: `SqliteDb`, `PostgresDb`, `MySqlDb`
   - Vector databases: `InMemoryVectorDb`
   - Tool classes: `ShellTool`, `CodeTool`, `SearchSerperTool`
   - Skill system: `Skill`, `SkillRegistry`, `load_skills`
   - Guardrails: `InputGuardrail`, `OutputGuardrail`
   - MCP: `McpTool`, `MCPConfig`

**Pattern:** Uses `__getattr__` and `_LAZY_IMPORTS` dict with thread-safe caching:
```python
_LAZY_IMPORTS = {
    "SqliteDb": "agentica.db.sqlite",
    "Claude": "agentica.model.anthropic.claude",
    "ShellTool": "agentica.tools.shell_tool",
    # ... many more
}

_LAZY_CACHE = {}
_LAZY_LOCK = threading.Lock()

def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        if name not in _LAZY_CACHE:
            with _LAZY_LOCK:
                if name not in _LAZY_CACHE:
                    module_path = _LAZY_IMPORTS[name]
                    module = importlib.import_module(module_path)
                    _LAZY_CACHE[name] = getattr(module, name)
        return _LAZY_CACHE[name]
    raise AttributeError(...)
```

---

## 3. Global Configuration Pattern

### Location
- **File:** `agentica/config.py`

### Configuration Items
```python
# Directory management
AGENTICA_HOME = ~/.agentica  (expandable)
AGENTICA_DATA_DIR = ~/.agentica/data
AGENTICA_SKILL_DIR = ~/.agentica/skills
AGENTICA_EXTRA_SKILL_PATHS = [] (customizable)
AGENTICA_WORKSPACE_DIR = ~/.agentica/workspace
AGENTICA_PROJECTS_DIR = ~/.agentica/projects

# Logging
AGENTICA_LOG_LEVEL = "INFO" (can be DEBUG)
AGENTICA_LOG_FILE = (auto-created if DEBUG mode)
AGENTICA_MAX_MEMORY_CHARACTER_COUNT = 40000

# External integrations
LANGFUSE_SECRET_KEY = (optional)
LANGFUSE_PUBLIC_KEY = (optional)
LANGFUSE_BASE_URL = (optional)
LANGFUSE_TIMEOUT = 300 (seconds)
```

### Pattern
- **Environment Variables:** All settings can be customized via `.env`
- **Defaults:** Sensible defaults under `~/.agentica/`
- **Load Order:** `.env` in current directory, then `~/.agentica/.env`
- **Expansion:** Supports `~` expansion for home directory

---

## 4. Tool System Architecture

### 4.1 Base Classes

#### `Function` Class
**Location:** `agentica/tools/base.py` (lines 76-317)

**Purpose:** Represents a single callable function that an agent can invoke.

**Key Properties:**
```python
class Function(BaseModel):
    name: str                           # Function name (required)
    description: Optional[str]          # LLM-visible description
    parameters: Dict[str, Any]          # JSON Schema for parameters
    strict: Optional[bool]              # Strict mode for parameters
    
    # Execution control
    entrypoint: Optional[Callable]      # The actual function to call
    skip_entrypoint_processing: bool    # Skip auto-processing
    sanitize_arguments: bool            # Clean up arg types
    show_result: bool                   # Show result to user
    stop_after_tool_call: bool          # Stop agent after this call
    
    # Concurrency & safety
    concurrency_safe: bool              # Can run in parallel
    is_read_only: bool                  # Never modifies state
    is_destructive: bool                # Irreversible operations
    
    # Hooks & validation
    pre_hook: Optional[Callable]        # Runs before execution
    post_hook: Optional[Callable]       # Runs after execution
    validate_input: Optional[Callable]  # Validate arguments
    available_when: Optional[Callable]  # Dynamic availability check
    
    # Execution constraints
    timeout: Optional[int]              # Timeout in seconds
    manages_own_timeout: bool           # Tool handles its own timeout
    interrupt_behavior: str             # "cancel" or "block"
    deferred: bool                      # Lazy-loaded, not shown by default
    max_result_size_chars: Optional[int] # Persist results if > size
```

**Static Methods:**
```python
Function._parse_parameters(entrypoint, strict=False) -> Dict[str, Any]
    # Parse type hints into JSON Schema

Function.from_callable(c: Callable, strict=False) -> Function
    # Create Function from a callable, auto-detecting @tool metadata

function.process_entrypoint(strict=False)
    # Process and validate an entrypoint
```

**Methods:**
```python
function.to_dict() -> Dict[str, Any]
    # Export as dict (for LLM schema)

function.is_available() -> bool
    # Check if tool is available (calls available_when if set)

function.get_definition_for_prompt_dict() -> Dict
    # Get function definition formatted for prompt

function.get_definition_for_prompt() -> str
    # Get function definition as JSON string
```

**Agent Reference:**
- Uses `weakref` to avoid circular references
- Pattern: `_agent_ref: weakref.ReferenceType` with property accessors

---

#### `FunctionCall` Class
**Location:** `agentica/tools/base.py` (lines 319-443)

**Purpose:** Represents an invocation of a function with arguments and results.

**Key Properties:**
```python
class FunctionCall(BaseModel):
    function: Function                  # Reference to Function
    arguments: Optional[Dict[str, Any]] # Call arguments
    result: Optional[Any]               # Call result
    call_id: Optional[str]              # Unique call ID
    error: Optional[str]                # Error if failed
```

**Methods:**
```python
fc.get_call_str() -> str
    # String representation: "function_name(arg1=val1, arg2=val2)"

async fc.execute() -> bool
    # Execute the function with all hooks and validation
    # Returns: True if successful, False if error
    # Raises: ToolCallException (RetryAgentRun, StopAgentRun)
```

**Execution Pipeline:**
1. Validate input (`validate_input` callback)
2. Run pre-hook (`pre_hook`)
3. Build arguments (including special `agent`, `fc` params)
4. Call function (auto-detects sync/async)
5. Run post-hook (`post_hook`)
6. Return success/failure

---

#### `Tool` Class
**Location:** `agentica/tools/base.py` (lines 604-663)

**Purpose:** Container for grouping related functions.

**Key Properties:**
```python
class Tool:
    name: str                           # Tool name
    description: str                    # Tool description
    functions: Dict[str, Function]      # Registered functions
```

**Core Methods:**
```python
tool.register(function, sanitize_arguments=True, 
              concurrency_safe=False, is_read_only=False,
              is_destructive=False, available_when=None) -> None
    # Register a function with this tool
    # Auto-creates Function from callable
    # Auto-detects @tool metadata

tool.get_system_prompt() -> Optional[str]
    # Override in subclasses to provide tool-specific prompts
    # Guides LLM on how to use the tool

tool.__repr__() -> str
    # Format: "<ToolClassName name=tool_name functions=[...]>"
```

---

### 4.2 Tool Registration Pattern

#### Simple Function Decorator: `@tool`
**Location:** `agentica/tools/decorators.py`

```python
from agentica.tools.decorators import tool

@tool(
    name="search",                      # Optional, defaults to function name
    description="Search the web",       # Optional, defaults to docstring
    show_result=False,
    sanitize_arguments=True,
    stop_after_tool_call=False,
    concurrency_safe=False,             # Set True for read-only tools
    is_read_only=False,
    is_destructive=False,
    deferred=False,                     # Set True for tool_search discovery
    interrupt_behavior="cancel",        # "cancel" or "block"
    available_when=None,                # Optional callback
)
def search_web(query: str, max_results: int = 5) -> str:
    """Search the web for query."""
    pass

agent = Agent(tools=[search_web])
```

**Mechanism:**
- Decorator attaches `_tool_metadata` dict to function
- `Function.from_callable()` detects metadata and uses it
- Supports per-function customization without Tool class

---

#### Tool Class Pattern
**Location:** `agentica/tools/base.py` and examples

```python
from agentica.tools.base import Tool

class MyTool(Tool):
    def __init__(self):
        super().__init__(name="my_tool", description="My tool description")
        
        # Register functions with metadata
        self.register(
            self.function1,
            concurrency_safe=True,
            is_read_only=True
        )
        self.register(self.function2)
    
    async def function1(self, param: str) -> str:
        """Function documentation."""
        return "result"
    
    def function2(self) -> int:
        """Another function."""
        return 42

tool = MyTool()
agent = Agent(tools=[tool])
```

**Advantages:**
- Organize related functions together
- Share state between functions
- Provide system prompts
- Manage initialization/cleanup

---

### 4.3 Real-World Example: WeatherTool

**Location:** `agentica/tools/weather_tool.py`

**Pattern:**
```python
class WeatherTool(Tool):
    def __init__(self):
        super().__init__(name="get_weather_tool")
        self.register(self.get_weather)
        self.openweather_api_key = os.getenv("OPENWEATHER_API_KEY")
    
    async def get_weather(self, city: str = None) -> str:
        """Get weather info for a city with fallback strategy.
        
        Args:
            city: City name (English, Chinese, or "auto:ip")
        
        Returns:
            str: Weather data in markdown format
        """
        # Implementation with multiple fallbacks
        # - OpenWeatherMap (if API key)
        # - wttr.in (free)
        # - Open-Meteo (free, no key)
```

**Key Features:**
- Async function support
- Error handling with fallbacks
- Type hints for parameter schema
- Markdown-formatted results
- Environment variable configuration

---

### 4.4 Built-in Tools

**Location:** `agentica/tools/buildin_tools.py`

**Available Built-in Tools:**
1. `BuiltinFileTool` - File operations (ls, read, write, edit, glob, grep)
2. `BuiltinExecuteTool` - Shell command execution
3. `BuiltinWebSearchTool` - Web search
4. `BuiltinFetchUrlTool` - URL content fetching
5. `BuiltinTodoTool` - Task list management
6. `BuiltinTaskTool` - Sub-agent task delegation
7. `BuiltinMemoryTool` - Memory management

**Example: BuiltinFileTool**
```python
class BuiltinFileTool(Tool):
    def __init__(self, work_dir=None, max_read_lines=500, 
                 max_line_length=2000, sandbox_config=None):
        super().__init__(name="builtin_file_tool")
        
        # Read-only tools are concurrency_safe
        self.register(self.ls, concurrency_safe=True, is_read_only=True)
        self.register(self.read_file, concurrency_safe=True, is_read_only=True)
        
        # Write tools must be serialized (not concurrency_safe)
        self.register(self.write_file, is_destructive=True)
        self.register(self.edit_file, is_destructive=True)
        self.register(self.glob, concurrency_safe=True, is_read_only=True)
        self.register(self.grep, concurrency_safe=True, is_read_only=True)
```

**Key Patterns:**
- Read-only tools marked with `concurrency_safe=True`
- Destructive tools marked with `is_destructive=True`
- Serialization for write operations
- Path resolution and sandbox enforcement

---

## 5. Global Tool Registry

### Location
**File:** `agentica/tools/registry.py`

### API
```python
from agentica.tools.registry import (
    register_tool,      # Add tool to registry
    get_tool,          # Retrieve by name
    list_tools,        # List all names
    unregister_tool,   # Remove tool
    clear_registry,    # Clear all
)

# Usage
register_tool("calculator", my_calculator_func)
tool = get_tool("calculator")
all_names = list_tools()  # Returns sorted list
unregister_tool("calculator")
clear_registry()
```

### Implementation
```python
_TOOL_REGISTRY: Dict[str, Union[Callable, "Tool"]] = {}

def register_tool(name: str, tool_or_func: Union[Callable, Tool]) -> None:
    _TOOL_REGISTRY[name] = tool_or_func

def get_tool(name: str) -> Union[Callable, Tool]:
    if name not in _TOOL_REGISTRY:
        raise KeyError(f"Tool '{name}' not found. Available: {sorted(_TOOL_REGISTRY.keys())}")
    return _TOOL_REGISTRY[name]

def list_tools() -> List[str]:
    return sorted(_TOOL_REGISTRY.keys())
```

---

## 6. Tool Metadata & Properties

### Concurrency Safety
```python
concurrency_safe: bool
    # True  = Tool may run in parallel with other concurrency_safe tools
    # False = Tool must be serialized (default for safety)
    
    # Examples:
    # - Read-only tools: True (read_file, grep, web_search)
    # - Shell tools: False (execute, can affect state)
    # - Write tools: False (write_file, edit_file)
```

### Safety Classification
```python
is_read_only: bool
    # True if tool NEVER modifies state
    # Used by permission systems to skip user confirmation

is_destructive: bool
    # True for irreversible operations
    # Examples: delete, overwrite, send, execute
    # Used to require extra caution/user confirmation
```

### Execution Control
```python
timeout: Optional[int]
    # Timeout in seconds for execution
    # None = use default (120s)
    # Each invocation wrapped with asyncio.wait_for(timeout=)

manages_own_timeout: bool
    # True = Tool handles timeout internally
    # False = Framework wraps with timeout (default)
    
    # Example: Shell tools often manage their own timeout

show_result: bool
    # True = Show result to user after execution
    # False = Only send to LLM (default)

stop_after_tool_call: bool
    # True = Stop agent after this tool completes
    # False = Continue conversation (default)
```

### Deferred Tools
```python
deferred: bool
    # True = Tool description NOT sent to LLM by default
    # False = Always included in schema (default)
    
    # Use case: Many specialized tools, discovered via tool_search
    # Benefit: Reduces per-call token cost
```

### Interrupt Behavior
```python
interrupt_behavior: str
    # "cancel" = Tool can be cleanly terminated mid-execution
    #           Example: shell commands
    # "block"  = Tool must complete before honoring cancellation
    #           Example: agent delegation
```

### Result Storage
```python
max_result_size_chars: Optional[int]
    # None = Never persist (default for read_file)
    # int  = Persist to disk if result exceeds size
    #       Example: 50_000 for execute/bash tools
```

---

## 7. Tool Function Definition

### Parameter Schema
Automatically generated from Python type hints using `Function._parse_parameters()`:

```python
def my_tool(query: str, max_results: int = 5, debug: bool = False) -> str:
    """Search for query with results limit."""
    pass

# Auto-generates JSON Schema:
{
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "max_results": {"type": "integer"},
        "debug": {"type": "boolean"}
    },
    "required": ["query"]  # Parameters without defaults
}
```

### Docstring Extraction
- Function docstring becomes description
- Used by LLM to understand function purpose
- Supports markdown formatting

### Type Hint Support
- Standard Python type hints: `str`, `int`, `bool`, `float`, `list`, `dict`
- Optional types: `Optional[str]`
- Complex types: Converted to JSON Schema

---

## 8. Advanced Tool Patterns

### 4.1 Hooks System

**Pre/Post Execution Hooks:**
```python
def my_pre_hook(fc):
    """Called before execution."""
    print(f"About to call: {fc.function.name}")

def my_post_hook(fc):
    """Called after execution."""
    print(f"Result: {fc.result}")

func = Function(
    name="my_func",
    entrypoint=my_callable,
    pre_hook=my_pre_hook,
    post_hook=my_post_hook,
)
```

**Hook Signature Options:**
```python
# No parameters
def simple_hook():
    pass

# FunctionCall parameter
def with_fc(fc):
    # Can access: fc.function, fc.arguments, fc.result, fc.error

# Agent parameter
def with_agent(agent):
    # Can access: agent
```

### Exceptions & Control Flow

```python
from agentica.tools.base import (
    ToolCallException,
    RetryAgentRun,
    StopAgentRun,
)

# Retry the current tool call
raise RetryAgentRun("Need to retry")

# Stop agent execution entirely
raise StopAgentRun("Fatal error", user_message="Unable to complete")

# Both support:
# - user_message: Message to show user
# - agent_message: Message for agent context
# - messages: Additional context messages
```

### 4.2 Input Validation

```python
def validate_search_input(arguments: Dict[str, Any]) -> Optional[str]:
    """Validate tool input before execution.
    
    Returns:
        None = valid
        str = error message (execution will be skipped)
    """
    query = arguments.get("query", "")
    
    if not query or len(query) < 2:
        return "Query must be at least 2 characters"
    
    if len(query) > 1000:
        return "Query cannot exceed 1000 characters"
    
    return None  # Valid

func = Function(
    name="search",
    entrypoint=search_fn,
    validate_input=validate_search_input,
)
```

### 4.3 Dynamic Availability

```python
def is_weather_api_available():
    """Check if weather API key is configured."""
    return bool(os.getenv("OPENWEATHER_API_KEY"))

weather_func = Function(
    name="get_weather",
    entrypoint=get_weather_fn,
    available_when=is_weather_api_available,
)

# Usage:
if weather_func.is_available():
    # Only include in schema if available
```

---

## 9. Integration Patterns

### 9.1 With Agent
```python
from agentica import Agent, OpenAIChat

# Simple function
def calculator(expression: str) -> str:
    """Evaluate mathematical expression."""
    return str(eval(expression))

# Tool class
from agentica.tools.base import Tool
tool = MyTool()

# Built-in tools
from agentica.tools import BuiltinFileTool, BuiltinExecuteTool

agent = Agent(
    model=OpenAIChat(),
    tools=[
        calculator,
        tool,
        BuiltinFileTool(),
        BuiltinExecuteTool(),
    ]
)

response = agent.run("What is 2 + 2?")
```

### 9.2 Using Global Registry
```python
from agentica.tools.registry import register_tool, get_tool

# Register
register_tool("my_search", search_function)

# Retrieve
search_tool = get_tool("my_search")

# Use with Agent
from agentica import Agent
agent = Agent(tools=[get_tool("my_search")])
```

---

## 10. File Operations Tool Example

### Pattern for File Tool (Expanded)
```python
class BuiltinFileTool(Tool):
    def __init__(self, work_dir=None, max_read_lines=500,
                 max_line_length=2000, sandbox_config=None):
        super().__init__(name="builtin_file_tool")
        
        self.work_dir = Path(work_dir) if work_dir else Path.cwd()
        self.max_read_lines = max_read_lines
        self.max_line_length = max_line_length
        self._file_locks: Dict[str, asyncio.Lock] = {}
        self._file_read_state: Dict[str, Dict[str, Any]] = {}
        
        # Register functions
        self.register(self.ls, concurrency_safe=True, is_read_only=True)
        self.register(self.read_file, concurrency_safe=True, is_read_only=True)
        self.register(self.write_file, is_destructive=True)
        # ... more registrations
    
    def _resolve_path(self, path: str) -> Path:
        """Resolve path supporting ~, absolute, and relative paths."""
        if path.startswith("~"):
            return Path(path).expanduser()
        p = Path(path)
        if p.is_absolute():
            return p
        return self.work_dir / p
    
    async def ls(self, path: str = ".") -> str:
        """List directory contents."""
        # Implementation
    
    async def read_file(self, file: str, offset: int = 0) -> str:
        """Read file content."""
        # Implementation
    
    async def write_file(self, file: str, content: str) -> str:
        """Write file content."""
        # Implementation
```

**Key Patterns:**
- Async functions for I/O operations
- Path resolution with home directory support
- State management (locks, read tracking)
- Error handling with informative messages
- Sandbox enforcement for path restrictions

---

## 11. Tool Registration in __init__.py

### Export Pattern
```python
# In __init__.py:

# Eager imports (always available)
from agentica.tools.base import Tool, Function, FunctionCall
from agentica.tools.decorators import tool

# Lazy imports (loaded on demand)
_LAZY_IMPORTS = {
    "ShellTool": "agentica.tools.shell_tool",
    "CodeTool": "agentica.tools.code_tool",
    "SearchSerperTool": "agentica.tools.search_serper_tool",
    "WeatherTool": "agentica.tools.weather_tool",
    # ... many more
}

# In __all__:
__all__ = [
    "Tool", "Function", "FunctionCall", "tool",
    # ... other exports
    *_LAZY_IMPORTS.keys(),  # Include lazy imports
]
```

---

## 12. Summary: Integration Checklist for New Capabilities

### To Add a New Tool Type:

1. **Create tool file** in `agentica/tools/my_tool.py`
   - Extend `Tool` class or use `@tool` decorator
   - Register functions with `self.register()`
   - Document with docstrings

2. **Add type hints**
   - Use standard Python types for parameter schema
   - Return type for documentation

3. **Mark safety properties**
   - `concurrency_safe=True` for read-only operations
   - `is_read_only=True` for read-only operations
   - `is_destructive=True` for irreversible operations

4. **Support async/sync**
   - Async functions preferred for I/O
   - Sync functions auto-wrapped in executor

5. **Add to exports** in `agentica/tools/__init__.py`
   - Add to `__all__` for immediate availability or
   - Add to `_LAZY_IMPORTS` in main `__init__.py` for lazy loading

6. **Test with Agent**
   ```python
   from agentica import Agent, OpenAIChat
   from agentica.tools.my_tool import MyTool
   
   agent = Agent(
       model=OpenAIChat(),
       tools=[MyTool()]
   )
   response = agent.run("Use my tool...")
   ```

### Configuration for New Tool:
- Add to `config.py` if needs global settings
- Support environment variables
- Document in README

### Version Update:
- Update `agentica/version.py` when releasing
- Update changelog

---

## 13. Key Files Reference

| File | Purpose | Lines |
|------|---------|-------|
| `agentica/__init__.py` | Main module exports | 403 |
| `agentica/version.py` | Version constant | 2 |
| `agentica/config.py` | Global configuration | 49 |
| `agentica/tools/__init__.py` | Tools exports | 52 |
| `agentica/tools/base.py` | Tool, Function, FunctionCall | 664 |
| `agentica/tools/decorators.py` | @tool decorator | 82 |
| `agentica/tools/registry.py` | Global tool registry | 74 |
| `agentica/tools/buildin_tools.py` | Built-in tools | 2000+ |
| `agentica/tools/weather_tool.py` | Example: WeatherTool | 255 |

---

## 14. Best Practices

### 1. Concurrency & Performance
- Mark read-only operations with `concurrency_safe=True`
- Keep write operations serialized for safety
- Use async/await for I/O-bound operations

### 2. Error Handling
- Return meaningful error messages
- Use `ToolCallException` for critical issues
- Log via `agentica.utils.log.logger`

### 3. Type Safety
- Always include type hints for parameters
- Return type hints for LLM understanding
- Use `Optional` for nullable parameters

### 4. Security
- Validate all inputs via `validate_input`
- Use sandbox config to restrict file access
- Sanitize arguments with `sanitize_arguments=True`

### 5. Documentation
- Provide clear docstrings
- Use markdown in descriptions
- Document all parameters and return types

### 6. Configuration
- Use environment variables for API keys
- Support fallback mechanisms
- Document required configuration

---

