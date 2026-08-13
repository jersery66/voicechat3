# VoiceChat3 Phase 1 Crisis Runtime Detachment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变 A100/vLLM、STT、TTS、量表、放松、游戏、正常结束和报告主流程的前提下，让生产入口无法再到达危机识别、SafetyGate、Guard、CrisisDialog、`show_crisis`、`END_SAFETY` 或危机专用 RAG。

**Architecture:** 生产路径固定为 `main.py -> MainWindow -> ConversationCoordinator -> ConversationPipeline`；Coordinator 只负责语音转写复用、调用普通 Pipeline 和记录普通 policy/turn 事件。危机相关实现保留在 `safety/` 作为离线或历史代码，`EndType.SAFETY` 只保留为历史数据枚举；生产模块不再导入、构造、调用或生成任何 safety/crisis 控制对象与事件。

**Tech Stack:** Python 3.12、pytest 9、PySide6、OpenAI-compatible vLLM、PowerShell、AST 静态依赖检查。

---

## Locked file structure

**Create**

- `docs/refactor/phase1_crisis_detachment_implementation.md` — 记录起点 SHA、基线测试、依赖清单、验收结果和最终 SHA。
- `tests/test_crisis_runtime_boundary.py` — 从 `main.py` 做本地 import traversal，并检查活跃生产模块不存在危机控制符号。
- `safety/legacy_config.py` — 保存不再由生产配置导出的热线、危机 suffix 和历史 3B 危机 prompt。
- `safety/guard_client.py` — 保存 Guard 协议，供保留的离线 safety 代码使用。
- `safety/vllm_guard_client.py` — 保存不再由生产 inference 包导出的 vLLM Guard adapter。
- `safety/legacy_dialog.py` — 保存不再由生产 UI 包导入的 `CrisisDialog`。

**Modify**

- `conversation/coordinator.py` — 删除 SafetyGate 边界和 `assess_transcript()`，语音/文字直接交给普通 Pipeline。
- `services/pipeline.py` — 删除危机字段、危机锁、关键词判断、语义复评、`show_crisis` 和 `END_SAFETY` 路径；并发分类只保留 intent/emotion。
- `core/scale_fsm.py` — 删除 `crisis_lock_turns` 状态。
- `core/tags.py` — 删除 `END_SAFETY` 可执行映射。
- `services/agent_service.py` — 删除危机意图、危机评估方法和 Router 风险字段。
- `adapters/protocols.py` — 删除 AgentBackend 的危机方法要求。
- `ui/main_window.py`、`ui/__init__.py`、`ui/dialogs.py` — 删除生产 UI 的危机 imports、构造、队列事件和结束分支；保留普通 dialog。
- `config.py` — 生产 prompt、Agent prompt、报告 prompt 和运行配置不再导出危机/Guard/热线控制项。
- `services/rag_service.py`、`knowledge_base/knowledge.json` — 移除危机专用核心条目和默认回退条目，不改检索算法。
- `services/report_service.py`、`services/tools/report_tool.py` — 新运行不再生成危机资源或 crisis key event；历史报告 reader 保持兼容。
- `app/contracts.py`、`app/engine.py`、`core/session_fsm.py` — 删除可执行 crisis event 和 SAFETY 特殊分支；历史枚举仍在 `core/types.py`。
- `inference/__init__.py`、`inference/factory.py` — 生产 inference 包只暴露普通 dialogue client；Guard 实现迁入 `safety/`。
- `deployment/profiles.py`、`deployment/__init__.py`、`scripts/check_config.py`、`scripts/start_a100_vllm_stack.ps1` — 删除 Guard 配置面，严格保留 A100 的 8000/8001 两服务契约。
- `README.md`、`docs/refactor/01_feature_inventory.md` — 文档改成“危机链路暂时脱离生产，源代码留存”，不宣称当前运行时仍有危机保护。

**Delete**

- `inference/guard_client.py`、`inference/vllm_guard_client.py` — 内容迁入 `safety/` 后删除原位置，阻断生产 inference package 的可达性。
- `tests/test_crisis_risk.py` — 该文件只验证将被删除的 AgentService 危机 API；保留 `tests/test_safety_gate.py` 和改址后的 `tests/test_vllm_guard_client.py` 以验证 legacy safety 源码仍可用。

**Historical compatibility files intentionally retained**

- `core/types.py` 中的 `EndType.SAFETY`。
- `services/report_generator.py`、`ui/session_review.py`、`ui/stats_panel.py` 对旧报告中 `risk_assessment`/`crisis_count` 的只读展示。
- `safety/**` 及其离线测试。

---

### Task 1: Preflight, baseline and dependency inventory

**Files:**

- Create: `docs/refactor/phase1_crisis_detachment_implementation.md`
- Verify: repository and `tests/`

- [ ] **Step 1: Confirm the authoritative branch and clean code worktree**

Run:

```powershell
git branch --show-current
git rev-parse HEAD
git status --short
```

Expected:

```text
codex/a100-vllm-safety
2b88b99e9a0919ed91d8df0bed771687ecae4dc1
```

`git status --short` must contain no code changes. If it lists user-owned files, record them and never stage them.

- [ ] **Step 2: Run the baseline suite with the known working environment**

