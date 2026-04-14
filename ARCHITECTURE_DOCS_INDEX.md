# Agentica SDK - Architecture Documentation Index

**Generated:** April 14, 2026  
**SDK Version:** 1.3.5  
**Project:** `/Users/xuming/Documents/Codes/agentica`

---

## 📚 Documentation Collection

This project now includes comprehensive architecture documentation covering the core tool system and integration patterns. All documents are located in the project root directory.

### 1. **EXPLORATION_SUMMARY.md** ⭐ START HERE
- **Length:** ~11 KB (350 lines)
- **Audience:** Decision makers, team leads, architects
- **Purpose:** Executive summary of findings and key patterns
- **Key Sections:**
  - Key findings (4 components, 9 capabilities)
  - Module structure overview
  - Three approaches to tool definitions
  - Function properties reference
  - Execution pipeline
  - Built-in tools reference
  - Integration checklist
  - Best practices
  - Conclusion

**Use this to:** Get up to speed quickly on the architecture

---

### 2. **SDK_EXPLORATION.md** 🔬 DEEP DIVE
- **Length:** ~25 KB (650 lines)
- **Audience:** Developers, integrators, framework maintainers
- **Purpose:** Complete technical deep-dive into tool system architecture
- **Key Sections:**
  - Version management pattern (v1.3.5)
  - Module architecture (eager + lazy imports)
  - Global configuration pattern (11 settings)
  - Tool system architecture (Function, FunctionCall, Tool classes)
  - Tool registration patterns (@tool decorator, Tool class, global registry)
  - Real-world example (WeatherTool implementation)
  - Built-in tools overview (7 tools)
  - Advanced patterns (hooks, exceptions, validation, availability)
  - Integration patterns (Agent, registry)
  - File operations tool example
  - Tool registration in __init__.py
  - Integration checklist for new capabilities
  - Key files reference (9 files)
  - Best practices (6 categories)

**Use this to:** Understand implementation details and build new tools

---

### 3. **QUICK_REFERENCE.md** 📊 VISUAL GUIDE
- **Length:** ~23 KB (ASCII diagrams + text)
- **Audience:** Developers needing quick lookups
- **Purpose:** Visual architecture diagrams and quick reference sheets
- **Key Sections:**
  - Architecture overview diagram
  - Tool system hierarchy diagram
  - Function definition & registration options
  - Function execution flow pipeline
  - Function properties cheat sheet
  - Agent integration example
  - File organization tree
  - Configuration hierarchy diagram
  - Lazy import pattern diagram
  - Key takeaways

**Use this to:** Quick lookups, understanding relationships, cheat sheets

---

### 4. **GATEWAY_EXPLORATION.md** 🌐 INFRASTRUCTURE
- **Length:** ~30 KB (from related exploration)
- **Audience:** DevOps, infrastructure engineers
- **Purpose:** Gateway system architecture and deployment patterns
- **Note:** Companion document for infrastructure integration

---

## 📋 Quick Navigation

### By Use Case

#### "I need to add a new tool"
1. Read: **EXPLORATION_SUMMARY.md** Section 3 (Three Approaches)
2. Read: **SDK_EXPLORATION.md** Section 4.3 (WeatherTool Example)
3. Reference: **QUICK_REFERENCE.md** (Properties Cheat Sheet)
4. Follow: **EXPLORATION_SUMMARY.md** Section 9 (Integration Checklist)

#### "I need to understand the architecture"
1. Start: **EXPLORATION_SUMMARY.md** (5-10 min overview)
2. Deep dive: **SDK_EXPLORATION.md** (45-60 min complete study)
3. Reference: **QUICK_REFERENCE.md** (ongoing lookups)

#### "I need to understand execution flow"
1. **QUICK_REFERENCE.md** - Function Execution Flow diagram
2. **SDK_EXPLORATION.md** Section 8 (Advanced Tool Patterns)
3. **SDK_EXPLORATION.md** Section 4.1 (FunctionCall Class)

#### "I need reference material"
- **QUICK_REFERENCE.md** - All diagrams and cheat sheets
- **SDK_EXPLORATION.md** Section 13 - Files reference table
- **EXPLORATION_SUMMARY.md** Section 12 - Files reference

#### "I'm integrating new capabilities"
1. **EXPLORATION_SUMMARY.md** - Key findings (3 approaches)
2. **QUICK_REFERENCE.md** - Architecture overview
3. **SDK_EXPLORATION.md** - Full system details
4. **EXPLORATION_SUMMARY.md** Section 9 - Checklist

