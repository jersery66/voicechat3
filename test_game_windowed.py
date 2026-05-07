#!/usr/bin/env python3
"""Windowed test script for the therapeutic game."""

import os
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame
from game.engine import GameEngine
from game.clinical_tracker import ClinicalTracker


def main():
    print("=" * 50)
    print("Therapeutic Game - Windowed Test Mode")
    print("=" * 50)
    print()
    print("Controls:")
    print("  WASD / Arrow Keys - Move player")
    print("  B - Build camp (when prompted)")
    print("  N - Skip camp building")
    print("  Space - Breathing exercise (during storm)")
    print("  ESC - End game and view summary")
    print()

    # Override fullscreen setting for testing
    import game.config
    game.config.FULLSCREEN = False

    # Create output directory
    output_dir = os.path.join(os.path.dirname(__file__), "test_output")
    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, "game_test_data.csv")

    # Create tracker and engine
    tracker = ClinicalTracker(csv_path)

    # Patch engine to use windowed mode
    original_run = GameEngine.run

    def patched_run(self):
        pygame.init()
        pygame.mouse.set_visible(True)

        # Windowed mode for testing
        self.screen = pygame.display.set_mode((1200, 800))
        self.screen_width, self.screen_height = self.screen.get_size()
        pygame.display.set_caption("心理互动游戏 - 测试模式")

        try:
            self.font_large = pygame.font.SysFont("microsoftyahei", 48, bold=True)
            self.font_medium = pygame.font.SysFont("microsoftyahei", 28)
            self.font_small = pygame.font.SysFont("microsoftyahei", 20)
        except:
            self.font_large = pygame.font.Font(None, 48)
            self.font_medium = pygame.font.Font(None, 28)
            self.font_small = pygame.font.Font(None, 20)

        self.clock = pygame.time.Clock()
        self.game_start_time = time.time()
        self.tutorial_start_time = time.time()
        self.tracker.record_event("game_start")

        self._init_game_objects()

        try:
            while self.running:
                dt = self.clock.tick(60) / 1000.0
                self._handle_events()
                self._update(dt)
                self._render()
        except Exception as e:
            print(f"[ERROR] Game engine error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.tracker.record_event("game_end",
                                      detail=f"reason={self._exit_reason}")
            self.tracker.save_csv()
            pygame.quit()

        return self.tracker.get_summary_metrics()

    GameEngine.run = patched_run

    engine = GameEngine(tracker)

    # Run game
    print("Launching game in windowed mode...")
    results = engine.run()

    # Print results
    print()
    print("=" * 50)
    print("Game Results:")
    print("=" * 50)
    for key, value in results.items():
        print(f"  {key}: {value}")

    print()
    print(f"Clinical data saved to: {csv_path}")
    print()


if __name__ == "__main__":
    main()