Run:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests -q
```

Expected: exit code `0`; record the exact passed/skipped counts. If any test fails, record the complete failing node IDs as pre-existing failures and stop before code changes.

- [ ] **Step 3: Capture the crisis dependency inventory**

Run:

```powershell
$patterns = 'SafetyGate|SafetyAction|SafetyDecision|show_crisis|CrisisDialog|assess_crisis|_keyword_crisis|crisis_lock|crisis_risk|crisis_indicators|safety_payload|END_SAFETY|EndType.SAFETY|CRISIS_INTERVENTION_SUFFIX|AGENT_CRISIS_SYSTEM_MESSAGE|CRISIS_HOTLINES|build_safety_gate|build_guard_client|GUARD_MODEL|from safety|import safety'
git grep -n -E $patterns -- ':!docs/**'
```

Expected: hits in Coordinator, Pipeline, AgentService, MainWindow, config, inference, app/core compatibility code and tests. Paste the command output under an `Initial dependency inventory` heading in the implementation record.

- [ ] **Step 4: Add the implementation record**

Create the file with these exact headings and fill them from Steps 1–3:

```markdown
# Phase 1 Crisis Runtime Detachment Implementation Record

## Baseline

- Branch: `codex/a100-vllm-safety`
- Starting commit: `2b88b99e9a0919ed91d8df0bed771687ecae4dc1`
- Working tree before code changes: clean
- Python: `E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe`
- Baseline pytest: record the exact pytest summary from the preflight run

## Initial dependency inventory

Paste the bounded `git grep` result from the preflight command here.

## Changed files

This section is populated after implementation from `git diff --name-status`.

## Verification

This section is populated from the final targeted, headless, full-suite and boundary checks.

## Deployment preservation

This section records the unchanged A100 model names, endpoints and launcher contract.

## Git result

This section records the final commit SHA, pushed branch and clean working tree.
```

- [ ] **Step 5: Do not commit yet**

The implementation record must travel in the single Phase 1 commit after all tests pass.

---

### Task 2: Add the production boundary tests first

**Files:**

- Create: `tests/test_crisis_runtime_boundary.py`
- Modify: `tests/test_conversation_integration.py`

- [ ] **Step 1: Write a failing import-traversal test**

Create `tests/test_crisis_runtime_boundary.py` with this import resolver and assertions:

```python
"""Architecture boundary for the temporarily detached crisis runtime."""

from __future__ import annotations

import ast
from collections import deque
from pathlib import Path

import config
from core.types import EndType
from services.pipeline import NATURAL_SCALE_QUESTIONS, PHQ9_ITEM_CORE


ROOT = Path(__file__).resolve().parents[1]


def _module_file(module: str) -> Path | None:
    parts = module.split(".")
    candidate = ROOT.joinpath(*parts).with_suffix(".py")
    if candidate.is_file():
        return candidate
    package = ROOT.joinpath(*parts, "__init__.py")
    return package if package.is_file() else None


def _local_imports(path: Path) -> set[Path]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[Path] = set()
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.append(node.module)
        for module in modules:
            target = _module_file(module)
            if target is not None:
                found.add(target.resolve())
    return found


def _reachable_from_main() -> set[Path]:
    queue = deque([(ROOT / "main.py").resolve()])
    reached: set[Path] = set()
    while queue:
        path = queue.popleft()
        if path in reached:
            continue
        reached.add(path)
        queue.extend(_local_imports(path) - reached)
    return reached


def test_main_import_graph_cannot_reach_safety_package():
    reached = _reachable_from_main()
    safety_root = (ROOT / "safety").resolve()
    offenders = sorted(path for path in reached if safety_root in path.parents)
    assert offenders == []


def test_active_runtime_modules_have_no_crisis_control_symbols():
    active_files = [
        "conversation/coordinator.py",
        "services/pipeline.py",
        "services/agent_service.py",
        "adapters/protocols.py",
        "app/contracts.py",
        "app/engine.py",
        "ui/main_window.py",
    ]
    forbidden = {
        "SafetyGate", "SafetyDecision", "show_crisis", "CrisisDialog",
        "assess_crisis_risk", "_keyword_crisis_risk", "crisis_lock_turns",
        "crisis_risk", "crisis_indicators", "safety_payload", "END_SAFETY",
        "CrisisAlertEvent",
    }
    hits = {
        relative: sorted(symbol for symbol in forbidden
                         if symbol in (ROOT / relative).read_text(encoding="utf-8"))
        for relative in active_files
    }
    assert {path: symbols for path, symbols in hits.items() if symbols} == {}


def test_production_config_no_longer_exports_crisis_runtime_controls():
    for name in (
        "CRISIS_INTERVENTION_SUFFIX",
        "AGENT_CRISIS_SYSTEM_MESSAGE",
        "CRISIS_HOTLINES",
        "GUARD_MODEL",
        "GUARD_MODEL_SERVER",
    ):
        assert not hasattr(config, name)


def test_end_safety_is_historical_but_phq9_item_nine_is_preserved():
    assert EndType.SAFETY.value == "SAFETY"
    assert ("PHQ-9", 9) in NATURAL_SCALE_QUESTIONS
    assert PHQ9_ITEM_CORE[9]["scale_item_text"]
```

- [ ] **Step 2: Change the MainWindow source contract**

Replace the old `assess_transcript` assertion in `tests/test_conversation_integration.py` with:

```python
def test_main_window_routes_turns_through_conversation_coordinator():
    source = Path("ui/main_window.py").read_text(encoding="utf-8")

    assert "coordinator.execute(config, safe_put)" in source
    assert "coordinator.assess_transcript" not in source
    assert "build_safety_gate" not in source
    assert "show_crisis" not in source
    assert "self.pipeline.execute(config, safe_put)" not in source
```

- [ ] **Step 3: Run the red tests**

Run:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/test_crisis_runtime_boundary.py tests/test_conversation_integration.py -q
```

Expected: FAIL on the current safety import graph, production config exports, active crisis symbols and old MainWindow `assess_transcript` call.

