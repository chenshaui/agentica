# -*- coding: utf-8 -*-
"""
Vector databases for RAG.

Base classes (VectorDb, Distance) are dependency-free.
Specific backends require their own extras:
    pip install agentica[rag]           # basic + InMemoryVectorDb
    pip install agentica[qdrant]        # + Qdrant
    pip install agentica[chroma]        # + ChromaDB
    pip install agentica[lancedb]       # + LanceDB
    pip install agentica[pgvector]      # + pgvector
"""
from .base import VectorDb, Distance
