# -*- coding: utf-8 -*-
"""
@author: XuMing(xuming624@qq.com)
@description: Embedding providers for RAG.

Base class (Embedding) is dependency-free.
Specific providers (OpenAIEmbedding, OllamaEmbedding, ...) require [rag] extras:
    pip install agentica[rag]
"""
from .base import Embedding