- [ ] **Step 4: Keep the failing tests uncommitted until the corresponding implementation is green**

Do not create a red-only commit.

---

### Task 3: Make ConversationCoordinator a safety-free ordinary turn adapter

**Files:**

- Modify: `conversation/coordinator.py`
- Modify: `tests/test_conversation_coordinator.py`

- [ ] **Step 1: Replace crisis-oriented coordinator tests**

Delete the three tests that expect dialogue bypass/`show_crisis` and replace them with:

```python
def test_legacy_crisis_words_flow_through_the_ordinary_text_pipeline(tmp_path):
    pipeline = FakePipeline()
    events = []
    coordinator = ConversationCoordinator(
        pipeline=pipeline,
        journal=EventJournal(tmp_path / "events.jsonl"),
    )

    result = coordinator.execute(
        PipelineConfig(user_text="我准备今晚割腕"),
        lambda *event: events.append(event),
    )

    assert len(pipeline.calls) == 1
    assert result.full_response == "ok"
    assert all(event[0] != "show_crisis" for event in events)


def test_voice_transcript_is_transcribed_once_and_always_runs_pipeline(tmp_path):
    pipeline = VoiceCapableFakePipeline("我准备今晚割腕")
    coordinator = ConversationCoordinator(
        pipeline=pipeline,
        journal=EventJournal(tmp_path / "events.jsonl"),
    )

    result = coordinator.execute(
        PipelineConfig(use_stt=True, audio_data=[1]),
        lambda *_event: None,
    )

    assert pipeline.transcribe_calls == [[1]]
    assert len(pipeline.calls) == 1
    assert pipeline.calls[0].transcribed_text == "我准备今晚割腕"
    assert result.full_response == "ok"


def test_coordinator_journal_contains_no_safety_decision(tmp_path):
    journal = EventJournal(tmp_path / "events.jsonl")
    coordinator = ConversationCoordinator(pipeline=FakePipeline(), journal=journal)

    coordinator.execute(PipelineConfig(user_text="normal input"), lambda *_event: None)

    records = [json.loads(line) for line in journal.path.read_text(encoding="utf-8").splitlines()]
    assert [record["type"] for record in records] == ["policy_decision", "turn_completed"]
```

Also update the existing normal and voice journal expectations from:

```python
["safety_decision", "policy_decision", "turn_completed"]
```

to:

```python
["policy_decision", "turn_completed"]
```

- [ ] **Step 2: Run the coordinator tests to confirm they fail**

Run:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/test_conversation_coordinator.py -q
```

Expected: FAIL because Coordinator still owns SafetyGate, bypasses Pipeline and writes `safety_decision`.

- [ ] **Step 3: Implement the minimal coordinator**

Change the constructor and turn execution to this shape; keep the existing journal/session helpers unchanged:

```python
class ConversationCoordinator:
    """Single production entry for voice and text turns."""

    def __init__(self, pipeline: LegacyPipeline, *, journal: EventJournal | None = None,
                 session_id: str | None = None):
        self._pipeline = pipeline
        self._journal = journal
        self._session_id = session_id

    def decide_turn(self, agent_route: dict | None = None) -> PolicyDecision:
        policy = PolicyDecision.from_agent_route(agent_route)
        self._record("policy_decision", policy)
        return policy

    def execute(self, config: PipelineConfig,
                emit: Callable[[str, Any], None]) -> PipelineResult:
        if config.use_stt:
            transcript = self._pipeline.transcribe(config.audio_data, emit)
            if not transcript.strip():
                self._record("turn_completed", {"input_mode": "voice", "end_type": None})
                return PipelineResult()
            config = PipelineConfig(
                use_stt=True,
                use_tts=config.use_tts,
                audio_data=config.audio_data,
                transcribed_text=transcript,
                extra_system_suffix=config.extra_system_suffix,
            )
            input_mode = "voice"
        else:
            input_mode = "text"

        result = self._pipeline.execute(config, emit)
        policy = PolicyDecision.from_agent_route(result.agent_route)
        self._record("policy_decision", policy.model_copy(update={"reason": ""}))
        self._record("turn_completed", {"input_mode": input_mode, "end_type": result.end_type})
        return result
```

Delete all `safety.*` imports, the `safety_gate` constructor argument, `assess_transcript()`, `_execute_text_turn()` and `safety_decision` recording.

- [ ] **Step 4: Run coordinator tests**

Run:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/test_conversation_coordinator.py -q
```

Expected: all tests in the file PASS.

---

### Task 4: Remove crisis classification and state from ConversationPipeline

**Files:**

- Modify: `services/pipeline.py`
- Modify: `core/scale_fsm.py`
- Modify: `core/tags.py`
- Modify: `tests/integration/fakes.py`
- Modify: `tests/integration/test_pipeline_e2e.py`
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_core_scale_fsm.py`

- [ ] **Step 1: Replace crisis E2E tests with pass-through tests**

Replace `TestCrisisGate` in `tests/integration/test_pipeline_e2e.py` with:

```python
class TestDetachedCrisisRuntime:
    def test_legacy_crisis_keyword_uses_the_ordinary_pipeline(self, ctx):
        p = ctx()
        result, emit = run(p, "我不想活了")

        assert result.spoken_text
        assert not hasattr(result, "crisis_risk")
        assert "show_crisis" not in emit.types()
        assert "危机干预" not in p.llm.calls[-1]["system_suffix"]

    def test_negative_emotion_does_not_trigger_a_third_crisis_call(self, ctx):
        agent = FakeAgent()
        p = ctx(agent=agent)

        run(p, "最近很绝望，整个人都很累")

        assert agent.intent_calls == 1
        assert agent.emotion_calls == 1
