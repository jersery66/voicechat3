import pygame
import time
import math
from game.config import (
    MAP_WIDTH, MAP_HEIGHT, COLOR_CAMP_STRUCTURE
)


class CampSystem:
    """Delayed gratification: resources can be spent to build camp structures.
    Now with 5 tiers and delayed gratification choice."""

    # Camp tiers configuration
    CAMP_TIERS = {
        1: {"name": "篝火", "cost": 3, "desc": "照亮周围"},
        2: {"name": "帐篷", "cost": 6, "desc": "提供庇护"},
        3: {"name": "小屋", "cost": 10, "desc": "恢复生命"},
        4: {"name": "花园", "cost": 15, "desc": "吸引资源"},
        5: {"name": "家园", "cost": 20, "desc": "完成建设"},
    }

    def __init__(self, tracker):
        self.tracker = tracker
        self.structures = []
        self.build_offered = False
        self.pending_offer = None
        self.highest_built_tier = 0
        self.skip_count = 0  # Track delayed gratification choices

    def check_build_offer(self, collected_resources):
        if self.build_offered:
            return

        # Find the next tier to offer
        next_tier = self.highest_built_tier + 1
        if next_tier > 5:
            return  # All tiers built

        tier_info = self.CAMP_TIERS[next_tier]
        if collected_resources >= tier_info["cost"]:
            self.pending_offer = {
                "tier": next_tier,
                "cost": tier_info["cost"],
                "name": tier_info["name"],
                "position": (MAP_WIDTH // 2, MAP_HEIGHT // 2)
            }
            self.build_offered = True
            self.tracker.record_event("camp_build_choice_offered",
                                      detail=f"tier={next_tier} name={tier_info['name']} "
                                             f"cost={tier_info['cost']} available={collected_resources}")

    def attempt_build(self, current_resources):
        if not self.build_offered or not self.pending_offer:
            return False, current_resources

        offer = self.pending_offer
        if current_resources >= offer["cost"]:
            self.structures.append({
                "tier": offer["tier"],
                "name": offer["name"],
                "position": offer["position"],
                "built_time": time.time()
            })
            remaining = current_resources - offer["cost"]
            self.highest_built_tier = offer["tier"]
            self.skip_count = 0  # Reset skip count on build

            self.tracker.record_event("camp_build",
                                      success=True,
                                      detail=f"tier={offer['tier']} name={offer['name']} "
                                             f"cost={offer['cost']} remaining={remaining}")

            # Record delayed gratification choice
            self.tracker.record_event("delayed_gratification_choice",
                                      detail=f"chose_build tier={offer['tier']}")

            self.build_offered = False
            self.pending_offer = None
            return True, remaining
        else:
            self.tracker.record_event("camp_build",
                                      success=False,
                                      detail=f"insufficient_resources "
                                             f"needed={offer['cost']} available={current_resources}")
            return False, current_resources

    def skip_build(self):
        if self.build_offered:
            self.skip_count += 1
            self.tracker.record_event("camp_build_skipped",
                                      detail=f"tier={self.pending_offer['tier']} "
                                             f"cost={self.pending_offer['cost']} skip_count={self.skip_count}")
            # Record delayed gratification choice (waiting for higher tier)
            self.tracker.record_event("delayed_gratification_choice",
                                      detail=f"chose_skip tier={self.pending_offer['tier']} "
                                             f"skip_count={self.skip_count}")
            self.build_offered = False
            self.pending_offer = None

    def render_structures(self, surface, cam_x, cam_y):
        for s in self.structures:
            sx = int(s["position"][0] - cam_x)
            sy = int(s["position"][1] - cam_y)
            tier = s["tier"]

            if tier == 1:  # Campfire
                # Fire glow
                glow_surf = pygame.Surface((80, 80), pygame.SRCALPHA)
                glow_alpha = int(100 + 50 * math.sin(time.time() * 3))
                pygame.draw.circle(glow_surf, (255, 150, 50, glow_alpha), (40, 40), 35)
                surface.blit(glow_surf, (sx - 40, sy - 40))
                # Fire
                pygame.draw.circle(surface, (255, 100, 30), (sx, sy), 12)
                pygame.draw.circle(surface, (255, 200, 50), (sx, sy - 5), 8)

            elif tier == 2:  # Tent
                points = [(sx, sy - 35), (sx - 30, sy + 15), (sx + 30, sy + 15)]
                pygame.draw.polygon(surface, (180, 140, 80), points)
                pygame.draw.polygon(surface, (220, 180, 100), points, 2)
                # Door
                pygame.draw.rect(surface, (120, 100, 60), (sx - 8, sy + 5, 16, 10))

            elif tier == 3:  # Cabin
                pygame.draw.rect(surface, (160, 120, 70), (sx - 35, sy - 15, 70, 40))
                roof = [(sx - 40, sy - 15), (sx, sy - 50), (sx + 40, sy - 15)]
                pygame.draw.polygon(surface, (140, 100, 60), roof)
                pygame.draw.rect(surface, (200, 160, 100), (sx - 35, sy - 15, 70, 40), 2)
                # Window
                pygame.draw.rect(surface, (150, 200, 255), (sx - 20, sy - 5, 15, 15))
                pygame.draw.rect(surface, (150, 200, 255), (sx + 5, sy - 5, 15, 15))

            elif tier == 4:  # Garden
                # House base
                pygame.draw.rect(surface, (160, 120, 70), (sx - 35, sy - 15, 70, 40))
                roof = [(sx - 40, sy - 15), (sx, sy - 50), (sx + 40, sy - 15)]
                pygame.draw.polygon(surface, (140, 100, 60), roof)
                # Flowers
                flower_colors = [(255, 100, 100), (255, 200, 50), (200, 100, 255)]
                for i, fx in enumerate([sx - 50, sx - 40, sx + 40, sx + 50]):
                    fy = sy + 20
                    color = flower_colors[i % len(flower_colors)]
                    pygame.draw.circle(surface, color, (fx, fy), 5)
                    pygame.draw.line(surface, (100, 200, 100), (fx, fy), (fx, fy + 10), 2)

            elif tier == 5:  # Home
                # Full house
                pygame.draw.rect(surface, (180, 140, 80), (sx - 40, sy - 20, 80, 50))
                roof = [(sx - 45, sy - 20), (sx, sy - 60), (sx + 45, sy - 20)]
                pygame.draw.polygon(surface, (160, 100, 60), roof)
                # Chimney
                pygame.draw.rect(surface, (120, 100, 80), (sx + 25, sy - 55, 15, 25))
                # Smoke
                smoke_alpha = int(100 + 50 * math.sin(time.time() * 2))
                smoke_surf = pygame.Surface((30, 30), pygame.SRCALPHA)
                pygame.draw.circle(smoke_surf, (200, 200, 200, smoke_alpha), (15, 15), 10)
                surface.blit(smoke_surf, (sx + 25, sy - 75))
                # Door and windows
                pygame.draw.rect(surface, (100, 80, 50), (sx - 10, sy + 10, 20, 20))
                pygame.draw.rect(surface, (150, 200, 255), (sx - 30, sy - 10, 15, 15))
                pygame.draw.rect(surface, (150, 200, 255), (sx + 15, sy - 10, 15, 15))

    def render_build_prompt(self, surface, cam_x, cam_y):
        if not self.build_offered or not self.pending_offer:
            return

        pos = self.pending_offer["position"]
        sx = int(pos[0] - cam_x)
        sy = int(pos[1] - cam_y - 80)

        try:
            font = pygame.font.SysFont("microsoftyahei", 20, bold=True)
            font_small = pygame.font.SysFont("microsoftyahei", 16)
        except:
            font = pygame.font.Font(None, 20)
            font_small = pygame.font.Font(None, 16)

        # Pulsing diamond icon
        pulse = 0.8 + 0.2 * math.sin(time.time() * 4)
        size = int(20 * pulse)

        points = [(sx, sy - size), (sx + size, sy),
                  (sx, sy + size), (sx - size, sy)]
        icon_surf = pygame.Surface((size * 2 + 4, size * 2 + 4), pygame.SRCALPHA)
        offset_points = [(p[0] - sx + size + 2, p[1] - sy + size + 2) for p in points]
        pygame.draw.polygon(icon_surf, (255, 215, 0, int(200 * pulse)), offset_points)
        surface.blit(icon_surf, (sx - size - 2, sy - size - 2))

        # Build prompt text
        tier = self.pending_offer["tier"]
        name = self.pending_offer["name"]
        cost = self.pending_offer["cost"]

        prompt_text = f"按 B 建造 {name} (消耗 {cost} 资源)"
        prompt_surf = font.render(prompt_text, True, (255, 215, 0))
        prompt_rect = prompt_surf.get_rect(center=(sx, sy + size + 20))
        surface.blit(prompt_surf, prompt_rect)

        # Skip option
        skip_text = "按 N 继续积攒 (等待更高级)"
        skip_surf = font_small.render(skip_text, True, (200, 200, 200))
        skip_rect = skip_surf.get_rect(center=(sx, sy + size + 45))
        surface.blit(skip_surf, skip_rect)
