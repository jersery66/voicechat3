# RelaxationCatalog 与 Content Model

状态：**FUTURE DESIGN / Phase 1 实现范围**

## Schema

```yaml
id: bubble_pop
display_name: 泡泡
category: EXERCISE | VIDEO | GAME
enabled: true
recommended_duration_seconds: 120
max_duration_seconds: 300
requires_mouse: true
requires_audio: false
requires_video: false
resource_path: null
implementation_type: local_deterministic | existing_video | planned
sort_order: 10
implementation_status: PLANNED | AVAILABLE | DISABLED
```

Catalog 只负责 list、lookup、enabled filtering 和 schema validation，不负责
business decision、recommendation、start、UI 或播放。

必须拒绝 duplicate id、unknown category、空 display name、负 duration、
`max_duration < recommended_duration` 和错误 resource metadata。资源不存在时
使用 `implementation_status=PLANNED`，不能假装已实现。

## Phase 1 entries

```text
EXERCISE: breathing, muscle_relaxation, meditation, grounding(planned)
VIDEO: current relaxation video resources (metadata only)
GAME: bubble_pop, gentle_search, calm_puzzle, falling_leaves (planned)
```

Catalog 不包含 `clinical_score`、`treatment_effect`、`addiction_score`、
`relapse_score`、`attention_score` 或 `willpower_score`。新增内容只添加 catalog
entry 和未来 adapter，不新增一个 RouterAction，也不在 Pipeline 写大量条件。