```

Add `intent_calls` and `emotion_calls` counters to `FakeAgent`, increment them in `classify_intent()` and `detect_emotion()`, and delete `crisis_keyword_result`, `crisis_llm_result`, `_keyword_crisis_risk()` and `assess_crisis_risk()` from the fake.

- [ ] **Step 2: Change tag and scale-state tests**

In `tests/test_pipeline.py`, replace the `END_SAFETY` detection test with:

```python
def test_end_safety_is_not_an_executable_end_tag(self):
    assert detect_tag("[END_SAFETY]危机", END_PATTERNS) is None
```

Replace the mixed `END_SAFETY` first-match test with:

```python
def test_removed_end_safety_does_not_mask_a_normal_end_tag(self):
    assert detect_tag("[END_SAFETY][END_GOAL_ACHIEVED]", END_PATTERNS) == "goal_achieved"
```

Delete every `crisis_lock_turns` setup/assertion from `tests/test_core_scale_fsm.py`; leave all other scale reset and delegation assertions unchanged.

- [ ] **Step 3: Run the new tests red**

Run:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/integration/test_pipeline_e2e.py tests/test_pipeline.py tests/test_core_scale_fsm.py -q
```

Expected: FAIL because PipelineResult still has crisis fields, the classifier makes a third call, crisis lock exists and `END_SAFETY` is still mapped.

- [ ] **Step 4: Remove the crisis fields and state**

In `PipelineResult`, keep:

```python
intent: str = "counseling"
emotion_result: dict = field(default_factory=dict)
agent_route: dict = field(default_factory=dict)
```

Delete `crisis_risk`, `crisis_indicators`, `safety_payload` and their migration comments.

In `ScaleState`, delete the `gates: crisis_lock_turns` documentation and assignment. Delete `_crisis_lock_turns = delegate_property(...)` from `ConversationPipeline`.

- [ ] **Step 5: Remove the pre-LLM hard crisis branch**

Delete the complete block that calls `_keyword_crisis_risk`, imports `CRISIS_INTERVENTION_SUFFIX`, clears scale state, writes `immediate_crisis` and decrements the crisis lock. Replace its final gate with:

```python
allow_new_scale = True
```

Do not change the existing scale start/continue/pause rules below it.

- [ ] **Step 6: Make post-LLM classification intent/emotion-only**

Rename `_classify_intent_emotion_crisis()` to `_classify_intent_emotion()` and use this body:

```python
def _classify_intent_emotion(self, text: str) -> tuple[str, dict]:
    """Run the ordinary intent and emotion classifiers in parallel."""
    intent_result = {"intent": "counseling", "confidence": 1.0}
    emotion_result = {"emotion": "neutral", "intensity": 0.0}
    if self.agent:
        futures = {
            self._executor.submit(self.agent.classify_intent, text): "intent",
            self._executor.submit(self.agent.detect_emotion, text): "emotion",
        }
        for future in as_completed(futures):
            tag = futures[future]
            try:
                result = future.result()
                if tag == "intent":
                    intent_result = result
                else:
                    emotion_result = result
                    self.session_emotions.append({"role": "user", **emotion_result})
            except Exception as exc:
                logger.warning(f"{tag} detection failed: {exc}")
    intent = intent_result.get("intent", "counseling")
    logger.debug(
        f"Intent: {intent} ({intent_result.get('confidence', 0):.2f}) "
        f"| Emotion: {emotion_result.get('emotion', 'neutral')} "
        f"({emotion_result.get('intensity', 0):.2f})"
    )
    return intent, emotion_result
```

Submit the renamed method and unpack only two values:

```python
agent_future = self._executor.submit(self._classify_intent_emotion, result.user_text)
result.intent, result.emotion_result = agent_future.result(timeout=10)
```

On failure, set only the existing ordinary intent/emotion fallbacks. Delete the semantic crisis reassessment, `metrics.timer("agent.crisis")`, `show_crisis` emit and crisis log fields.

- [ ] **Step 7: Remove END_SAFETY execution**

Delete `r'\[END_SAFETY\]'` from `core.tags.END_PATTERNS` and remove the `'safety': EndType.SAFETY` entry from `get_end_type_enum()`.

- [ ] **Step 8: Run the targeted Pipeline/state suite**

Run:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/integration/test_pipeline_e2e.py tests/test_pipeline.py tests/test_core_scale_fsm.py -q
```

Expected: all selected tests PASS; PHQ-9/GAD-7/PCL-5, relaxation, game and normal end cases in the E2E file remain green.

---

### Task 5: Remove crisis responsibility from AgentService and its protocol

**Files:**

- Modify: `services/agent_service.py`
- Modify: `adapters/protocols.py`
- Modify: `tests/test_adapter_conformance.py`
- Delete: `tests/test_crisis_risk.py`

- [ ] **Step 1: Tighten the protocol test**

Change the AgentBackend method list in `tests/test_adapter_conformance.py` to:

```python
for name in (
    "is_available",
    "route_conversation_actions",
    "classify_intent",
    "detect_emotion",
):
    assert hasattr(agent, name)
```

Add:

```python
assert not hasattr(agent, "assess_crisis_risk")
assert not hasattr(agent, "_keyword_crisis_risk")
```

- [ ] **Step 2: Add Router schema assertions to the architecture boundary test**

Append:

```python
def test_agent_router_schema_has_no_crisis_fields():
    source = (ROOT / "services/agent_service.py").read_text(encoding="utf-8")
    route_source = source[source.index("def route_conversation_actions"):]
    assert '"risk_level"' not in route_source
    assert '"urgency"' not in route_source
    assert '"immediate_crisis"' not in route_source
