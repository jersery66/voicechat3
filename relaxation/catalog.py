"""Deterministic metadata catalog for Relaxation Center content."""

from __future__ import annotations

from typing import Iterable

from .contracts import (
    RelaxationContentDefinition,
    RelaxationContentRole,
    RelaxationContentType,
)


class RelaxationCatalog:
    """Immutable registry for content discovery only."""

    def __init__(self, definitions: Iterable[RelaxationContentDefinition]) -> None:
        values = tuple(definitions)
        ids = [item.id for item in values]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate relaxation content id")
        self._definitions = values
        self._by_id = {item.id: item for item in values}

    @property
    def definitions(self) -> tuple[RelaxationContentDefinition, ...]:
        return self._definitions

    def get(self, content_id: str) -> RelaxationContentDefinition | None:
        return self._by_id.get(content_id)

    def require(self, content_id: str) -> RelaxationContentDefinition:
        item = self.get(content_id)
        if item is None:
            raise KeyError(f"unknown relaxation content: {content_id}")
        return item

    def list_enabled(self) -> tuple[RelaxationContentDefinition, ...]:
        return tuple(item for item in self._definitions if item.enabled)

    def list_by_role(
        self,
        role: RelaxationContentRole,
        *,
        enabled_only: bool = True,
    ) -> tuple[RelaxationContentDefinition, ...]:
        """Return content by product role without making a business decision."""
        return tuple(
            item
            for item in self._definitions
            if item.role is role and (item.enabled or not enabled_only)
        )

    def __iter__(self):
        return iter(self._definitions)

    def __len__(self) -> int:
        return len(self._definitions)


def _content(**values) -> RelaxationContentDefinition:
    return RelaxationContentDefinition(**values)


def build_default_catalog() -> RelaxationCatalog:
    """Register current exercises plus planned V1 game metadata."""
    return RelaxationCatalog(
        [
            _content(
                id="breathing", display_name="呼吸放松", category=RelaxationContentType.EXERCISE,
                role=RelaxationContentRole.CORE_RELAXATION,
                recommended_duration_seconds=180, max_duration_seconds=300,
                requires_audio=True, implementation_type="existing_relaxation",
                implementation_status="AVAILABLE", sort_order=10,
            ),
            _content(
                id="muscle_relaxation", display_name="肌肉放松", category=RelaxationContentType.EXERCISE,
                role=RelaxationContentRole.CORE_RELAXATION,
                recommended_duration_seconds=300, max_duration_seconds=600,
                requires_audio=True, implementation_type="existing_relaxation",
                implementation_status="AVAILABLE", sort_order=20,
            ),
            _content(
                id="meditation", display_name="正念练习", category=RelaxationContentType.EXERCISE,
                role=RelaxationContentRole.CORE_RELAXATION,
                recommended_duration_seconds=180, max_duration_seconds=600,
                requires_audio=True, implementation_type="existing_relaxation",
                implementation_status="AVAILABLE", sort_order=30,
            ),
            _content(
                id="bubble_pop", display_name="泡泡", category=RelaxationContentType.GAME,
                role=RelaxationContentRole.LEISURE,
                recommended_duration_seconds=120, max_duration_seconds=300,
                requires_mouse=True, implementation_type="local_deterministic",
                implementation_status="PLANNED", sort_order=40,
            ),
            _content(
                id="gentle_search", display_name="找一找", category=RelaxationContentType.GAME,
                role=RelaxationContentRole.LEISURE,
                recommended_duration_seconds=180, max_duration_seconds=300,
                requires_mouse=True, implementation_type="local_deterministic",
                implementation_status="PLANNED", sort_order=50,
            ),
            _content(
                id="calm_puzzle", display_name="轻拼图", category=RelaxationContentType.GAME,
                role=RelaxationContentRole.LEISURE,
                recommended_duration_seconds=300, max_duration_seconds=600,
                requires_mouse=True, implementation_type="local_deterministic",
                implementation_status="PLANNED", sort_order=60,
            ),
            _content(
                id="falling_leaves", display_name="接住落叶", category=RelaxationContentType.GAME,
                role=RelaxationContentRole.LEISURE,
                recommended_duration_seconds=120, max_duration_seconds=300,
                requires_mouse=True, implementation_type="local_deterministic",
                implementation_status="PLANNED", sort_order=70,
            ),
        ]
    )
