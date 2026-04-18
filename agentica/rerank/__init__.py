# -*- coding: utf-8 -*-
"""
Rerank module for document reranking.

Base class (Rerank) is dependency-free.
Specific providers (JinaRerank, ZhipuAIRerank, ...) may require additional
dependencies, but most use plain httpx (already in core).
"""
from agentica.rerank.base import Rerank

__all__ = ["Rerank"]
