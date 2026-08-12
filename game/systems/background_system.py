"""
Background Visual System.
Dynamic background that evolves as the player builds camps - from grey/dark to bright/warm.
"""

import pygame
import math


class BackgroundSystem:
    """Manages dynamic background that evolves with camp progress."""

    # Color palettes for each camp tier (0=none, 1-5=camp tiers)
    SKY_COLORS = [
        # (top_color, bottom_color, grid_color) - Tier 0: No camp
        ((30, 35, 45), (20, 25, 35), (35, 40, 50)),
        # Tier 1: Campfire - slightly warmer
        ((35, 40, 55), (25, 30, 40), (40, 45, 55)),
        # Tier 2: Tent - hint of warmth
        ((45, 50, 65), (30, 35, 50), (50, 55, 65)),
        # Tier 3: Cabin - warmer tones
        ((55, 60, 75), (40, 45, 60), (60, 65, 75)),
        # Tier 4: Garden - nature colors
        ((65, 75, 85), (45, 55, 65), (70, 80, 70)),
        # Tier 5: Home - warm and bright
        ((80, 90, 100), (55, 65, 75), (80, 90, 80)),
    ]

    GROUND_COLORS = [
        # Tier 0: Dark, barren
        ((25, 30, 20), (35, 40, 30)),
        # Tier 1: Slightly less barren
        ((30, 35, 25), (40, 45, 35)),
        # Tier 2: Hint of life
        ((35, 45, 30), (50, 55, 40)),
        # Tier 3: Some grass
        ((45, 60, 35), (60, 70, 45)),
        # Tier 4: Lush
        ((55, 75, 40), (70, 85, 50)),
        # Tier 5: Vibrant
        ((65, 90, 45), (80, 100, 55)),
    ]

    def __init__(self):
        self.current_tier = 0
        self.target_tier = 0
        self.transition_progress = 1.0  # 1.0 = fully transitioned
        self.stars = self._generate_stars(50)
        self.clouds = self._generate_clouds(5)
        self.grass_particles = []

    def _generate_stars(self, count):
        """Generate random star positions."""
        import random
        stars = []
        for _ in range(count):
            x = random.randint(0, 1920)
            y = random.randint(0, 300)
            brightness = random.randint(100, 255)
            twinkle_speed = random.uniform(1, 3)
            stars.append((x, y, brightness, twinkle_speed))
        return stars

    def _generate_clouds(self, count):
        """Generate cloud positions."""
        import random
        clouds = []
        for _ in range(count):
            x = random.randint(-200, 1920)
            y = random.randint(50, 250)
            width = random.randint(100, 250)
            speed = random.uniform(5, 15)
            clouds.append([x, y, width, speed])
        return clouds

    def update_tier(self, new_tier):
        """Update target tier for transition."""
        if new_tier != self.target_tier:
            self.target_tier = new_tier
            self.transition_progress = 0.0

    def update(self, dt):
        """Update background animations."""
        # Smooth transition between tiers
        if self.transition_progress < 1.0:
            self.transition_progress = min(1.0, self.transition_progress + dt * 0.5)
            if self.transition_progress >= 1.0:
                self.current_tier = self.target_tier

        # Move clouds
        for cloud in self.clouds:
            cloud[0] += cloud[3] * dt
            if cloud[0] > 2100:
                cloud[0] = -300

    def _lerp_color(self, color1, color2, t):
        """Linearly interpolate between two colors."""
        return tuple(int(c1 + (c2 - c1) * t) for c1, c2 in zip(color1, color2))

    def render(self, surface, screen_w, screen_h, camera_y=0):
        """Render the dynamic background."""
        # Calculate current colors based on transition
        if self.transition_progress < 1.0:
            t = self.transition_progress
            sky_top = self._lerp_color(
                self.SKY_COLORS[self.current_tier][0],
                self.SKY_COLORS[self.target_tier][0], t)
            sky_bottom = self._lerp_color(
                self.SKY_COLORS[self.current_tier][1],
                self.SKY_COLORS[self.target_tier][1], t)
            ground_top = self._lerp_color(
                self.GROUND_COLORS[self.current_tier][0],
                self.GROUND_COLORS[self.target_tier][0], t)
            ground_bottom = self._lerp_color(
                self.GROUND_COLORS[self.current_tier][1],
                self.GROUND_COLORS[self.target_tier][1], t)
        else:
            sky_top = self.SKY_COLORS[self.current_tier][0]
            sky_bottom = self.SKY_COLORS[self.current_tier][1]
            ground_top = self.GROUND_COLORS[self.current_tier][0]
            ground_bottom = self.GROUND_COLORS[self.current_tier][1]

        # Draw sky gradient
        sky_height = int(screen_h * 0.4)
        for y in range(sky_height):
            t = y / sky_height
            color = self._lerp_color(sky_top, sky_bottom, t)
            pygame.draw.line(surface, color, (0, y), (screen_w, y))

        # Draw stars (only visible in early tiers)
        if self.current_tier < 3:
            star_alpha = max(0, 1.0 - self.current_tier * 0.3)
            for x, y, brightness, speed in self.stars:
                twinkle = int(brightness * (0.7 + 0.3 * math.sin(pygame.time.get_ticks() / 1000 * speed)))
                alpha = int(twinkle * star_alpha)
                if alpha > 0:
                    star_surf = pygame.Surface((4, 4), pygame.SRCALPHA)
                    pygame.draw.circle(star_surf, (255, 255, 200, alpha), (2, 2), 2)
                    surface.blit(star_surf, (x % screen_w, y))

        # Draw clouds
        cloud_alpha = max(50, min(150, 50 + self.current_tier * 20))
        for x, y, width, _ in self.clouds:
            cloud_surf = pygame.Surface((width, width // 3), pygame.SRCALPHA)
            pygame.draw.ellipse(cloud_surf, (255, 255, 255, cloud_alpha),
                               (0, 0, width, width // 3))
            pygame.draw.ellipse(cloud_surf, (255, 255, 255, cloud_alpha),
                               (width // 4, -width // 6, width // 2, width // 3))
            surface.blit(cloud_surf, (int(x) % screen_w, y))

        # Draw ground gradient
        ground_start = sky_height
        ground_height = screen_h - ground_start
        for y in range(ground_height):
            t = y / ground_height
            color = self._lerp_color(ground_top, ground_bottom, t)
            pygame.draw.line(surface, color, (0, ground_start + y), (screen_w, ground_start + y))

        # Draw grid lines (subtle)
        grid_color = self.SKY_COLORS[self.current_tier][2] if self.transition_progress >= 1.0 else \
            self._lerp_color(self.SKY_COLORS[self.current_tier][2],
                            self.SKY_COLORS[self.target_tier][2], self.transition_progress)

        grid_spacing = 100
        grid_alpha = max(30, min(80, 30 + self.current_tier * 10))
        grid_surf = pygame.Surface((screen_w, screen_h), pygame.SRCALPHA)

        # Horizontal grid lines
        for y in range(0, screen_h, grid_spacing):
            adjusted_y = y - int(camera_y) % grid_spacing
            pygame.draw.line(grid_surf, (*grid_color, grid_alpha),
                           (0, adjusted_y), (screen_w, adjusted_y))

        # Vertical grid lines
        for x in range(0, screen_w, grid_spacing):
            pygame.draw.line(grid_surf, (*grid_color, grid_alpha),
                           (x, 0), (x, screen_h))

        surface.blit(grid_surf, (0, 0))

    def render_ambient_particles(self, surface, screen_w, screen_h, camp_tier):
        """Render ambient particles based on camp tier."""
        import random
        import time

        if camp_tier < 2:
            return

        # Fireflies for tier 2+
        if camp_tier >= 2:
            num_particles = min(20, camp_tier * 5)
            for _ in range(num_particles):
                x = random.randint(0, screen_w)
                y = random.randint(0, screen_h)
                alpha = int(100 + 50 * math.sin(time.time() * 2 + x * 0.1))
                size = random.randint(2, 4)
                color = (200, 255, 100, alpha) if camp_tier < 4 else (255, 200, 100, alpha)
                particle_surf = pygame.Surface((size * 2, size * 2), pygame.SRCALPHA)
                pygame.draw.circle(particle_surf, color, (size, size), size)
                surface.blit(particle_surf, (x, y))
