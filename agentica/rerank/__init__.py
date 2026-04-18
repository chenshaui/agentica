# -*- coding: utf-8 -*-
"""
@author: XuMing(xuming624@qq.com)
@description: Rerank module for document reranking.

Base class (Rerank) is dependency-free.
Specific providers (JinaRerank, ZhipuAIRerank, ...) may require additional
dependencies, but most use plain httpx (already in core).
"""
from agentica.rerank.base import Rerank

__all__ = ["Rerank"]
