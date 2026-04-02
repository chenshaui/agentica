# Knowledge (RAG)

RAG (Retrieval-Augmented Generation) 让 Agent 基于你的私有文档回答问题，而非仅依赖模型的训练知识。

## 基本流程

```
用户提问 -> 向量检索 -> 提取相关文档 -> 注入上下文 -> LLM 生成回答
```

## 快速开始

### 1. 创建知识库

```python
from agentica import Knowledge
from agentica.vectordb import LanceDb
from agentica.emb import OpenAIEmb

knowledge = Knowledge(
    data_path="./documents",          # 文档目录或文件
    vector_db=LanceDb(
        table_name="my_docs",
        uri="./lancedb",
    ),
    chunk_size=1000,                  # 分块大小
    num_documents=5,                  # 检索返回文档数
)

# 加载文档到向量库
knowledge.load(recreate=True)
```

### 2. 配置 Agent

```python
from agentica import Agent, OpenAIChat

agent = Agent(
    model=OpenAIChat(id="gpt-4o"),
    knowledge=knowledge,
    search_knowledge=True,          # Agent 可主动搜索知识库
    add_references=True,            # 响应中附加引用来源
    instructions=[
        "基于知识库内容回答问题",
        "如果知识库中没有相关信息，明确告知用户",
        "引用时注明文档来源",
    ],
)

result = await agent.run("项目的部署流程是什么?")
print(result.content)
```

## 支持的文档格式

- **文本文件**: `.txt`, `.md`
- **PDF**: `.pdf`（需要 `PyPDF2` 或 `pdfplumber`）
- **CSV**: `.csv`

## 向量数据库

### LanceDB（推荐，零依赖）

```python
from agentica.vectordb import LanceDb

db = LanceDb(table_name="documents", uri="./lancedb")
```

### ChromaDB

```python
from agentica.vectordb import ChromaDb

db = ChromaDb(collection="documents", path="./chromadb")
```

### 其他选项

```python
from agentica.vectordb import (
    InMemoryVectorDb,   # 内存（测试用）
    PgVectorDb,         # PostgreSQL + pgvector
    PineconeDb,         # Pinecone（云服务）
    QdrantDb,           # Qdrant
)
```

## Embedding 模型

```python
from agentica.emb import (
    OpenAIEmb,         # OpenAI text-embedding-3-small
    ZhipuAIEmb,        # 智谱 AI embedding
    JinaEmb,           # Jina Embeddings
    OllamaEmb,         # 本地 Ollama
    HuggingfaceEmb,    # HuggingFace 模型
)
```

## Agentic RAG

Agent 自主决定何时搜索知识库：

```python
agent = Agent(
    knowledge=knowledge,
    search_knowledge=True,   # Agent 自主判断是否需要搜索
)
```

模型会将 `search_knowledge_base` 作为工具调用，自主决定搜索时机和查询词。

## 混合检索与重排序

```python
from agentica.vectordb import LanceDb
from agentica.rerank import JinaReranker

db = LanceDb(
    table_name="docs",
    uri="./lancedb",
    search_type="hybrid",           # 向量 + 关键词
    reranker=JinaReranker(),        # 重排序
)
```

## 最佳实践

| 实践 | 说明 |
|------|------|
| **适当的分块大小** | 1000-2000 字符通常效果最佳 |
| **检索数量** | 3-5 个文档，过多会稀释相关性 |
| **清晰的指令** | 告诉 Agent 何时和如何使用知识库 |
| **定期更新** | 文档变更后重新加载（`upsert=True`） |
| **混合检索** | 关键词 + 向量检索互补效果好 |
| **重排序** | 检索后重排序显著提升准确率 |

## 下一步

- [Agent 核心概念](agent.md) -- Agent 如何集成知识库
- [工具系统](tools.md) -- 自定义检索工具
- [API 参考](../api/agent.md) -- Knowledge API
