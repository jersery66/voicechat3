import pygame
import math
import time
from game.config import (
    PLAYER_RADIUS, PLAYER_SPEED, HEALTH_MAX, MAP_WIDTH, MAP_HEIGHT,
    COLOR_PLAYER, COLOR_PLAYER_GLOW, COLOR_TEXT
)


class Player:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.health = HEALTH_MAX
        self.radius = PLAYER_RADIUS
        self.speed = PLAYER_SPEED
        self.invincible_timer = 0
        self.damage_flash = 0
        self.glow_pulse = 0

    def update(self, dt, keys):
        dx, dy = 0, 0
        if keys[pygame.K_LEFT] or keys[pygame.K_a]:
            dx -= 1
        if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
            dx += 1
        if keys[pygame.K_UP] or keys[pygame.K_w]:
            dy -= 1
        if keys[pygame.K_DOWN] or keys[pygame.K_s]:
            dy += 1

        if dx != 0 and dy != 0:
            dx *= 0.707
            dy *= 0.707

        self.x += dx * self.speed * dt * 60
        self.y += dy * self.speed * dt * 60

        self.x = max(self.radius, min(MAP_WIDTH - self.radius, self.x))
        self.y = max(self.radius, min(MAP_HEIGHT - self.radius, self.y))

        if self.invincible_timer > 0:
            self.invincible_timer -= dt
        if self.damage_flash > 0:
            self.damage_flash -= dt

        self.glow_pulse += dt * 3

    def take_damage(self, amount):
        if self.invincible_timer > 0:
            return False
        self.health -= amount
        self.invincible_timer = 1.0
        self.damage_flash = 0.3
        return True

    def render(self, surface, cam_x, cam_y):
        sx = int(self.x - cam_x)
        sy = int(self.y - cam_y)

        # Glow effect (pulsing outer ring)
        glow_radius = self.radius + 8 + int(4 * math.sin(self.glow_pulse))
        glow_alpha = int(60 + 30 * math.sin(self.glow_pulse))
        glow_surf = pygame.Surface((glow_radius * 2 + 4, glow_radius * 2 + 4), pygame.SRCALPHA)
        pygame.draw.circle(glow_surf, (*COLOR_PLAYER_GLOW[:3], glow_alpha),
                          (glow_radius + 2, glow_radius + 2), glow_radius)
        surface.blit(glow_surf, (sx - glow_radius - 2, sy - glow_radius - 2))

        # Direction indicator (small arrow showing last movement direction)
        # This is always visible to help player orient

        # Main body
        if self.damage_flash > 0 and int(self.damage_flash * 10) % 2 == 0:
            color = (255, 100, 100)
        else:
            color = COLOR_PLAYER

        if self.invincible_timer > 0 and int(self.invincible_timer * 5) % 2 == 0:
            return

        # Draw player body
        pygame.draw.circle(surface, color, (sx, sy), self.radius)
        pygame.draw.circle(surface, (255, 255, 255), (sx, sy), self.radius, 3)

        # Draw "YOU" text above player
        try:
            font = pygame.font.SysFont("microsoftyahei", 14, bold=True)
            text = font.render("你", True, COLOR_TEXT)
            text_rect = text.get_rect(center=(sx, sy - self.radius - 15))
            # Shadow
            shadow = font.render("你", True, (0, 0, 0))
            surface.blit(shadow, (text_rect.x + 1, text_rect.y + 1))
            surface.blit(text, text_rect)
        except:
            pass