---

## 🎯 Key Findings Summary

### The Agentica Tool System: 4 Core Components

1. **Function Class** (664 lines in base.py)
   - 25+ configurable properties
   - Type-safe JSON Schema generation
   - Metadata-driven behavior
   - Weak reference to agent (avoid circular refs)

2. **FunctionCall Class** (Lines 319-443 in base.py)
   - Execution pipeline with hooks
   - Input validation before execution
   - Special parameters (agent, fc)
   - Auto-detection of sync/async

3. **Tool Container** (Lines 604-663 in base.py)
   - Groups related functions
   - Provides system prompt support
   - Manages function registration
   - Override `get_system_prompt()` for custom guidance

4. **Global Registry** (registry.py)
   - Dynamic tool discovery
   - Named tool lookup
   - Plugin system support

### Import Strategy: Two-Tier System

**EAGER IMPORTS** (Fast)
- Tool, Function, FunctionCall
- @tool decorator
- Agent, Workflow
- Core types

**LAZY IMPORTS** (On-Demand)
- 70+ tool implementations
- Database backends
- Model providers
- Heavy dependencies
- Thread-safe caching

### Three Ways to Define Tools

1. **@tool Decorator** - Single functions, minimal setup
2. **Tool Class** - Multiple functions, state sharing
3. **Global Registry** - Dynamic discovery, plugins

### Execution Pipeline

```
Validate Input → Pre-Hook → Call Function → Post-Hook → Return
```

### Function Properties: The Control Layer (15+ properties)

**Safety & Concurrency**
- `concurrency_safe` - Allow parallel with other read-only tools
- `is_read_only` - Never modifies state
- `is_destructive` - Irreversible operations

**Execution Control**
- `show_result` - Display to user
- `stop_after_tool_call` - Halt agent
- `timeout` - Execution timeout

**Validation & Hooks**
- `validate_input` - Pre-execution validation
- `pre_hook` - Before execution
- `post_hook` - After execution
- `available_when` - Dynamic availability

**Discovery & Availability**
- `deferred` - Lazy-load discovery
- `interrupt_behavior` - "cancel" or "block"

**Result Management**
- `max_result_size_chars` - Persist to disk

---

## 📊 Documentation Statistics

| Document | Size | Lines | Focus |
|----------|------|-------|-------|
| EXPLORATION_SUMMARY.md | 11 KB | ~350 | Executive overview |
| SDK_EXPLORATION.md | 25 KB | ~650 | Technical deep-dive |
| QUICK_REFERENCE.md | 23 KB | ASCII | Visual + diagrams |
| **TOTAL** | **~59 KB** | **~1000** | Complete coverage |

---

## 🔧 Integration Checklist

To add new tools to the SDK:

- [ ] Create tool file in `agentica/tools/my_tool.py`
- [ ] Define class extending `Tool` or use `@tool` decorator
- [ ] Register functions with `self.register(func, metadata...)`
- [ ] Add type hints for JSON Schema generation
- [ ] Mark safety properties (read_only, destructive, concurrency_safe)
- [ ] Support async/sync (async preferred)
- [ ] Add to exports in `agentica/tools/__init__.py`
- [ ] Add to `_LAZY_IMPORTS` if heavy dependency
- [ ] Test with Agent
- [ ] Update `agentica/version.py` if new capability

---

## 📚 Cross-References

### Files Explained
- `agentica/__init__.py` - Eager & lazy import orchestration
- `agentica/version.py` - Simple version constant
- `agentica/config.py` - Global configuration with env var support
- `agentica/tools/base.py` - Core classes (Function, FunctionCall, Tool)
- `agentica/tools/decorators.py` - @tool decorator implementation
- `agentica/tools/registry.py` - Global tool lookup
- `agentica/tools/buildin_tools.py` - 7 built-in tools
- `agentica/tools/weather_tool.py` - Example implementation
- `agentica/tools/` - 48 total tool files

### Example Tools Referenced
- **WeatherTool** - Async, fallbacks, env vars, markdown output
- **BuiltinFileTool** - Concurrency control, safety classification
- **BuiltinExecuteTool** - Shell command execution
- **CodeTool** - Code analysis and operations
- **ShellTool** - Advanced shell operations

---

## ✅ Best Practices

### Concurrency & Performance
- ✅ Mark read-only ops with `concurrency_safe=True`
- ✅ Serialize write operations (default)
- ✅ Use async/await for I/O

