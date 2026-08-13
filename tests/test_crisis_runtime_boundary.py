"""Architecture boundary for the temporarily detached crisis runtime.

These checks are intentionally source-level.  Importing the application entry
point would construct a Qt application and may load model dependencies, so the
production reachability check parses local imports instead.
"""

from __future__ import annotations

import ast
import json
from collections import deque
from pathlib import Path

from core.types import EndType


ROOT = Path(__file__).resolve().parents[1]


def _module_file(module: str) -> Path | None:
    """Resolve an absolute project module to a local Python file, if present."""
    if not module or any(part in {"", ".", ".."} for part in module.split(".")):
        return None
    parts = module.split(".")
    candidate = ROOT.joinpath(*parts).with_suffix(".py")
    if candidate.is_file():
        return candidate.resolve()
    package = ROOT.joinpath(*parts, "__init__.py")
    return package.resolve() if package.is_file() else None


def _package_parts(path: Path) -> list[str]:
    """Return the import package containing ``path``."""
    relative = path.resolve().relative_to(ROOT)
    parts = list(relative.parts)
    if parts[-1] == "__init__.py":
        return parts[:-1]
    return parts[:-1]


def _relative_module(path: Path, level: int, module: str | None) -> list[str]:
    """Resolve ``ImportFrom``'s dotted relative module against ``path``."""
    package = _package_parts(path)
    # ``level=1`` means the current package, ``level=2`` its parent, etc.
    if level > len(package) + 1:
        return []
    base = package[: len(package) - (level - 1)]
    if module:
        base.extend(module.split("."))
    return base


