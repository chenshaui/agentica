# -*- coding: utf-8 -*-
"""
Knowledge module: RAG knowledge base orchestration.

Base class (Knowledge) is dependency-free.
Integrations (LangChainKnowledge, LlamaIndexKnowledge) require their own libs.

For a complete RAG stack with vector DB:
    pip install agentica[rag]                # basic RAG
    pip install agentica[qdrant]             # + Qdrant
    pip install agentica[chroma]             # + ChromaDB
    pip install agentica[lancedb]            # + LanceDB
    pip install agentica[pgvector]           # + pgvector
"""
from .base import Knowledge
from .langchain_knowledge import LangChainKnowledge
from .llamaindex_knowledge import LlamaIndexKnowledge
