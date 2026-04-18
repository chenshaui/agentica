# -*- coding: utf-8 -*-
"""
Embedding providers for RAG.

Base class (Embedding) is dependency-free.
Specific providers (OpenAIEmbedding, OllamaEmbedding, ...) require [rag] extras:
    pip install agentica[rag]
"""
from .base import Embedding
