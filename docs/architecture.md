# 架构说明

## 1. 设计原则

1. **LLM 不搬运事实，只做选择。**
   事实（景点、坐标、门票、时刻）由工具与检索产出；LLM 只在候选集里挑选与排序。

2. **确定性的事交给代码，创造性的事交给 LLM。**
   日期补齐、时间轴、算钱、校验、排序、渲染 → 代码；选点、取舍、写理由 → LLM。

3. **每一层都能降级。**
   任一数据源失败都不阻塞整体，问题进入 `errors` → `warnings`，最终由 `status` 表达。

---

## 2. 两层图

```
父图（PlanningState）
START → orchestrator ──Send × N──► region_worker（子图）× N ──► synthesizer → END

子图（WorkerState）
START → prepare ─┬─ scout_leg ────┐
                 ├─ scout_poi ────┤
                 ├─ scout_hotel ──┼─► merge → plan → validate ─┬─► finalize → END
                 ├─ scout_weather ┤                            └─► plan（回炉，最多 2 次）
                 └─ scout_guide ──┘
```

**父图并行是动态的**（城市数量运行时才知道），用 `Send`。
**子图并行是静态的**（永远是这 5 个 scout），用普通边即可。

---

## 3. State 设计

### 父图 `PlanningState`

| 字段 | reducer | 说明 |
|---|---|---|
| `regions: list[RegionDraft]` | 默认（覆盖） | orchestrator 写一次 |
| `region_plans: list[LocalPlan]` | `operator.add` | N 个 worker 并发写同一 channel，必须归约 |
| `current_plan: str` | 默认 | synthesizer 写 |
| `messages` | `add_messages` | 继承自 `MessagesState` |

> 并发写同一 channel 时若无 reducer，LangGraph 会抛 `InvalidUpdateError`。

### 子图 `WorkerState`

每个 scout **独占自己的 key**（`pois` / `hotels` / `weather` / `guides` / `inbound` / `outbound`），
因此天然无写冲突，只有 `errors` 使用 `operator.add`。

> 并行图设计的省事技巧：**按输出划分 key**，而不是让所有节点往同一个列表里塞。

---

## 4. 数据契约（三层）

| 层 | 模型 | 谁产出 | 特点 |
|---|---|---|---|
| 事实层 | `TransportLeg` `POICandidate` `HotelCandidate` `WeatherDay` `GuideNote` | scout | 带 `source` / `confidence`，允许为空 |
| 规划层 | `DraftItem` `DraftDay` `PlannerDraft` | LLM | 只含 `ref` 引用，不含事实 |
| 输出层 | `ItineraryItem` `DayPlan` `HotelPick` `BudgetBreakdown` `LocalPlan` | 代码 | 最终渲染契约 |

**为什么要分三层**：事实可被工具替换、可被校验；规划只表达"选择"；输出保证结构稳定。
`synthesizer` 只依赖输出层，因此换数据源、换模型都不影响渲染。

---

## 5. 节点职责

| 节点 | 类型 | 输入 | 输出 | 失败时 |
|---|---|---|---|---|
| `prepare` | 代码 | `region` | `dates` / `budget_limit` | — |
| `scout_leg` | 代码 + API | 前后城市、交通方式 | `inbound` / `outbound` | 空值 + 错误 |
| `scout_poi` | 代码 + API | 城市、兴趣 | `list[POICandidate]` | 空列表 + 错误 |
| `scout_hotel` | 代码 + API | 城市、预算 | `list[HotelCandidate]` | 空列表 |
| `scout_weather` | 代码 + API | 日期列表 | `list[WeatherDay]` | 空列表 |
| `scout_guide` | RAG（规划中） | 城市 | `list[GuideNote]` | 空列表 |
| `merge` | 代码 | 所有事实 | `index: id → 摘要` | — |
| `plan` | **LLM** | 候选索引 + 约束 | `PlannerDraft` | 代码兜底行程 |
| `validate` | 代码 | `draft` | `ok` + `errors` | 回炉一次 |
| `finalize` | 代码 | `draft` + 事实 | `LocalPlan` | — |

---

## 6. 防幻觉机制（核心）

```
merge 造候选表  ──►  plan 只能输出 ref: "poi_03"  ──►  validate 检查 ref ∈ index
                                                          │
                                                    finalize 用 ref 反查真实对象
```

1. **封闭世界**：LLM 只看到 `index`（id + 一行摘要），没有"表外"空间。
2. **引用而非复述**：模型写不了景点名，名字由代码从 id 查出来。
3. **代码强制**：prompt 只是"请求"，`validate` 才是"强制"。

校验项：

- 日期覆盖完整、不重不漏
- `sight` 的 `ref` 必须存在，且全程不重复
- 当天条目时间不重叠（按 `duration_min` 推进）
- Day 1 不早于到达时刻；最后一天要赶得上离开
- `hotel_ref` 合法、预算不超支

---

## 7. 降级与状态

| 情况 | status |
|---|---|
| 有行程、无硬错误 | `ok` |
| 校验没过 / 没选到酒店 / 超预算 / 用了兜底 | `partial` |
| 行程为空 | `failed` |

降级信息全部写进 `warnings`，由 `synthesizer` 原样展示——**问题必须可见，不能假装成功**。

MOCK 数据只进 `warnings`，不降低 `status`：那是"数据是假的"，不是"规划失败了"。

---

## 8. 关键取舍

**为什么不用 agent-as-tool？**
取数是参数固定的确定性操作。ReAct 循环会让执行顺序随机、串行、可能漏调，还得多花 token。
本项目只在 `plan` 一处使用 LLM。

**为什么子图用包装层而不是直接 `add_node(subgraph)`？**
父子 state schema 差异大（`region_plans` vs `local_plan`），包一层让边界清晰，还能在边界做兜底。

**为什么校验用代码而不是让 LLM 自检？**
确定性、零成本、错误信息精确到"2026-11-23 09:00 早于到达时间 11:30"，回炉时模型知道改哪。

**为什么 `passengers` / `interests` / `transport_mode` 由代码回填？**
它们是全局上下文，不是每段要决策的东西。N 个并行分支必须从同一份权威数据读，否则会出现"太原 2 人、大同 3 人"。

---

## 9. 执行时序

```
superstep 1: prepare
superstep 2: scout_leg | scout_poi | scout_hotel | scout_weather | scout_guide   （并行）
superstep 3: merge
superstep 4: plan
superstep 5: validate
superstep 6: finalize  或  plan（回到 4）
```

---

## 10. 扩展点

- `scout_guide` → 接入 RAG（城市攻略库）
- `enrich`（新增）→ 对 top-N 景点检索深度介绍，填进 `ItineraryItem.note`
- `scout_poi` → 坐标聚类，让每天的景点落在同一片区
- `plan` → 检索相似行程作为 few-shot
- 加 checkpointer + `interrupt`：用户说"第 3 天太赶"后局部重排