```

- [ ] **Step 3: Run the Agent tests red**

Run:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/test_adapter_conformance.py tests/test_crisis_runtime_boundary.py -q
```

Expected: FAIL because production AgentService still exposes crisis APIs and its Router prompt/result contains risk fields.

- [ ] **Step 4: Remove crisis APIs and intent routing**

Delete from `services/agent_service.py`:

- `AGENT_CRISIS_SYSTEM_MESSAGE` import.
- `_CRISIS_KEYWORDS` and `_CRISIS_DENIAL_PATTERNS`.
- The crisis branch in `_keyword_classify()`.
- `assess_crisis_risk()`, `_keyword_crisis_risk()` and `_has_explicit_crisis_denial()`.

Change `classify_intent()` to accept only:

```python
valid_intents = {"counseling", "entertainment", "chitchat", "relaxation"}
```

Update `_call_json()` documentation so it lists intent, RAG routing, relaxation and emotion only.

- [ ] **Step 5: Remove Router risk fields without introducing Phase 2 contracts**

Keep the current Router action/scale compatibility format, but make the JSON schema:

```json
{
  "action": "chat|start_scale|continue_scale|recommend_relaxation|recommend_game|recommend_media|exit",
  "scale": null,
  "target_item": null,
  "intervention_type": null,
  "confidence": 0.0,
  "reason": "15字以内"
}
```

Remove `urgency` and `risk_level` from every example. Remove `risk_level` and `immediate_crisis` from both the normalized return and the fallback return. Do not introduce `RouterProposal`, `TurnPolicy` or a new `TurnDecision`.

- [ ] **Step 6: Remove obsolete tests and update protocol comments**

Delete `tests/test_crisis_risk.py`. In `adapters/protocols.py`, delete `assess_crisis_risk()` and `_keyword_crisis_risk()` from `AgentBackend` and remove comments saying crisis decisions belong to the Agent.

- [ ] **Step 7: Run Agent/protocol tests**

Run:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/test_adapter_conformance.py tests/test_agent_timeout.py tests/test_crisis_runtime_boundary.py -q
```

Expected: all selected tests PASS.

---

### Task 6: Detach crisis UI from MainWindow

**Files:**

- Create: `safety/legacy_dialog.py`
- Modify: `ui/dialogs.py`
- Modify: `ui/main_window.py`
- Modify: `ui/__init__.py`
- Modify: `tests/test_conversation_integration.py`
- Modify: `tests/integration/test_ui_boot_headless.py`

- [ ] **Step 1: Add UI source assertions**

Append to `tests/test_conversation_integration.py`:

```python
def test_main_window_has_no_crisis_ui_protocol():
    source = Path("ui/main_window.py").read_text(encoding="utf-8")
    for symbol in (
        "CRISIS_HOTLINES",
        "CrisisDialog",
        "show_crisis",
        "_show_crisis_dialog",
        "build_safety_gate",
        "assess_transcript",
        "EndType.SAFETY",
    ):
        assert symbol not in source
```

- [ ] **Step 2: Run the UI source test red**

Run:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/test_conversation_integration.py -q
```

Expected: FAIL on all currently wired crisis UI symbols.

- [ ] **Step 3: Move the retained dialog to the legacy safety domain**

Move the complete `CrisisDialog` class, unchanged in behavior, from `ui/dialogs.py` into `safety/legacy_dialog.py`. The new file imports the exact Qt classes and `BaseDialog` dependencies the class uses. No production module imports `safety.legacy_dialog`.

Remove `CrisisDialog` from `ui/__init__.py` exports and from `ui.main_window` dialog imports.

- [ ] **Step 4: Remove MainWindow crisis wiring**

Delete:

- `CRISIS_HOTLINES` import.
- `show_crisis` queue branch.
- `_show_crisis_dialog()`.
- both `EndType.SAFETY` branches.
- dynamic imports of `DEPLOYMENT_PROFILE`, `RUNTIME_MODELS` and `build_safety_gate` used only for Coordinator creation.
- `safety_gate=` from `ConversationCoordinator(...)`.
- the post-relaxation `coordinator.assess_transcript()` call; keep the existing feedback acknowledgement and scale-resume behavior unchanged.

Coordinator construction becomes:

```python
self.conversation_coordinator = ConversationCoordinator(
    pipeline=self.pipeline,
    journal=EventJournal(journal_root / "events.jsonl"),
)
```

- [ ] **Step 5: Run UI source and headless smoke tests**

Run:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/test_conversation_integration.py tests/integration/test_ui_boot_headless.py -q
```

Expected: all selected tests PASS and a real offscreen `MainWindow` still initializes.

---

### Task 7: Move retained Guard/safety configuration out of production packages

**Files:**

- Create: `safety/legacy_config.py`
- Create: `safety/guard_client.py`
- Create: `safety/vllm_guard_client.py`
- Modify: `safety/__init__.py`
- Modify: `inference/__init__.py`
- Modify: `inference/factory.py`
- Delete: `inference/guard_client.py`
- Delete: `inference/vllm_guard_client.py`
- Modify: `config.py`
- Modify: `deployment/profiles.py`
- Modify: `deployment/__init__.py`
- Modify: `scripts/check_config.py`
- Modify: `scripts/start_a100_vllm_stack.ps1`
- Modify: `tests/test_vllm_guard_client.py`
- Modify: `tests/test_deployment_profiles.py`
- Modify: `tests/test_config_health_vllm.py`

- [ ] **Step 1: Update retained Guard tests to the legacy namespace**

Change imports in `tests/test_vllm_guard_client.py` to:

```python
from safety.safety_gate import SafetyGate
from safety.types import SafetyAction
from safety.vllm_guard_client import VLLMGuardClient
```

Replace factory tests with direct construction tests; production `inference.factory` must no longer expose `build_guard_client` or `build_safety_gate`.

- [ ] **Step 2: Tighten deployment profile tests**

Replace optional Guard assertions with:

```python
assert not hasattr(profile, "optional_guard_model")
assert not hasattr(profile, "guard_base_url")
assert not hasattr(models, "optional_guard")
```

In config-health tests, assert `run_check()` calls dialogue, Agent, STT/TTS, knowledge and media checks, but not `check_guard_backend`.

- [ ] **Step 3: Run retained-safety and profile tests red**

Run:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/test_vllm_guard_client.py tests/test_safety_gate.py tests/test_deployment_profiles.py tests/test_config_health_vllm.py -q
```

