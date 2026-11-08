"""多城市行程规划 Agent。

设计原则：LLM 只做选择，事实由工具产出，行程由代码校验。

当前阶段：图结构与数据契约已完成，5 个 scout 仍返回 MOCK 数据。
后续按 docs/architecture.md 第 10 节扩展（真实数据 → RAG → 评估 → 服务化）。
"""

__version__ = "0.1.0"