### Safety & Security
- ✅ Validate inputs via `validate_input`
- ✅ Restrict file access with sandbox
- ✅ Mark destructive ops explicitly

### Type Safety
- ✅ Include type hints for all parameters
- ✅ Provide return type hints
- ✅ Use `Optional` for nullable params

### Documentation
- ✅ Clear docstrings → LLM descriptions
- ✅ Use markdown in descriptions
- ✅ Document parameters and returns

### Configuration
- ✅ Use environment variables
- ✅ Support fallback mechanisms
- ✅ Document requirements

---

## 🎓 Learning Path

**For Beginners:**
1. EXPLORATION_SUMMARY.md (Section 1-3)
2. QUICK_REFERENCE.md (Architecture Overview)
3. EXPLORATION_SUMMARY.md (Integration Checklist)

**For Intermediate Developers:**
1. SDK_EXPLORATION.md (Sections 1-6)
2. QUICK_REFERENCE.md (All diagrams)
3. SDK_EXPLORATION.md (Sections 7-11)

**For Advanced Integration:**
1. All documents cover different aspects
2. Focus on patterns section (SDK_EXPLORATION.md #8)
3. Reference files section for implementation details

**For Tool Development:**
1. EXPLORATION_SUMMARY.md (Section 3)
2. SDK_EXPLORATION.md (Section 4.3 - WeatherTool)
3. Follow Integration Checklist (EXPLORATION_SUMMARY.md #9)

---

## 🚀 Getting Started with New Tools

### Minimal Example: @tool Decorator
```python
from agentica.tools.decorators import tool

@tool(concurrency_safe=True, is_read_only=True)
def my_read_operation(query: str) -> str:
    """Do something read-only."""
    return result
```

### Medium Example: Tool Class
```python
from agentica.tools.base import Tool

class MyTool(Tool):
    def __init__(self):
        super().__init__(name="my_tool")
        self.register(self.my_func)
    
    async def my_func(self, param: str) -> str:
        """Function with documentation."""
        return result
```

### Complex Example: With All Features
See **SDK_EXPLORATION.md** Section 4.3 (WeatherTool)
- Async execution
- Multiple fallbacks
- Env var configuration
- Error handling
- Markdown output

---

## 📞 Questions & Answers

**Q: Where should I start?**
A: Read **EXPLORATION_SUMMARY.md** for a 10-minute overview.

**Q: How do I add a new tool?**
A: See **EXPLORATION_SUMMARY.md** Section 3 or follow the checklist in Section 9.

**Q: What are the key properties?**
A: See **QUICK_REFERENCE.md** - Function Properties Cheat Sheet.

**Q: What's the execution flow?**
A: See **QUICK_REFERENCE.md** - Function Execution Flow diagram.

**Q: Where are the examples?**
A: **SDK_EXPLORATION.md** Section 4.3 (WeatherTool) is the primary example.

**Q: How does concurrency work?**
A: See **SDK_EXPLORATION.md** Section 6 (Tool Metadata & Properties).

**Q: What about lazy imports?**
A: See **QUICK_REFERENCE.md** - Lazy Import Pattern diagram.

---

## 📝 Document Generation Info

**Generated:** April 14, 2026  
**Tool System Version:** 1.3.5  
**Files Analyzed:** 48 tool files, 14 key architecture files  
**Lines of Documentation:** ~1000 lines  
**Diagrams:** 11 ASCII architecture diagrams  
**Total Coverage:** Complete tool system architecture

---

## 🔗 Related Documentation

In the same directory:
- **GATEWAY_EXPLORATION.md** - Infrastructure integration
- **CLAUDE.md** - Claude-specific integrations
- **AGENTS.md** - Agent system overview
- **CODEBUDDY.md** - Related tooling
- **README.md** - Project overview

---

## ✨ Summary

These three documents provide **complete coverage** of the Agentica SDK tool system:

1. **EXPLORATION_SUMMARY.md** - Quick, executive-level understanding (11 KB)
2. **SDK_EXPLORATION.md** - Complete technical reference (25 KB)
3. **QUICK_REFERENCE.md** - Visual diagrams and cheat sheets (23 KB)

Together, they enable:
- ✅ Understanding the architecture
- ✅ Implementing new tools
- ✅ Extending the framework
- ✅ Following best practices
- ✅ Quick reference lookup

**Start with EXPLORATION_SUMMARY.md, then drill into SDK_EXPLORATION.md as needed.**

---

Generated by AI Analysis System for Agentica SDK Development Team