Expected: FAIL until Guard code moves and deployment/config surfaces are removed.

- [ ] **Step 4: Preserve legacy constants only under safety**

Move the exact current values of these constants into `safety/legacy_config.py`:

```python
CRISIS_INTERVENTION_SUFFIX = """...existing suffix text..."""
AGENT_CRISIS_SYSTEM_MESSAGE = """...existing crisis classifier prompt..."""
CRISIS_HOTLINES = {
    "全国心理援助热线": "400-161-9995",
    "北京危机干预中心": "010-82951332",
    "生命热线": "400-821-1215",
    "紧急求助": "110/120",
}
```

The executor must copy the current strings byte-for-byte before deleting their definitions from `config.py`; production code must not import `safety.legacy_config`.

- [ ] **Step 5: Move Guard code and narrow inference exports**

Move the full `GuardClient` protocol and `VLLMGuardClient` implementation to `safety/guard_client.py` and `safety/vllm_guard_client.py`, changing their local imports to `safety.types`.

Make `inference/__init__.py` export only:

```python
from inference.dialogue_client import DialogueClient
from inference.router_client import RouterClient
from inference.vllm_client import VLLMOpenAIClient

__all__ = ["DialogueClient", "RouterClient", "VLLMOpenAIClient"]
```

Make `inference/factory.py` contain only `build_dialogue_client()` and its dialogue imports. Delete the original Guard files after their tests import from `safety`.

- [ ] **Step 6: Remove Guard fields from deployment and health checks**

Delete `optional_guard_model` and `guard_base_url` from `DeploymentProfile`; delete `optional_guard` from `RuntimeModels`; update all three profile constructors and both `RuntimeModels(...)` construction paths.

Keep the A100 values unchanged:

```python
dialogue_model="Qwen/Qwen2.5-72B-Instruct-AWQ"
dialogue_base_url="http://127.0.0.1:8000/v1"
agent_model="Qwen/Qwen2.5-3B-Instruct-AWQ"
agent_base_url="http://127.0.0.1:8001/v1"
```

Delete `GUARD_BACKEND`, `GUARD_MODEL`, `GUARD_MODEL_SERVER` from `config.py`; delete `check_guard_backend()` and its `run_check()` entry; delete only the two `VOICECHAT_GUARD_*` cleanup lines from `scripts/start_a100_vllm_stack.ps1`.

- [ ] **Step 7: Run retained legacy safety, production profile and boundary tests**

Run:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/test_vllm_guard_client.py tests/test_safety_gate.py tests/test_deployment_profiles.py tests/test_config_health_vllm.py tests/test_crisis_runtime_boundary.py -q
```

Expected: all selected tests PASS; legacy safety imports only when its dedicated tests explicitly import `safety`.

---

### Task 8: Remove crisis rules from production prompts and RAG

**Files:**

- Modify: `config.py`
- Modify: `services/rag_service.py`
- Modify: `knowledge_base/knowledge.json`
- Modify: `tests/test_rag.py`
- Modify: `tests/test_crisis_runtime_boundary.py`

- [ ] **Step 1: Add prompt and RAG tests**

Append to `tests/test_crisis_runtime_boundary.py`:

```python
def test_production_prompts_have_no_crisis_control_protocol():
    prompt_text = "\n".join((
        config.SYSTEM_PROMPT,
        config.AGENT_INTENT_SYSTEM_MESSAGE,
        config.RESEARCHER_REPORT_PROMPT,
    ))
    for marker in (
        "END_SAFETY",
        "红色预警",
        "危机干预（最高优先级）",
        '"risk_assessment"',
        '"risk_level"',
        '"immediate_action"',
    ):
        assert marker not in prompt_text
```

Append to `tests/test_rag.py`:

```python
def test_core_rag_contains_no_crisis_specific_entry(tmp_path):
    source = Path("knowledge_base/knowledge.json")
    (tmp_path / "knowledge.json").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    rag = RAGService(knowledge_base_path=str(tmp_path))

    assert all(entry["title"] != "危机干预与自杀预防" for entry in rag.knowledge_base)
    assert "危机干预" not in "\n".join(entry["content"] for entry in rag.knowledge_base)
```

Add `from pathlib import Path` to the test file.

- [ ] **Step 2: Run prompt/RAG tests red**

Run:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/test_crisis_runtime_boundary.py tests/test_rag.py -q
```

Expected: FAIL because production prompt strings and the core knowledge entry still contain crisis control material.

- [ ] **Step 3: Remove only crisis control text from SYSTEM_PROMPT**

Delete:

