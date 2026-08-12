import pygame
import random
import time
import math
from game.config import (
    GOOD_RESOURCE_RADIUS, MAP_WIDTH, MAP_HEIGHT,
    COLOR_GOOD_RESOURCE, COLOR_BAD_RESOURCE
)


class Resource:
    def __init__(self, x, y, is_good, spawn_time, lifetime):
        self.x = x
        self.y = y
        self.is_good = is_good
        self.spawn_time = spawn_time
        self.alive = True
        self.lifetime = lifetime
        self.pulse_phase = random.uniform(0, math.pi * 2)


class ResourceSystem:
    """Go/No-Go task: collect green, avoid red."""

    def __init__(self, tracker, difficulty_system=None):
        self.tracker = tracker
        self.difficulty = difficulty_system
        self.resources = []
        self.last_spawn_time = 0
        self.collected_good = 0
        self.collect_effect = []

    def update(self, dt, player_x, player_y, player_radius):
        now = time.time()

        spawn_interval = self.difficulty.get_spawn_interval() if self.difficulty else 2.0
        max_resources = self.difficulty.get_max_resources() if self.difficulty else 6

        if (now - self.last_spawn_time >= spawn_interval and
                len(self.resources) < max_resources):
            self._spawn_resource()
            self.last_spawn_time = now

        # Despawn expired
        self.resources = [r for r in self.resources
                          if now - r.spawn_time < r.lifetime and r.alive]

        # Update effects
        self.collect_effect = [(x, y, t, good) for x, y, t, good in self.collect_effect
                               if now - t < 0.5]

        # Collision check
        collected_this_frame = None
        for r in self.resources:
            if not r.alive:
                continue
            dist = math.hypot(r.x - player_x, r.y - player_y)
            # Use the per-kind collision radius so good and bad resources can be
            # tuned independently (config has both GOOD_RESOURCE_RADIUS and
            # BAD_RESOURCE_RADIUS).
            collision_radius = GOOD_RESOURCE_RADIUS if r.is_good else BAD_RESOURCE_RADIUS
            if dist < player_radius + collision_radius:
                reaction_ms = (now - r.spawn_time) * 1000
                r.alive = False
                self.collect_effect.append((r.x, r.y, now, r.is_good))

                if r.is_good:
                    self.tracker.record_event("go_nogo_response",
                                              reaction_time_ms=reaction_ms, success=True,
                                              detail="good_resource_collected")
                    self.collected_good += 1
                    collected_this_frame = "good"
                else:
                    self.tracker.record_event("go_nogo_response",
                                              reaction_time_ms=reaction_ms, success=False,
                                              detail="bad_resource_hit")
                    collected_this_frame = "bad"

                if self.difficulty:
                    self.difficulty.on_go_nogo_result(r.is_good)
                break

        return collected_this_frame

    def _spawn_resource(self):
        good_ratio = self.difficulty.get_good_ratio() if self.difficulty else 0.70
        lifetime = self.difficulty.get_resource_lifetime() if self.difficulty else 10.0

        is_good = random.random() < good_ratio
        x = random.randint(80, MAP_WIDTH - 80)
        y = random.randint(80, MAP_HEIGHT - 80)

        r = Resource(x, y, is_good, time.time(), lifetime)
        self.resources.append(r)
        self.tracker.record_event("resource_spawned",
                                  detail=f"{'good' if is_good else 'bad'} pos=({x},{y})")

    def render(self, surface, cam_x, cam_y):
        now = time.time()

        # Draw collection effects
        for x, y, t, is_good in self.collect_effect:
            elapsed = now - t
            alpha = int(255 * (1 - elapsed * 2))
            if alpha <= 0:
                continue
            sx = int(x - cam_x)
            sy = int(y - cam_y)
            radius = int(30 + elapsed * 50)
            effect_surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
            color = (*COLOR_GOOD_RESOURCE, alpha) if is_good else (*COLOR_BAD_RESOURCE, alpha)
            pygame.draw.circle(effect_surf, color, (radius, radius), radius, 3)
            surface.blit(effect_surf, (sx - radius, sy - radius))

        # Draw resources
        for r in self.resources:
            if not r.alive:
                continue
            sx = int(r.x - cam_x)
            sy = int(r.y - cam_y)

            pulse = 1.0 + 0.15 * math.sin(now * 3 + r.pulse_phase)
            radius = int(GOOD_RESOURCE_RADIUS * pulse)

            age = now - r.spawn_time
            life_pct = 1 - (age / r.lifetime)

            if r.is_good:
                # Green circle with checkmark
                glow_surf = pygame.Surface((radius * 2 + 20, radius * 2 + 20), pygame.SRCALPHA)
                pygame.draw.circle(glow_surf, (*COLOR_GOOD_RESOURCE, 40),
                                  (radius + 10, radius + 10), radius + 8)
                surface.blit(glow_surf, (sx - radius - 10, sy - radius - 10))
                pygame.draw.circle(surface, COLOR_GOOD_RESOURCE, (sx, sy), radius)
                pygame.draw.circle(surface, (255, 255, 255), (sx, sy), radius, 2)
                # Checkmark
                cs = int(radius * 0.5)
                pygame.draw.line(surface, (255, 255, 255),
                               (sx - cs, sy), (sx - cs // 2, sy + cs // 2), 3)
                pygame.draw.line(surface, (255, 255, 255),
                               (sx - cs // 2, sy + cs // 2), (sx + cs, sy - cs // 2), 3)
            else:
                # Red circle with X
                glow_surf = pygame.Surface((radius * 2 + 20, radius * 2 + 20), pygame.SRCALPHA)
                pygame.draw.circle(glow_surf, (*COLOR_BAD_RESOURCE, 40),
                                  (radius + 10, radius + 10), radius + 8)
                surface.blit(glow_surf, (sx - radius - 10, sy - radius - 10))
                pygame.draw.circle(surface, COLOR_BAD_RESOURCE, (sx, sy), radius)
                pygame.draw.circle(surface, (255, 255, 255), (sx, sy), radius, 2)
                # X mark
                half = int(radius * 0.5)
                pygame.draw.line(surface, (255, 255, 255),
                               (sx - half, sy - half), (sx + half, sy + half), 3)
                pygame.draw.line(surface, (255, 255, 255),
                               (sx + half, sy - half), (sx - half, sy + half), 3)

            # Life bar
            bar_width = radius * 2
            bar_height = 4
            bar_x = sx - bar_width // 2
            bar_y = sy + radius + 8
            pygame.draw.rect(surface, (60, 60, 60), (bar_x, bar_y, bar_width, bar_height))
            life_width = int(bar_width * max(0, life_pct))
            color = (100, 255, 100) if life_pct > 0.3 else (255, 100, 100)
            pygame.draw.rect(surface, color, (bar_x, bar_y, life_width, bar_height))
