"""Campaign, fixed-node, hint, and endless-mode contracts for Untangle V2-A.2."""

from __future__ import annotations

from relaxation.puzzles.untangle.campaign import (
    CampaignMode,
    UntangleCampaign,
    campaign_levels,
)
from relaxation.puzzles.untangle.generator import Difficulty
from relaxation.puzzles.untangle.model import UntangleModel


def _finish(model: UntangleModel) -> None:
    for point in model.target_positions:
        model.move_point(point.id, point.x, point.y)


def test_campaign_has_fifteen_fixed_seed_levels_and_chapters():
    levels = campaign_levels()
    assert len(levels) == 15
    assert [level.number for level in levels] == list(range(1, 16))
    assert len({level.seed for level in levels}) == 15
    assert [level.chapter for level in levels[:3]] == ["理清头绪"] * 3
    assert [level.chapter for level in levels[3:6]] == ["越来越乱"] * 3
    assert [level.chapter for level in levels[6:9]] == ["固定支点"] * 3
    assert [level.chapter for level in levels[9:12]] == ["错综复杂"] * 3
    assert [level.chapter for level in levels[12:]] == ["最后三关"] * 3
    assert levels[6].fixed_node_ids
    assert levels[-1].fixed_node_ids
    assert all(level.edge_count == level.point_count + level.diagonal_count for level in levels)
    assert [level.complexity for level in levels] == sorted(level.complexity for level in levels)


def test_campaign_level_is_deterministic_and_fixed_nodes_stay_at_target():
    first = UntangleCampaign(all_levels_unlocked=True)
    second = UntangleCampaign(all_levels_unlocked=True)
    assert first.load_level(7) is True
    assert second.load_level(7) is True
    assert first.model.state == second.model.state
    for node_id in first.model.fixed_node_ids:
        assert first.model.points[node_id] == first.model.target_positions[node_id]


def test_fixed_nodes_cannot_be_dragged_or_moved():
    model = UntangleModel(difficulty=Difficulty.EASY, seed=7, fixed_node_ids=(0,))
    point = model.points[0]
    assert model.begin_drag(point.id) is False
    assert model.move_point(point.id, 0.2, 0.2) is False
    assert model.points[0] == point


def test_hints_progress_without_moving_or_solving_the_puzzle():
    model = UntangleModel(difficulty=Difficulty.NORMAL, seed=7)
    before = model.state.points
    first = model.request_hint()
    second = model.request_hint()
    third = model.request_hint()
    fourth = model.request_hint()
    assert first.level == 1
    assert second.level == 2
    assert third.level == 3
    assert fourth.level == 3
    assert first.node_id is not None
    assert second.edge_indices
    assert third.direction
    assert model.state.points == before
    assert model.completed is False


def test_campaign_progression_supports_skip_replay_and_final_completion():
    campaign = UntangleCampaign(all_levels_unlocked=True)
    assert campaign.mode is CampaignMode.CAMPAIGN
    assert campaign.current_level.number == 1
    _finish(campaign.model)
    assert campaign.complete_current() is True
    assert campaign.next_level() is True
    assert campaign.current_level.number == 2
    assert campaign.replay_current() is True
    assert campaign.current_level.number == 2
    assert campaign.skip_current() is True
    assert campaign.current_level.number == 3
    for number in range(3, 16):
        assert campaign.load_level(number) is True
        _finish(campaign.model)
        assert campaign.complete_current() is True
        if number < 15:
            assert campaign.next_level() is True
    assert campaign.campaign_completed is True


def test_campaign_locking_and_endless_mode_are_separate():
    campaign = UntangleCampaign()
    assert campaign.load_level(2) is False
    assert campaign.load_level(1) is True
    campaign.skip_current()
    assert campaign.load_level(2) is True
    assert campaign.start_endless(Difficulty.CHALLENGE, seed=99) is True
    assert campaign.mode is CampaignMode.ENDLESS
    assert campaign.current_level is None
    assert campaign.model.difficulty is Difficulty.CHALLENGE
    assert campaign.replay_current() is True
