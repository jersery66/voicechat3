import pygame
import time
import math
import random
from game.config import (
    STORM_MIN_INTERVAL, STORM_MAX_INTERVAL, COLOR_STORM_OVERLAY, COLOR_BREATH_CIRCLE,
    BREATHING_CYCLE_SECONDS
)


class StormSystem:
    """Craving management: random storm events trigger breathing exercise (4-7-8 pattern).
    Now with text guidance and rhythm scoring."""

    def __init__(self, tracker):
        self.tracker = tracker
        self.active = False
        self.next_storm_time = time.time() + random.uniform(STORM_MIN_INTERVAL, STORM_MAX_INTERVAL)
        self.breath_phase = None    # "inhale", "hold", "exhale"
        self.breath_timer = 0
        self.cycle_count = 0
        self.target_cycles = 2
        self.circle_radius = 50
        self.max_radius = 200
        self.keypress_times = []
        self.phase_start_time = 0
        self.inhale_start_time = 0  # opening time of the current inhale window
        self.space_held = False
        self.space_hold_start = 0
        self.rhythm_scores = []  # Score for each cycle (0-3 stars)
        self.health_restore_pending = 0
        self.guidance_text = ""
        self.guidance_subtext = ""

    def update(self, dt):
        now = time.time()

        if not self.active:
            if now >= self.next_storm_time:
                self._start_storm()
            return False

        self.breath_timer += dt

        if self.breath_phase == "inhale":
            progress = min(self.breath_timer / 4.0, 1.0)
            self.circle_radius = 50 + int((self.max_radius - 50) * progress)
            self.guidance_text = "缓慢吸气..."
            self.guidance_subtext = f"{max(0, 4 - int(self.breath_timer)):.0f}秒"
            if self.breath_timer >= 4.0:
                self._transition_phase("hold")

        elif self.breath_phase == "hold":
            self.circle_radius = self.max_radius + int(10 * math.sin(self.breath_timer * 2))
            self.guidance_text = "轻轻屏住..."
            self.guidance_subtext = f"{max(0, 7 - int(self.breath_timer)):.0f}秒"
            if self.breath_timer >= 7.0:
                self._transition_phase("exhale")

        elif self.breath_phase == "exhale":
            progress = min(self.breath_timer / 8.0, 1.0)
            self.circle_radius = self.max_radius - int((self.max_radius - 50) * progress)
            self.guidance_text = "慢慢呼出..."
            self.guidance_subtext = f"{max(0, 8 - int(self.breath_timer)):.0f}秒"
            if self.breath_timer >= 8.0:
                self.cycle_count += 1

                # Calculate rhythm score for this cycle
                score = self._calculate_cycle_score()
                self.rhythm_scores.append(score)

                # Health restore based on score
                health_restore = score * 5  # 0-15 HP per cycle
                self.health_restore_pending += health_restore

                self.tracker.record_event("breathing_cycle",
                                          success=True,
                                          detail=f"cycle_{self.cycle_count} score={score}")

                if self.cycle_count >= self.target_cycles:
                    self._end_storm()
                else:
                    self._transition_phase("inhale")

        return True

    def _calculate_cycle_score(self):
        """Calculate breathing rhythm score (0-3) based on keypress timing."""
        if not self.keypress_times:
            return 1  # Default score for participation

        # Check if space was held during inhale phase
        score = 1  # Base score for completing

        # Bonus for pressing during the inhale window (4s from inhale start)
        inhale_presses = [t for t in self.keypress_times
                          if self.inhale_start_time <= t <= self.inhale_start_time + 4]
        if inhale_presses:
            score += 1

        # Bonus for rhythm consistency
        if len(self.keypress_times) >= 2:
            score += 1

        return min(3, score)

    def _start_storm(self):
        self.active = True
        self.cycle_count = 0
        self.keypress_times = []
        self.rhythm_scores = []
        self.health_restore_pending = 0
        self.tracker.record_event("storm_start")
        self._transition_phase("inhale")

    def _end_storm(self):
        self.active = False
        avg_score = sum(self.rhythm_scores) / len(self.rhythm_scores) if self.rhythm_scores else 0
        self.tracker.record_event("storm_end",
                                  detail=f"cycles_completed={self.cycle_count} avg_score={avg_score:.1f}")
        self.next_storm_time = time.time() + random.uniform(
            STORM_MIN_INTERVAL, STORM_MAX_INTERVAL)
        self.guidance_text = ""
        self.guidance_subtext = ""

    def _transition_phase(self, new_phase):
        self.breath_phase = new_phase
        self.breath_timer = 0
        self.phase_start_time = time.time()
        # Record the inhale-window opening so the cycle score can reward
        # presses during inhale (the previous phase_start_time pointed at the
        # exhale start, so inhale presses never scored). Reset the keypress
        # buffer once per cycle (at inhale) so it accumulates all presses.
        if new_phase == "inhale":
            self.inhale_start_time = self.phase_start_time
            self.keypress_times = []
        self.tracker.record_event("breathing_phase", detail=new_phase)

    def handle_keypress(self, event):
        if event.key == pygame.K_SPACE:
            now = time.time()
            self.keypress_times.append(now)
            self.space_held = True
            self.space_hold_start = now
            self.tracker.record_event("breathing_keypress",
                                      detail=f"phase={self.breath_phase} timer={self.breath_timer:.1f}")

    def handle_keyrelease(self, event):
        if event.key == pygame.K_SPACE:
            self.space_held = False

    def get_pending_health_restore(self):
        """Get and clear pending health restore."""
        restore = self.health_restore_pending
        self.health_restore_pending = 0
        return restore

    def render(self, surface, screen_w, screen_h):
        # Dark overlay
        overlay = pygame.Surface((screen_w, screen_h), pygame.SRCALPHA)
        overlay.fill((*COLOR_STORM_OVERLAY, 160))
        surface.blit(overlay, (0, 0))

        cx, cy = screen_w // 2, screen_h // 2

        try:
            font_large = pygame.font.SysFont("microsoftyahei", 48, bold=True)
            font_medium = pygame.font.SysFont("microsoftyahei", 32)
            font_small = pygame.font.SysFont("microsoftyahei", 24)
        except:
            font_large = pygame.font.Font(None, 48)
            font_medium = pygame.font.Font(None, 32)
            font_small = pygame.font.Font(None, 24)

        # Title
        title = font_large.render("渴求风暴来袭", True, (255, 150, 150))
        title_rect = title.get_rect(center=(cx, cy - 200))
        surface.blit(title, title_rect)

        # Breathing circle
        radius = max(10, int(self.circle_radius))
        alpha = int(180 + 75 * math.sin(time.time() * 1.5))

        circle_surf = pygame.Surface((radius * 2 + 20, radius * 2 + 20), pygame.SRCALPHA)
        pygame.draw.circle(circle_surf, (*COLOR_BREATH_CIRCLE, min(255, alpha)),
                           (radius + 10, radius + 10), radius)
        surface.blit(circle_surf, (cx - radius - 10, cy - radius - 10))

        # Phase indicator ring
        if self.breath_phase == "inhale":
            ring_color = (100, 255, 100, 200)
        elif self.breath_phase == "hold":
            ring_color = (255, 255, 100, 200)
        else:
            ring_color = (100, 180, 255, 200)

        ring_surf = pygame.Surface((screen_w, screen_h), pygame.SRCALPHA)
        pygame.draw.circle(ring_surf, ring_color, (cx, cy), radius + 5, 4)
        surface.blit(ring_surf, (0, 0))

        # Guidance text (center of circle)
        if self.guidance_text:
            guidance_surf = font_medium.render(self.guidance_text, True, (255, 255, 255))
            guidance_rect = guidance_surf.get_rect(center=(cx, cy - 20))
            surface.blit(guidance_surf, guidance_rect)

            # Countdown
            if self.guidance_subtext:
                sub_surf = font_large.render(self.guidance_subtext, True, (255, 255, 200))
                sub_rect = sub_surf.get_rect(center=(cx, cy + 30))
                surface.blit(sub_surf, sub_rect)

        # Progress bar
        if self.breath_phase:
            bar_width = 300
            bar_height = 8
            bar_x = cx - bar_width // 2
            bar_y = cy + radius + 30

            # Background
            pygame.draw.rect(surface, (60, 60, 80), (bar_x, bar_y, bar_width, bar_height))

            # Progress
            if self.breath_phase == "inhale":
                progress = min(self.breath_timer / 4.0, 1.0)
                color = (100, 255, 100)
            elif self.breath_phase == "hold":
                progress = min(self.breath_timer / 7.0, 1.0)
                color = (255, 255, 100)
            else:
                progress = min(self.breath_timer / 8.0, 1.0)
                color = (100, 180, 255)

            pygame.draw.rect(surface, color, (bar_x, bar_y, int(bar_width * progress), bar_height))

        # Cycle counter
        cycle_text = f"呼吸周期: {self.cycle_count}/{self.target_cycles}"
        cycle_surf = font_small.render(cycle_text, True, (200, 200, 200))
        surface.blit(cycle_surf, (20, screen_h - 60))

        # Stars from previous cycles
        if self.rhythm_scores:
            stars_text = "评分: " + "".join(["★" * s + "☆" * (3 - s) for s in self.rhythm_scores])
            stars_surf = font_small.render(stars_text, True, (255, 215, 0))
            surface.blit(stars_surf, (20, screen_h - 30))

        # Instructions
        if self.breath_phase == "inhale":
            hint = font_small.render("按住空格键配合吸气", True, (200, 200, 200))
        elif self.breath_phase == "exhale":
            hint = font_small.render("松开空格键配合呼气", True, (200, 200, 200))
        else:
            hint = font_small.render("保持平静", True, (200, 200, 200))
        hint_rect = hint.get_rect(center=(cx, screen_h - 80))
        surface.blit(hint, hint_rect)