def _local_imports(path: Path) -> set[Path]:
    """Collect local targets from absolute and relative Python imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[Path] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = _module_file(alias.name)
                if target is not None:
                    found.add(target)
            continue

        if not isinstance(node, ast.ImportFrom):
            continue

        if node.level:
            module_parts = _relative_module(path, node.level, node.module)
            module = ".".join(module_parts)
        elif node.module:
            module = node.module
            module_parts = module.split(".")
        else:
            module = ""
            module_parts = []

        targets = []
        if module:
            targets.append(module)
        # ``from package import child`` may not be re-exported by the package
        # __init__, so also inspect a local child module when one exists.
        targets.extend(
            ".".join([*module_parts, alias.name])
            for alias in node.names
            if alias.name != "*"
        )
        for target_module in targets:
            target = _module_file(target_module)
            if target is not None:
                found.add(target)
    return found


def _reachable_from_main() -> set[Path]:
    queue = deque([(ROOT / "main.py").resolve()])
    reached: set[Path] = set()
    while queue:
        path = queue.popleft()
        if path in reached or not path.is_file():
            continue
        reached.add(path)
        queue.extend(_local_imports(path) - reached)
    return reached


def _top_level_names(path: Path) -> set[str]:
    """Return names defined or imported by a module without executing it."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def _top_level_mapping(path: Path, name: str) -> ast.Dict:
    """Find a literal top-level mapping assignment without importing it."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            assert isinstance(node.value, ast.Dict), f"{name} must remain a literal mapping"
            return node.value
    raise AssertionError(f"top-level mapping {name!r} was not found")


def _literal_key(key: ast.expr) -> object:
    try:
        return ast.literal_eval(key)
    except (ValueError, TypeError):
        return object()


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
        "SafetyGate",
        "SafetyAction",
        "SafetyDecision",
        "show_crisis",
        "CrisisDialog",
        "assess_transcript",
        "assess_crisis_risk",
        "_keyword_crisis_risk",
        "crisis_lock_turns",
        "crisis_risk",
        "crisis_indicators",
        "safety_payload",
        "END_SAFETY",
        "CrisisAlertEvent",
        "build_safety_gate",
        "build_guard_client",
    }
    hits = {
        relative: sorted(
            symbol
            for symbol in forbidden
            if symbol in (ROOT / relative).read_text(encoding="utf-8")
        )
        for relative in active_files
    }
    assert {path: symbols for path, symbols in hits.items() if symbols} == {}


def test_production_config_no_longer_exports_crisis_runtime_controls():
    exported = _top_level_names(ROOT / "config.py")
    for name in (
        "CRISIS_INTERVENTION_SUFFIX",
        "AGENT_CRISIS_SYSTEM_MESSAGE",
        "CRISIS_HOTLINES",
        "GUARD_MODEL",
        "GUARD_MODEL_SERVER",
    ):
        assert name not in exported


def test_end_safety_is_historical_but_phq9_item_nine_is_preserved():
    assert EndType.SAFETY.value == "SAFETY"
    pipeline_path = ROOT / "services/pipeline.py"
    natural_questions = _top_level_mapping(pipeline_path, "NATURAL_SCALE_QUESTIONS")
    assert any(_literal_key(key) == ("PHQ-9", 9) for key in natural_questions.keys)

    phq9_item_core = _top_level_mapping(pipeline_path, "PHQ9_ITEM_CORE")
    item_nine = next(
        value
        for key, value in zip(phq9_item_core.keys, phq9_item_core.values)
        if _literal_key(key) == 9
    )
    assert isinstance(item_nine, ast.Dict)
    assert any(_literal_key(key) == "scale_item_text" for key in item_nine.keys)


def test_agent_router_prompt_has_no_crisis_output_fields():
    source = (ROOT / "services/agent_service.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(ROOT / "services/agent_service.py"))
    router_node = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "route_conversation_actions"
    )
    # The source segment ends at the next same-level definition, so a later
    # helper cannot accidentally make this assertion fail.
    router_source = ast.get_source_segment(source, router_node) or ""
    assert "risk_level" not in router_source
    assert "urgency" not in router_source
    assert "immediate_crisis" not in router_source


def test_production_prompts_have_no_crisis_control_protocol():
    import config

    prompt_text = "\n".join(
        (
            config.SYSTEM_PROMPT,
            config.AGENT_INTENT_SYSTEM_MESSAGE,
            config.RESEARCHER_REPORT_PROMPT,
        )
    )
    for marker in (
        "END_SAFETY",
        "红色预警",
        "危机干预（最高优先级）",
        '"risk_assessment"',
        '"risk_level"',
        '"immediate_action"',
    ):
        assert marker not in prompt_text


def test_crisis_knowledge_is_preserved_only_under_legacy_resources():
    resource = ROOT / "safety" / "resources" / "crisis_knowledge.json"
    assert resource.is_file()
    assert json.loads(resource.read_text(encoding="utf-8")) == [
        {
            "keywords": ["自杀", "想死", "不想活", "轻生", "自残", "割腕", "活够了", "死了算了"],
            "title": "危机干预与自杀预防",
            "content": (
                "针对自杀风险的危机干预流程：\n"
                "1. 立即行动：如来访者表达自杀念头，立即向管教民警报告，不要独自处理\n"
                "2. 安全评估：评估是否有具体计划、方法和时间，了解自杀意念的强度\n"
                "3. 移除危险：确保来访者身边没有尖锐物品、绳索等危险物品\n"
                "4. 陪伴看护：安排同戒人员24小时轮流陪伴，不使其独处\n"
                "5. 情感支持：认真倾听，不评判，表达关心：你的命很重要，我们都在\n"
                "6. 专业干预：安排所内心理咨询师进行紧急心理干预\n"
                "7. 后续跟进：建立每日谈话制度，持续关注情绪变化"
            ),
        }
    ]

    production_entries = json.loads(
        (ROOT / "knowledge_base" / "knowledge.json").read_text(encoding="utf-8")
    )
    assert all(entry["title"] != "危机干预与自杀预防" for entry in production_entries)


def test_production_rag_does_not_access_legacy_crisis_resources():
    rag_source = (ROOT / "services" / "rag_service.py").read_text(encoding="utf-8")
    assert "safety/resources" not in rag_source
    assert "crisis_knowledge" not in rag_source
    assert (ROOT / "safety" / "resources" / "crisis_knowledge.json").resolve() not in _reachable_from_main()
