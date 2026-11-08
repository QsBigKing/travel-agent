# data

数据目录（**不要提交大文件**）。

- `corpus/`：RAG 语料，按城市分目录，建议 markdown
  ```
  corpus/太原/山西博物院.md
  corpus/太原/交通与预约.md
  ```
  每篇建议带 front-matter：
  ```yaml
  ---
  city: 太原
  spot: 山西博物院
  doc_type: guide      # guide / policy / spot
  source: 官方公众号
  updated_at: 2026-10-01
  ---
  ```

- `curated/`：人工维护的热门景点表（门票、开放时间、是否需预约），字段 `updated_at` 必填
- `samples/`：接口返回样本，用于离线调试与字段映射