- the scale-task exception that allows relaxation tags for crisis.
- `【红色预警】` and crisis priority rules.
- the crisis intervention section and `END_SAFETY` instruction.
- the automatic “伤害自己” safety-confirmation sentence.

Keep PHQ-9 Q9, ordinary empathy for strong negative emotions, `|||`, scale tags, relaxation/game tags and all current MI style rules.

- [ ] **Step 4: Remove crisis from Agent and report prompts**

Remove the `crisis` category/rules/examples from `AGENT_INTENT_SYSTEM_MESSAGE`. Keep counseling, relaxation, entertainment and chitchat.

Remove `risk_assessment` from `RESEARCHER_REPORT_PROMPT` so new reports do not generate it. Do not remove reader-side handling in `services/report_generator.py`, `ui/session_review.py` or `ui/stats_panel.py`; those remain backward-compatible with historical records.

- [ ] **Step 5: Remove crisis-dedicated core RAG material**

From both `knowledge_base/knowledge.json` and `_create_default_knowledge_base()`:

- delete the entry titled `危机干预与自杀预防`.
- remove the `危机干预` line from the ordinary `抑郁情绪干预` entry.

From `_init_jieba()` remove only `危机干预`, `自杀预防` and `安全评估`. Keep depression/self-harm wording required for PHQ-9 and ordinary psychological text handling. Do not change scoring, lazy loading, top-k, cache or RAG truncation.

- [ ] **Step 6: Run prompt/RAG, config and scale tests**

Run:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/test_crisis_runtime_boundary.py tests/test_rag.py tests/test_config_detection.py tests/test_pipeline.py tests/test_core_scoring.py -q
```

Expected: all selected tests PASS, including PHQ-9 Q9 and score-tag validation.

---

### Task 9: Remove executable crisis events and new crisis report output

**Files:**

- Modify: `app/contracts.py`
- Modify: `app/engine.py`
- Modify: `core/session_fsm.py`
- Modify: `services/report_service.py`
- Modify: `services/tools/report_tool.py`
- Modify: `tests/test_app_contracts.py`
- Modify: `tests/test_app_engine.py`
- Modify: `tests/test_core_session_fsm.py`
- Modify: `tests/test_report_service.py`

- [ ] **Step 1: Tighten app contract tests**

Append to `tests/test_app_contracts.py`:

```python
def test_event_union_has_no_crisis_alert_kind():
    from app.contracts import _EVENT_TYPES

    assert "crisis_alert" not in _EVENT_TYPES
```

- [ ] **Step 2: Remove SAFETY-only behavior expectations**

In `tests/test_app_engine.py`, remove `{"end_type": EndType.SAFETY}` from the no-force parameter table; keep QUIT, INVALID and explicit `allow_force_relaxation=False` coverage.

Delete `test_safety_never_forces_relaxation` from `tests/test_core_session_fsm.py` and change the report parsing fallback case from `EndType.SAFETY` to `EndType.QUIT` in `tests/test_report_service.py`.

- [ ] **Step 3: Run the app/report tests red**

Run:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/test_app_contracts.py tests/test_app_engine.py tests/test_core_session_fsm.py tests/test_report_service.py -q
```

Expected: the new event-union test FAILS until `CrisisAlertEvent` is removed; ordinary app/FSM/report tests continue to expose regressions.

- [ ] **Step 4: Remove executable event and special ending branches**

Delete `CrisisAlertEvent` and its Event union entry from `app/contracts.py`.

Change the special no-force constants to:

```python
_NO_FORCE_END_TYPES = (EndType.INVALID, EndType.QUIT)
```

and the equivalent condition in `core/session_fsm.py` to exclude only INVALID/QUIT. Do not remove `EndType.SAFETY` from `core/types.py`.

- [ ] **Step 5: Remove crisis-only report helpers and writes**

Delete `CRISIS_HOTLINES` import, `get_crisis_resources()` and `get_crisis_resources_for_tts()` from `services/report_service.py`.

Delete the `risk_assessment -> crisis_risk_<level>` key-event block from `services/tools/report_tool.py`. Keep historical report rendering/read paths unchanged.

- [ ] **Step 6: Run app/report tests**

Run:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/test_app_contracts.py tests/test_app_engine.py tests/test_core_session_fsm.py tests/test_report_service.py tests/test_crisis_runtime_boundary.py -q
```

Expected: all selected tests PASS.

---

### Task 10: Update deployment and architecture documentation without changing the A100 runtime contract

**Files:**

- Modify: `README.md`
- Modify: `docs/refactor/01_feature_inventory.md`
- Modify: `docs/refactor/phase1_crisis_detachment_implementation.md`

- [ ] **Step 1: Replace active-safety claims**

In both Chinese and English README sections:

- remove diagrams/feature bullets claiming current production performs crisis keyword detection or deterministic safety routing.
- remove `END_SAFETY` from current executable tag lists.
- state that Phase 1 temporarily detaches crisis routing from production while the legacy source remains under `safety/`.
- keep the ethics disclaimer that operators must follow institutional procedures; clearly label it as an operational limitation, not an implemented feature.

Use this wording for the deployment section:

```text
当前 A100 生产 profile 仍是两个 vLLM 服务：72B 对话模型位于 127.0.0.1:8000，3B Router 位于 127.0.0.1:8001。Phase 1 未修改模型、端口或启动预算；危机/Guard 链路已暂时从生产运行时拆除，相关源代码仅保留在 safety/ 供后续重新设计。
```

- [ ] **Step 2: Update the feature inventory status**

Change F06 from an active smoke scenario to:

```markdown
| F06 | 危机/安全链路 | `safety/**` legacy source | 暂时脱离生产；Phase 1 boundary test 保证 main import graph 不可达 |
```

- [ ] **Step 3: Verify deploy files retain their exact service contract**

Run:

```powershell
git diff -- deployment/profiles.py scripts/start_a100_vllm_stack.ps1 scripts/start_vllm_a100.ps1
Select-String -LiteralPath deployment/profiles.py -Pattern 'Qwen/Qwen2.5-72B-Instruct-AWQ|127.0.0.1:8000/v1|Qwen/Qwen2.5-3B-Instruct-AWQ|127.0.0.1:8001/v1'
```

Expected: the diff contains only Guard field/remnant deletion; all four A100 model/endpoint matches remain. `scripts/start_vllm_a100.ps1` has no change.

- [ ] **Step 4: Populate implementation record changed-file and preservation sections**

Run `git diff --name-status` and list each Phase 1 path with a one-sentence reason. Record the four A100 model/endpoint values and explicitly state that STT, TTS, launchers, report generation and PySide6 entry were preserved.

---

### Task 11: Final verification, single commit and push

**Files:**

- Verify: all Phase 1 files and `tests/`
- Modify: `docs/refactor/phase1_crisis_detachment_implementation.md`

- [ ] **Step 1: Run the architecture boundary and PHQ-9 protection tests**

Run:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/test_crisis_runtime_boundary.py tests/test_pipeline.py tests/test_core_scoring.py tests/test_core_scale_fsm.py -q
```

