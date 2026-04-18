#!/usr/bin/env bash
# Migrate Python code from v1.3.5 top-level imports to v1.3.6+ recommended paths.
#
# Usage:
#   bash scripts/migrate-v1.3.6.sh [target_dir]   # default: current dir
#
# What it does:
#   Rewrites `from agentica import X` for symbols that v1.3.6 deprecates
#   into their new sub-module paths. Safe: only touches .py files.
#
# Requirements:
#   - GNU sed or macOS sed (BSD)
#   - Python 3.x

set -euo pipefail

TARGET="${1:-.}"

if [[ "$(uname)" == "Darwin" ]]; then
    SED_INPLACE=(sed -i '')
else
    SED_INPLACE=(sed -i)
fi

echo "Migrating agentica v1.3.5 → v1.3.6 import paths in: $TARGET"
echo "Scanning .py files..."

# Map: old_symbol → new_module_path
declare -A MIGRATIONS=(
    # RAG
    ["Knowledge"]="agentica.knowledge"
    ["LangChainKnowledge"]="agentica.knowledge"
    ["LlamaIndexKnowledge"]="agentica.knowledge"
    # VectorDB
    ["VectorDb"]="agentica.vectordb"
    ["Distance"]="agentica.vectordb"
    ["SearchType"]="agentica.vectordb"
    ["InMemoryVectorDb"]="agentica.vectordb"
    # Embedding (base)
    ["Embedding"]="agentica.embedding"
    ["OpenAIEmbedding"]="agentica.embedding.openai"
    ["AzureOpenAIEmbedding"]="agentica.embedding.azure_openai"
    ["OllamaEmbedding"]="agentica.embedding.ollama"
    ["TogetherEmbedding"]="agentica.embedding.together"
    ["FireworksEmbedding"]="agentica.embedding.fireworks"
    ["ZhipuAIEmbedding"]="agentica.embedding.zhipuai"
    ["JinaEmbedding"]="agentica.embedding.jina"
    ["GeminiEmbedding"]="agentica.embedding.gemini"
    ["HuggingfaceEmbedding"]="agentica.embedding.huggingface"
    ["MulanAIEmbedding"]="agentica.embedding.mulanai"
    ["HashEmbedding"]="agentica.embedding.hash"
    ["HttpEmbedding"]="agentica.embedding.http"
    # Rerank
    ["Rerank"]="agentica.rerank"
    ["JinaRerank"]="agentica.rerank.jina"
    ["ZhipuAIRerank"]="agentica.rerank.zhipuai"
    # DB
    ["SqliteDb"]="agentica.db"
    ["PostgresDb"]="agentica.db"
    ["MysqlDb"]="agentica.db"
    ["RedisDb"]="agentica.db"
    ["InMemoryDb"]="agentica.db"
    ["JsonDb"]="agentica.db"
    # Model providers
    ["Claude"]="agentica.model.anthropic.claude"
    ["Ollama"]="agentica.model.ollama.chat"
    ["LiteLLMChat"]="agentica.model.litellm.chat"
    ["KimiChat"]="agentica.model.kimi.chat"
    # Protocols
    ["MCPConfig"]="agentica.mcp"
    # Tier 3 experimental
    ["Swarm"]="agentica.swarm"
    ["SwarmResult"]="agentica.swarm"
    ["SubagentType"]="agentica.subagent"
    ["SubagentConfig"]="agentica.subagent"
    ["SubagentRun"]="agentica.subagent"
    ["SubagentRegistry"]="agentica.subagent"
)

# Apply migrations
changed_files=0
for symbol in "${!MIGRATIONS[@]}"; do
    new_module="${MIGRATIONS[$symbol]}"
    # Match: `from agentica import X` or `from agentica import ..., X, ...`
    # Safe replacement: only the exact symbol X, preserving line structure.
    # Pattern: `from agentica import ... X ...`  →  new line inserting
    #
    # Simplified: rewrite lines matching exactly `from agentica import X`
    pattern="from agentica import $symbol\b"
    replacement="from $new_module import $symbol"

    # Find files with a matching exact import
    files=$(grep -rl -E "^from agentica import $symbol\$" "$TARGET" --include="*.py" 2>/dev/null || true)
    if [[ -n "$files" ]]; then
        for f in $files; do
            "${SED_INPLACE[@]}" "s|^from agentica import $symbol$|from $new_module import $symbol|g" "$f"
            ((changed_files++)) || true
            echo "  Migrated: $f  ($symbol → $new_module)"
        done
    fi
done

echo ""
echo "Done. $changed_files file(s) updated."
echo ""
echo "Note: Multi-symbol imports like 'from agentica import A, B, Claude' are not"
echo "automatically split. Search for such lines manually and split."
echo ""
echo "To find remaining deprecations:"
echo "  python -W error::DeprecationWarning -m pytest your_tests/"
