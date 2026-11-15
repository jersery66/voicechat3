"""Curated Untangle campaign and post-campaign endless mode."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import random

from .generator import Difficulty
from .model import UntangleHint, UntangleModel


class CampaignMode(str, Enum):
    CAMPAIGN = "campaign"
    ENDLESS = "endless"


@dataclass(frozen=True)
class LevelDefinition:
    number: int
    chapter: str
    chapter_number: int
    title: str
    seed: int
    point_count: int
    diagonal_count: int
    minimum_crossings: int
    fixed_node_ids: tuple[int, ...] = ()

    @property
    def edge_count(self) -> int:
        return self.point_count + self.diagonal_count

    @property
    def complexity(self) -> int:
        """Internal curation metric; it is never shown as a score to players."""
        return self.point_count + self.edge_count + self.minimum_crossings + 3 * len(self.fixed_node_ids)


@dataclass
class CampaignProgress:
    completed_levels: set[int] = field(default_factory=set)
    skipped_levels: set[int] = field(default_factory=set)
    all_levels_unlocked: bool = False

    def is_unlocked(self, number: int) -> bool:
        if self.all_levels_unlocked or number == 1:
            return True
        return number - 1 in self.completed_levels or number - 1 in self.skipped_levels

    def mark_completed(self, number: int) -> None:
        self.completed_levels.add(number)
        self.skipped_levels.discard(number)

    def mark_skipped(self, number: int) -> None:
        if number not in self.completed_levels:
            self.skipped_levels.add(number)


_LEVELS: tuple[LevelDefinition, ...] = (
    LevelDefinition(1, "理清头绪", 1, "先找出口", 1000, 6, 2, 2),
    LevelDefinition(2, "理清头绪", 1, "再看一眼", 1001, 7, 3, 4),
    LevelDefinition(3, "理清头绪", 1, "连起来了", 1002, 8, 4, 6),
    LevelDefinition(4, "越来越乱", 2, "线索变多", 1003, 9, 5, 8),
    LevelDefinition(5, "越来越乱", 2, "别急着拖", 1005, 10, 6, 10),
    LevelDefinition(6, "越来越乱", 2, "越来越乱", 1006, 11, 7, 15),
    LevelDefinition(7, "固定支点", 3, "先看支点", 1007, 10, 7, 15, (0,)),
    LevelDefinition(8, "固定支点", 3, "留出空间", 1004, 12, 9, 20, (0,)),
    LevelDefinition(9, "固定支点", 3, "两处牵制", 1009, 13, 10, 30, (0,)),
    LevelDefinition(10, "错综复杂", 4, "不对称", 1018, 13, 10, 40, (0, 3)),
    LevelDefinition(11, "错综复杂", 4, "交错", 1011, 14, 11, 50, (0, 3)),
    LevelDefinition(12, "错综复杂", 4, "回头看", 1008, 16, 13, 70, (0, 3)),
    LevelDefinition(13, "最后三关", 5, "稳住", 1016, 16, 13, 90, (0, 3, 7)),
    LevelDefinition(14, "最后三关", 5, "最后的线索", 1010, 18, 15, 120, (0, 3, 7, 10)),
    LevelDefinition(15, "最后三关", 5, "最后三关", 1012, 20, 17, 180, (0, 3, 7, 10)),
)


def campaign_levels() -> tuple[LevelDefinition, ...]:
    return _LEVELS


class UntangleCampaign:
    """Standalone campaign controller; it owns progression, not V1 runtime."""

    def __init__(self, *, all_levels_unlocked: bool = False, seed: int | None = None) -> None:
        self.progress = CampaignProgress(all_levels_unlocked=all_levels_unlocked)
        self.mode = CampaignMode.CAMPAIGN
        self._current_level_number = 1
        self._endless_rng = random.Random(seed)
        self.model = self._model_for_level(_LEVELS[0])

    @property
    def current_level(self) -> LevelDefinition | None:
        if self.mode is not CampaignMode.CAMPAIGN:
            return None
        return _LEVELS[self._current_level_number - 1]

    @property
    def campaign_completed(self) -> bool:
        return all(
            number in self.progress.completed_levels or number in self.progress.skipped_levels
            for number in range(1, len(_LEVELS) + 1)
        )

    def load_level(self, number: int) -> bool:
        if number < 1 or number > len(_LEVELS) or not self.progress.is_unlocked(number):
            return False
        self.mode = CampaignMode.CAMPAIGN
        self._current_level_number = number
        self.model = self._model_for_level(_LEVELS[number - 1])
        return True

    def complete_current(self) -> bool:
        level = self.current_level
        if level is None or not self.model.completed:
            return False
        self.progress.mark_completed(level.number)
        return True

    def skip_current(self) -> bool:
        level = self.current_level
        if level is None:
            return False
        self.progress.mark_skipped(level.number)
        if level.number < len(_LEVELS):
            return self.next_level()
        return True

    def next_level(self) -> bool:
        level = self.current_level
        if level is None or level.number >= len(_LEVELS):
            return False
        if level.number not in self.progress.completed_levels and level.number not in self.progress.skipped_levels:
            return False
        return self.load_level(level.number + 1)

    def replay_current(self) -> bool:
        level = self.current_level
        if level is not None:
            return self.load_level(level.number)
        return self.start_endless(self.model.difficulty, seed=self.model.seed)

    def start_campaign(self) -> bool:
        return self.load_level(self._current_level_number)

    def start_endless(self, difficulty: Difficulty = Difficulty.EASY, *, seed: int | None = None) -> bool:
        self.mode = CampaignMode.ENDLESS
        self.model = UntangleModel(
            difficulty=difficulty,
            seed=seed if seed is not None else self._endless_rng.randrange(2**31),
        )
        return True

    def next_endless(self, *, difficulty: Difficulty | None = None) -> bool:
        if self.mode is not CampaignMode.ENDLESS:
            return False
        return self.start_endless(difficulty or self.model.difficulty)

    def request_hint(self) -> UntangleHint:
        return self.model.request_hint()

    @staticmethod
    def _model_for_level(level: LevelDefinition) -> UntangleModel:
        difficulty = (
            Difficulty.EASY
            if level.point_count <= 8
            else Difficulty.NORMAL
            if level.point_count <= 12
            else Difficulty.CHALLENGE
        )
        return UntangleModel(
            difficulty=difficulty,
            seed=level.seed,
            point_count=level.point_count,
            minimum_crossings=level.minimum_crossings,
            diagonal_count=level.diagonal_count,
            fixed_node_ids=level.fixed_node_ids,
        )