Expected: PASS; `EndType.SAFETY` exists only for history, `END_SAFETY` is not executable, and PHQ-9 Q9 remains present and scoreable.

- [ ] **Step 2: Run ordinary behavior regression slices**

Run:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/test_conversation_coordinator.py tests/integration/test_pipeline_e2e.py tests/test_game_service.py tests/test_report_service.py tests/test_data_manager.py -q
```

Expected: PASS for ordinary text, voice transcript reuse, Router, scale, relaxation, game, normal end, report and storage paths.

- [ ] **Step 3: Run headless UI smoke**

Run:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/integration/test_ui_boot_headless.py -q
```

Expected: PASS; MainWindow initializes without importing or constructing a crisis UI path.

- [ ] **Step 4: Run deployment/config regression**

Run:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests/test_deployment_profiles.py tests/test_config_health_vllm.py tests/test_config_vllm.py tests/test_vllm_backend.py tests/test_vllm_deploy_script.py -q
```

Expected: PASS with the A100 72B/3B vLLM endpoints still at 8000/8001.

- [ ] **Step 5: Run the full regression suite**

Run:

```powershell
& 'E:\数据库\代码\Data\PDCH\voicechat\.venv\Scripts\python.exe' -m pytest tests -q
```

Expected: exit code `0` with no new failures. Record exact passed/skipped counts in the implementation record.

- [ ] **Step 6: Run static final gates**

Run:

```powershell
git diff --check
$patterns = 'SafetyGate|SafetyAction|SafetyDecision|show_crisis|CrisisDialog|assess_crisis|_keyword_crisis|crisis_lock|crisis_risk|crisis_indicators|safety_payload|END_SAFETY|CRISIS_INTERVENTION_SUFFIX|AGENT_CRISIS_SYSTEM_MESSAGE|CRISIS_HOTLINES|build_safety_gate|build_guard_client|GUARD_MODEL|from safety|import safety'
git grep -n -E $patterns -- ':!safety/**' ':!tests/test_safety_gate.py' ':!tests/test_vllm_guard_client.py' ':!core/types.py' ':!services/report_generator.py' ':!ui/session_review.py' ':!ui/stats_panel.py' ':!docs/**'
git status --short
```

Expected:

- `git diff --check` produces no output.
- grep has no active runtime hit; historical reader/enum files were explicitly excluded.
- status lists only Phase 1 files and the implementation plan/record.

- [ ] **Step 7: Complete the implementation record**

Record these explicit answers:

```text
Can production runtime import safety? NO
Can Coordinator invoke SafetyGate? NO
Can Pipeline emit show_crisis? NO
Can Agent call a crisis model? NO
Can current runtime create EndType.SAFETY? NO
Is PHQ-9 Q9 retained? YES
A100 profile preserved? YES
vLLM 8000/8001 preserved? YES
```

Also record targeted test, headless test, deployment test and full-suite summaries.

- [ ] **Step 8: Review the final diff before staging**

Run:

```powershell
git diff --stat
git diff
```

Expected: no RouterProposal, TurnPolicy, authoritative TurnDecision, ScaleRuntime migration, SessionEngine authority migration, sentence-streaming TTS or UI framework rewrite.

- [ ] **Step 9: Stage only explicit Phase 1 paths**

Run a path-specific `git add --` containing only files listed by `git diff --name-only` for this phase plus the new plan/record. Never use `git add .` or `git add -A`.

- [ ] **Step 10: Create the single required commit**

Run:

```powershell
git commit -m "refactor: detach crisis flow from production runtime"
```

Expected: one commit created on `codex/a100-vllm-safety`.

- [ ] **Step 11: Push and verify clean state**

Run:

```powershell
git -c http.proxy="" -c https.proxy="" push origin codex/a100-vllm-safety
git status --short --branch
git rev-parse HEAD
```

Expected: push succeeds; branch tracks `origin/codex/a100-vllm-safety`; working tree is clean. Add the final SHA to the implementation record only if that record was already included in the commit; if adding the SHA would mutate the committed file, report the SHA in the handoff instead of amending the verified commit.

---

## Stop condition

After Task 11, stop. Do not begin Phase 2 (`RouterProposal -> TurnPolicy -> TurnDecision`) in the same commit or work session.
