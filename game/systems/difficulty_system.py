"""
Dynamic Difficulty Adjustment (DDA) System.
Adapts game parameters based on player performance to maintain optimal challenge.
"""


class DifficultySystem:
    """Adjusts game difficulty based on recent player performance."""

    # Difficulty levels 1-5
    LEVEL_PARAMS = {
        1: {
            "spawn_interval": 3.0,
            "good_ratio": 0.80,
            "resource_lifetime": 12,
            "max_resources": 4,
            "player_speed_mult": 1.0,
            "description": "入门"
        },
        2: {
            "spawn_interval": 2.5,
            "good_ratio": 0.75,
            "resource_lifetime": 10,
            "max_resources": 5,
            "player_speed_mult": 1.0,
            "description": "简单"
        },
        3: {
            "spawn_interval": 2.0,
            "good_ratio": 0.70,
            "resource_lifetime": 8,
            "max_resources": 6,
            "player_speed_mult": 1.05,
            "description": "适中"
        },
        4: {
            "spawn_interval": 1.5,
            "good_ratio": 0.60,
            "resource_lifetime": 7,
            "max_resources": 7,
            "player_speed_mult": 1.1,
            "description": "挑战"
        },
        5: {
            "spawn_interval": 1.2,
            "good_ratio": 0.50,
            "resource_lifetime": 6,
            "max_resources": 8,
            "player_speed_mult": 1.15,
            "description": "高手"
        },
    }

    def __init__(self):
        self.level = 1
        self.recent_results = []  # Sliding window of recent Go/No-Go results
        self.consecutive_hits = 0
        self.consecutive_misses = 0
        self.total_level_ups = 0
        self.total_level_downs = 0
        self.peak_level = 1

    def on_go_nogo_result(self, success: bool):
        """Record a Go/No-Go result and adjust difficulty if needed."""
        self.recent_results.append(success)
        if len(self.recent_results) > 10:
            self.recent_results.pop(0)

        if success:
            self.consecutive_hits += 1
            self.consecutive_misses = 0
        else:
            self.consecutive_misses += 1
            self.consecutive_hits = 0

        # Level up: 5 consecutive hits
        if self.consecutive_hits >= 5 and self.level < 5:
            self.level += 1
            self.consecutive_hits = 0
            self.total_level_ups += 1
            self.peak_level = max(self.peak_level, self.level)
            return "level_up"

        # Level down: 3 consecutive misses
        if self.consecutive_misses >= 3 and self.level > 1:
            self.level -= 1
            self.consecutive_misses = 0
            self.total_level_downs += 1
            return "level_down"

        return None

    def get_params(self) -> dict:
        """Get current difficulty parameters."""
        return self.LEVEL_PARAMS[self.level].copy()

    def get_spawn_interval(self) -> float:
        return self.LEVEL_PARAMS[self.level]["spawn_interval"]

    def get_good_ratio(self) -> float:
        return self.LEVEL_PARAMS[self.level]["good_ratio"]

    def get_resource_lifetime(self) -> float:
        return self.LEVEL_PARAMS[self.level]["resource_lifetime"]

    def get_max_resources(self) -> int:
        return self.LEVEL_PARAMS[self.level]["max_resources"]

    def get_player_speed_mult(self) -> float:
        return self.LEVEL_PARAMS[self.level]["player_speed_mult"]

    def get_level_name(self) -> str:
        return self.LEVEL_PARAMS[self.level]["description"]

    def get_recent_accuracy(self) -> float:
        """Calculate accuracy from recent results."""
        if not self.recent_results:
            return 0.0
        return sum(self.recent_results) / len(self.recent_results) * 100

    def get_metrics(self) -> dict:
        """Get difficulty metrics for clinical tracking."""
        return {
            "difficulty_level": self.level,
            "difficulty_peak": self.peak_level,
            "difficulty_level_ups": self.total_level_ups,
            "difficulty_level_downs": self.total_level_downs,
            "difficulty_recent_accuracy": round(self.get_recent_accuracy(), 1),
        }
