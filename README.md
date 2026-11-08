# 多城市行程规划 Agent

> **可验证的行程规划系统**：LLM 只做选择，事实由工具产出，行程由代码校验。

输入出发地、目的地、日期、预算和兴趣，输出一份逐日可执行的旅行攻略：
到达方式、景点顺序、每天的时间轴、住宿、预算拆分、实用攻略。

不是"会聊天的旅行助手"，而是**输出能被代码校验的行程系统**——这是本项目和其他旅行 Agent  demo 最大的区别。

---

## 为什么值得做

让 LLM 直接写攻略，会遇到三个绕不过去的问题：

1. **它会编造**——不存在的景点、错误的门票价、瞎写的班次。
2. **它算不对**——日期不连续、两段行程时间重叠、预算加不平。
3. **它不可验证**——输出是一段文字，你没法用代码判断对错。

本项目的解法是把"事实"和"判断"拆开：

| 谁 | 负责什么 | 为什么 |
|---|---|---|
| 工具（高德 / RAG） | 事实：景点、坐标、路线、天气、攻略 | 可查证、可替换、有来源 |
| LLM | 判断：选哪些点、什么顺序 | 这是它真正擅长的事 |
| 代码 | 校验、算时间、算钱、渲染 | 确定性的事不该交给模型 |

**核心机制**：LLM 输出的不是景点名，而是候选集的 `id`（如 `poi_03`）。
`validate` 节点检查 `id` 是否存在于候选池——**编造在结构上不可能发生**，而不是靠 prompt 恳求模型。

---

## 架构

```
父图（PlanningState）
┌──────────────┐   Send × N   ┌───────────────┐   region_plans   ┌────────────┐
│ orchestrator │ ───────────► │ region_worker │ ───────────────► │ synthesizer│
│  拆城市段     │   并行 N 个   │   （子图）     │   归约合并        │  渲染攻略   │
└──────────────┘              └───────────────┘                  └────────────┘

子图（WorkerState，只处理一个城市）
prepare ─┬─ scout_leg ────┐
         ├─ scout_poi ────┤
         ├─ scout_hotel ──┼─► merge ─► plan ─► validate ─┬─► finalize ─► END
         ├─ scout_weather ┤                              └─► plan（回炉 1 次）
         └─ scout_guide ──┘
```

- **父图**：按城市数量动态扇出（`Send`），N 个城市并行规划。
- **子图**：单城市内 5 路数据并行采集 → 代码合并 → LLM 选点 → 代码校验 → 代码展开。
- **降级**：任一数据源失败都不阻塞，最终 `status` 为 `ok / partial / failed`，问题写进 `warnings`。

详见 [`docs/architecture.md`](docs/architecture.md)。

---

## 当前状态

| 模块 | 状态 |
|---|---|
| LangGraph 编排（并行 / 子图 / 条件边 / reducer） | ✅ 完成 |
| 数据契约（Pydantic v2 模型 + 字段注释） | ✅ 完成 |
| 防幻觉机制（候选 id + 白名单校验） | ✅ 完成 |
| 校验与降级（时间/预算/日期/状态） | ✅ 完成 |
| 高德真实数据接入 | 🚧 进行中（当前为 MOCK） |
| RAG（混合检索 + 重排） | ⬜ 计划中 |
| 评估（ragas + 约束通过率） | ⬜ 计划中 |
| 服务化（FastAPI / 流式） | ⬜ 计划中 |

> 当前 5 个 scout 返回的是 MOCK 数据，用于验证图结构与校验逻辑。
> 真实数据接入见 Roadmap 第一阶段。

---

## 快速开始

```bash
cd travel-agent
uv sync

# 配置环境变量（参考 .env.example）
cp .env.example .env

jupyter lab notebooks/travel_planner.ipynb
```

运行顺序：从上到下执行 notebook 的 cell。
- 单城市冒烟测试：直接构造 `RegionDraft` 调 `region_worker`
- 完整流程：`travel_graph.astream(initial_state)`

> 首次运行需要 `DEEPSEEK_API_KEY`；接入真实数据后还需要 `AMAP_MAPS_API_KEY`。

---

## Roadmap

- [ ] **W1 · 真实化**：接入高德 POI / 路线 / 天气，移除 MOCK，完善失败降级
- [ ] **W2 · RAG**：城市攻略 + 景点详情语料，BM25 + 向量混合检索 + rerank，接入 `scout_guide`
- [ ] **W3 · 评估**：ragas 测检索与生成质量，规划约束通过率，输出 before/after
- [ ] **W4 · 工程化**：抽取为 Python 包、FastAPI 接口、checkpoint 多轮修改、LangSmith 追踪

---

## 技术栈

- **编排**：LangGraph（`Send` 并行扇出、子图、条件边重试、自定义 reducer）
- **模型**：DeepSeek（结构化输出）
- **数据**：高德地图 Web API / MCP
- **待接入**：Milvus/Chroma（向量）、bm25s + pkuseg（中文 BM25）、sentence-transformers（rerank）、ragas（评估）

---

## 已知限制

- 门票价格、开放时间、预约政策无法从高德获取，缺失时标注"以景区公示为准"，不做编造。
- 城际交通为"方案级"信息（耗时/换乘），具体班次与票价以 12306 为准。
- 当前天气仅覆盖预报窗口（约 4 天），超出部分回落到气候常年值。
