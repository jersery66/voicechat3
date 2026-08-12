import pygame
import time
import math
import ctypes
import logging
from game.config import (
    FPS, GAME_DURATION_SECONDS, MAP_WIDTH, MAP_HEIGHT,
    HEALTH_MAX, HEALTH_LOSS_BAD_PICKUP, COLOR_HEALTH_BAR,
    COLOR_HEALTH_BG, COLOR_PLAYER, COLOR_PAUSE_OVERLAY,
    COLOR_TEXT, TUTORIAL_DURATION
)
from game.clinical_tracker import ClinicalTracker
from game.entities.player import Player
from game.systems.resource_system import ResourceSystem
from game.systems.storm_system import StormSystem
from game.systems.camp_system import CampSystem
from game.systems.difficulty_system import DifficultySystem
from game.systems.background_system import BackgroundSystem

logger = logging.getLogger(__name__)


def disable_ime():
    """Disable IME on Windows to prevent the keyboard being intercepted.

    No-op on non-Windows platforms or when ``imm32`` is not available.
    """
    try:
        hwnd = pygame.display.get_wm_info().get('window')
        if hwnd:
            ctypes.windll.imm32.ImmDisableIME(0)
    except Exception as e:
        logger.info(f"Could not disable IME: {e}")


class GameEngine:
    """Main game loop. Runs fullscreen pygame."""

    def __init__(self, tracker: ClinicalTracker):
        self.tracker = tracker
        self.running = True
        self.clock = None
        self.screen = None
        self.screen_width = 0
        self.screen_height = 0
        self.state = "tutorial"  # tutorial, playing, breathing, ending, game_over
        self.game_start_time = 0
        self.tutorial_start_time = 0
        self.player = None
        self.camera_x = 0
        self.camera_y = 0
        self._exit_reason = "unknown"
        self.resource_sys = None
        self.storm_sys = None
        self.camp_sys = None
        self.font_large = None
        self.font_medium = None
        self.font_small = None
        self.collect_feedback = None  # (text, time, is_good)
        self._game_over_start_time = 0
        self._fade_alpha = 0  # For ending animation
        # Tutorial render cache (avoids re-rendering ~14 surfaces per frame)
        self._tutorial_cache = None
        # Cached label surfaces for the tutorial example icons
        self._tutorial_labels = None

    def run(self) -> dict:
        """Run the main game loop. Returns the tracker summary metrics.

        ``run()`` is idempotent with respect to pygame initialization — we
        only init the modules we use (display + font) and only quit those
        sub-systems on exit, so launching the game a second time inside the
        same Python process keeps the rest of the host application alive.
        """
        if not pygame.display.get_init():
            pygame.display.init()
        if not pygame.font.get_init():
            pygame.font.init()
        pygame.mouse.set_visible(True)

        self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN | pygame.NOFRAME)
        self.screen_width, self.screen_height = self.screen.get_size()
        pygame.display.set_caption("心理互动游戏")

        disable_ime()

        try:
            self.font_large = pygame.font.SysFont("microsoftyahei", 48, bold=True)
            self.font_medium = pygame.font.SysFont("microsoftyahei", 28)
            self.font_small = pygame.font.SysFont("microsoftyahei", 20)
        except Exception:
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
                dt = self.clock.tick(FPS) / 1000.0
                self._handle_events()
                self._update(dt)
                self._render()
        except Exception as e:
            logger.error(f"Game engine error: {e}", exc_info=True)
        finally:
            try:
                self.tracker.record_event(
                    "game_end", detail=f"reason={self._exit_reason}"
                )
                self.tracker.save_csv()
            except Exception as e:
                logger.warning(f"Failed to finalize tracker: {e}")
            # Only quit display+font; leave global pygame state alone so the
            # game can be relaunched in the same process.
            try:
                pygame.display.quit()
            except Exception:
                pass
            try:
                pygame.font.quit()
            except Exception:
                pass

        return self.tracker.get_summary_metrics()

    def _init_game_objects(self):
        self.player = Player(MAP_WIDTH // 2, MAP_HEIGHT // 2)
        self.difficulty_sys = DifficultySystem()
        self.background_sys = BackgroundSystem()
        self.resource_sys = ResourceSystem(self.tracker, self.difficulty_sys)
        self.storm_sys = StormSystem(self.tracker)
        self.camp_sys = CampSystem(self.tracker)

    def _handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self._exit_reason = "quit"
                self.running = False
                return

            if event.type == pygame.KEYDOWN:
                ctrl = event.mod & (pygame.KMOD_LCTRL | pygame.KMOD_RCTRL)
                alt = event.mod & (pygame.KMOD_LALT | pygame.KMOD_RALT)
                if ctrl and alt and event.key == pygame.K_q:
                    self._exit_reason = "backdoor"
                    self.running = False
                    return
                if event.key == pygame.K_ESCAPE:
                    if self.state == "playing" or self.state == "breathing":
                        self._exit_reason = "player_end"
                        self.state = "ending"
                        self._fade_alpha = 0
                        continue
                    elif self.state == "game_over":
                        if time.time() - self._game_over_start_time >= 3:
                            self.running = False
                        continue
                    else:
                        self._exit_reason = "escape"
                        self.running = False
                        return

                if self.state == "tutorial":
                    self.state = "playing"
                    self.game_start_time = time.time()
                    continue

                if self.state == "game_over":
                    if time.time() - self._game_over_start_time >= 3:
                        self.running = False
                    continue

                if self.state == "breathing":
                    self.storm_sys.handle_keypress(event)
                elif self.state == "playing":
                    if event.key == pygame.K_b:
                        success, new_count = self.camp_sys.attempt_build(
                            self.resource_sys.collected_good)
                        if success:
                            self.resource_sys.collected_good = new_count
                    elif event.key == pygame.K_n:
                        self.camp_sys.skip_build()

            if event.type == pygame.KEYUP:
                if self.state == "breathing":
                    self.storm_sys.handle_keyrelease(event)

    def _update(self, dt):
        if self.state == "tutorial":
            elapsed = time.time() - self.tutorial_start_time
            if elapsed >= TUTORIAL_DURATION:
                self.state = "playing"
                self.game_start_time = time.time()
            return

        if self.state == "ending":
            self._fade_alpha = min(255, self._fade_alpha + dt * 200)
            if self._fade_alpha >= 255:
                self.state = "game_over"
                self._game_over_start_time = time.time()
            return

        elapsed = time.time() - self.game_start_time
        if elapsed >= GAME_DURATION_SECONDS and self.state != "game_over":
            self._exit_reason = "timeout"
            self.state = "ending"
            self._fade_alpha = 0
            return

        self.background_sys.update_tier(self.camp_sys.highest_built_tier)
        self.background_sys.update(dt)

        if self.state == "playing":
            keys = pygame.key.get_pressed()
            self.player.update(dt, keys)

            self.camera_x = self.player.x - self.screen_width // 2
            self.camera_y = self.player.y - self.screen_height // 2
            self.camera_x = max(0, min(MAP_WIDTH - self.screen_width, self.camera_x))
            self.camera_y = max(0, min(MAP_HEIGHT - self.screen_height, self.camera_y))

            storm_active = self.storm_sys.update(dt)
            if storm_active:
                self.state = "breathing"
                return

            collision = self.resource_sys.update(
                dt, self.player.x, self.player.y, self.player.radius)

            if collision == "bad":
                if self.player.take_damage(HEALTH_LOSS_BAD_PICKUP):
                    self.tracker.record_event("player_damage",
                                              detail=f"bad_resource health={self.player.health}")
                    self.collect_feedback = ("受伤! -" + str(HEALTH_LOSS_BAD_PICKUP) + " 生命", time.time(), False)
                    if self.player.health <= 0:
                        self.state = "game_over"
                        self._exit_reason = "game_over"
                        self._game_over_start_time = time.time()
                        self.tracker.record_event("game_over", detail="health_zero")
            elif collision == "good":
                self.collect_feedback = ("收集成功! +1 资源", time.time(), True)

            self.camp_sys.check_build_offer(self.resource_sys.collected_good)

        elif self.state == "breathing":
            self.storm_sys.update(dt)
            if not self.storm_sys.active:
                health_restore = self.storm_sys.get_pending_health_restore()
                if health_restore > 0:
                    self.player.health = min(HEALTH_MAX, self.player.health + health_restore)
                    self.collect_feedback = (f"呼吸练习完成! +{health_restore} 生命", time.time(), True)
                self.state = "playing"

    def _render(self):
        self.background_sys.render(self.screen, self.screen_width, self.screen_height, self.camera_y)

        if self.state == "tutorial":
            self._render_tutorial()
        elif self.state == "playing":
            self.background_sys.render_ambient_particles(
                self.screen, self.screen_width, self.screen_height, self.camp_sys.highest_built_tier)
            self.resource_sys.render(self.screen, self.camera_x, self.camera_y)
            self.camp_sys.render_structures(self.screen, self.camera_x, self.camera_y)
            self.camp_sys.render_build_prompt(self.screen, self.camera_x, self.camera_y)
            self.player.render(self.screen, self.camera_x, self.camera_y)
            self._render_hud()
            self._render_collect_feedback()
        elif self.state == "breathing":
            self.background_sys.render_ambient_particles(
                self.screen, self.screen_width, self.screen_height, self.camp_sys.highest_built_tier)
            self.resource_sys.render(self.screen, self.camera_x, self.camera_y)
            self.camp_sys.render_structures(self.screen, self.camera_x, self.camera_y)
            self.player.render(self.screen, self.camera_x, self.camera_y)
            self.storm_sys.render(self.screen, self.screen_width, self.screen_height)
            self._render_hud()
        elif self.state == "ending":
            # Show game scene fading to black
            self.background_sys.render_ambient_particles(
                self.screen, self.screen_width, self.screen_height, self.camp_sys.highest_built_tier)
            self.resource_sys.render(self.screen, self.camera_x, self.camera_y)
            self.camp_sys.render_structures(self.screen, self.camera_x, self.camera_y)
            self.player.render(self.screen, self.camera_x, self.camera_y)
            self._render_hud()
            # Fade overlay
            fade_surf = pygame.Surface((self.screen_width, self.screen_height))
            fade_surf.fill((0, 0, 0))
            fade_surf.set_alpha(int(self._fade_alpha))
            self.screen.blit(fade_surf, (0, 0))
        elif self.state == "game_over":
            self._render_game_over()

        pygame.display.flip()

    def _render_tutorial(self):
        """Render the static tutorial overlay + animated example icons.

        The static portions (background overlay, title, instruction lines and
        the icon labels) are rendered once and cached — only the pulsing
        “you” circle radius needs per-frame recomputation.
        """
        cx, cy = self.screen_width // 2, self.screen_height // 2

        if self._tutorial_cache is None:
            cache = pygame.Surface(
                (self.screen_width, self.screen_height), pygame.SRCALPHA
            )
            # Dim background
            overlay = pygame.Surface(
                (self.screen_width, self.screen_height), pygame.SRCALPHA
            )
            overlay.fill((0, 0, 0, 200))
            cache.blit(overlay, (0, 0))

            title = self.font_large.render("心理互动游戏", True, (100, 200, 255))
            cache.blit(title, title.get_rect(center=(cx, cy - 200)))

            ime_warning = self.font_medium.render(
                "请先切换到英文输入法！(按 Ctrl+Space 切换)",
                True, (255, 100, 100)
            )
            cache.blit(ime_warning, ime_warning.get_rect(center=(cx, cy - 150)))

            instructions = [
                ("操作方式:", (255, 215, 0)),
                ("WASD 或 方向键 移动角色", COLOR_TEXT),
                ("", COLOR_TEXT),
                ("游戏目标:", (255, 215, 0)),
                ("收集 绿色✓ 资源 (靠近即可收集)", (100, 255, 100)),
                ("避开 红色✗ 资源 (会减少生命值)", (255, 100, 100)),
                ("", COLOR_TEXT),
                ("特殊事件:", (255, 215, 0)),
                ("蓝色风暴来临时，按 空格键 进行呼吸练习", (150, 150, 255)),
                ("收集足够资源后，按 B 建造营地", (255, 200, 100)),
                ("", COLOR_TEXT),
                ("按 ESC 可随时结束游戏并查看总结", (180, 180, 180)),
                ("按任意键开始游戏...", (200, 200, 200)),
            ]
            y_offset = cy - 120
            for text, color in instructions:
                if text:
                    rendered = self.font_medium.render(text, True, color)
                    cache.blit(rendered, rendered.get_rect(center=(cx, y_offset)))
                y_offset += 35

            self._tutorial_cache = cache
            # Pre-render the small labels under the example icons
            label_you = self.font_small.render("你", True, COLOR_TEXT)
            label_collect = self.font_small.render("收集", True, (100, 255, 100))
            label_avoid = self.font_small.render("避开", True, (255, 100, 100))
            self._tutorial_labels = (label_you, label_collect, label_avoid)

        # Static cache (cheap blit)
        self.screen.blit(self._tutorial_cache, (0, 0))

        # Animated example icons (only the radius pulses)
        pulse = 0.8 + 0.2 * math.sin(time.time() * 3)
        example_y = cy + 120
        radius = int(20 * pulse)

        pygame.draw.circle(self.screen, COLOR_PLAYER, (cx - 150, example_y), radius)
        pygame.draw.circle(self.screen, (255, 255, 255), (cx - 150, example_y), radius, 3)
        label_you, label_collect, label_avoid = self._tutorial_labels
        self.screen.blit(label_you, label_you.get_rect(center=(cx - 150, example_y - 30)))

        pygame.draw.circle(self.screen, (100, 255, 100), (cx, example_y), 25)
        pygame.draw.circle(self.screen, (255, 255, 255), (cx, example_y), 25, 2)
        self.screen.blit(label_collect, label_collect.get_rect(center=(cx, example_y - 30)))

        pygame.draw.circle(self.screen, (255, 60, 60), (cx + 150, example_y), 25)
        pygame.draw.circle(self.screen, (255, 255, 255), (cx + 150, example_y), 25, 2)
        half = 15
        pygame.draw.line(self.screen, (255, 255, 255),
                         (cx + 150 - half, example_y - half),
                         (cx + 150 + half, example_y + half), 3)
        pygame.draw.line(self.screen, (255, 255, 255),
                         (cx + 150 + half, example_y - half),
                         (cx + 150 - half, example_y + half), 3)
        self.screen.blit(label_avoid, label_avoid.get_rect(center=(cx + 150, example_y - 30)))

    def _render_collect_feedback(self):
        if not self.collect_feedback:
            return
        text, t, is_good = self.collect_feedback
        elapsed = time.time() - t
        if elapsed > 1.0:
            return

        color = (100, 255, 100) if is_good else (255, 100, 100)
        sx = int(self.player.x - self.camera_x)
        sy = int(self.player.y - self.camera_y - 50 - elapsed * 30)

        rendered = self.font_medium.render(text, True, color)
        self.screen.blit(rendered, rendered.get_rect(center=(sx, sy)))

    def _render_hud(self):
        bar_x, bar_y = 20, 20
        bar_w, bar_h = 200, 20

        health_label = self.font_small.render("生命值", True, COLOR_TEXT)
        self.screen.blit(health_label, (bar_x, bar_y - 25))

        pygame.draw.rect(self.screen, COLOR_HEALTH_BG, (bar_x, bar_y, bar_w, bar_h))
        health_pct = max(0, self.player.health / HEALTH_MAX)
        pygame.draw.rect(self.screen, COLOR_HEALTH_BAR,
                         (bar_x, bar_y, int(bar_w * health_pct), bar_h))
        pygame.draw.rect(self.screen, (255, 255, 255), (bar_x, bar_y, bar_w, bar_h), 2)

        health_text = f"{int(self.player.health)}%"
        self.screen.blit(self.font_small.render(health_text, True, COLOR_TEXT),
                        (bar_x + bar_w + 10, bar_y))

        resource_label = self.font_small.render(f"资源: {self.resource_sys.collected_good}", True, COLOR_TEXT)
        self.screen.blit(resource_label, (bar_x, bar_y + bar_h + 10))

        # Difficulty level
        diff_text = f"难度: {self.difficulty_sys.get_level_name()} ({self.difficulty_sys.level}/5)"
        diff_surf = self.font_small.render(diff_text, True, (255, 215, 0))
        self.screen.blit(diff_surf, diff_surf.get_rect(center=(self.screen_width // 2, 15)))

        # Time bar
        elapsed = time.time() - self.game_start_time
        remaining = max(0, GAME_DURATION_SECONDS - elapsed)
        time_pct = min(1.0, elapsed / GAME_DURATION_SECONDS)
        time_bar_w = 200
        time_bar_x = self.screen_width - time_bar_w - 20

        time_label = self.font_small.render(f"剩余时间: {int(remaining)}秒", True, COLOR_TEXT)
        self.screen.blit(time_label, (time_bar_x, 0))

        pygame.draw.rect(self.screen, COLOR_HEALTH_BG, (time_bar_x, 25, time_bar_w, 12))
        pygame.draw.rect(self.screen, (100, 180, 255), (time_bar_x, 25, int(time_bar_w * time_pct), 12))
        pygame.draw.rect(self.screen, (255, 255, 255), (time_bar_x, 25, time_bar_w, 12), 1)

        # Camp tier
        camp_tier = self.camp_sys.highest_built_tier
        if camp_tier > 0:
            camp_text = f"营地: {self.camp_sys.CAMP_TIERS[camp_tier]['name']}"
        else:
            camp_text = "营地: 未建造"
        self.screen.blit(self.font_small.render(camp_text, True, (255, 200, 100)),
                        (self.screen_width - 150, self.screen_height - 30))

        # Controls hint
        self.screen.blit(self.font_small.render("WASD移动 | B建造 | 空格呼吸 | ESC结束", True, (150, 150, 150)),
                        (20, self.screen_height - 30))

    def _render_game_over(self):
        overlay = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        overlay.fill((*COLOR_PAUSE_OVERLAY, 210))
        self.screen.blit(overlay, (0, 0))

        cx, cy = self.screen_width // 2, self.screen_height // 2

        if self._exit_reason == "timeout":
            title_text = "家园重建完成"
            title_color = (100, 255, 100)
        else:
            title_text = "旅程暂告一段落"
            title_color = (255, 200, 100)

        title = self.font_large.render(title_text, True, title_color)
        self.screen.blit(title, title.get_rect(center=(cx, cy - 200)))

        camp_tier = self.camp_sys.highest_built_tier
        if camp_tier >= 5:
            msg = "你成功建造了完整的家园，新的生活从这里开始"
        elif camp_tier >= 3:
            msg = "你建造了温暖的庇护所，每一块砖都是希望的象征"
        elif camp_tier >= 1:
            msg = "篝火的光芒照亮了前路，重建才刚刚开始"
        else:
            msg = "重建的道路漫长，但你已经迈出了第一步"

        msg_surf = self.font_medium.render(msg, True, (200, 200, 200))
        self.screen.blit(msg_surf, msg_surf.get_rect(center=(cx, cy - 160)))

        # Game stats
        y_offset = cy - 110
        section_title = self.font_small.render("── 游戏表现 ──", True, (180, 180, 200))
        self.screen.blit(section_title, section_title.get_rect(center=(cx, y_offset)))
        y_offset += 30

        if camp_tier > 0:
            camp_name = self.camp_sys.CAMP_TIERS[camp_tier]['name']
            camp_display = f"{camp_tier}/5 ({camp_name})"
        else:
            camp_display = "0/5 (未建造)"

        game_stats = [
            (f"收集物资: {self.resource_sys.collected_good} 个", (100, 255, 100)),
            (f"达到难度: {self.difficulty_sys.get_level_name()}", (255, 200, 100)),
            (f"建造进度: {camp_display}", (200, 150, 255)),
            (f"剩余生命: {int(self.player.health)}%",
             (255, 100, 100) if self.player.health < 50 else (100, 255, 100)),
        ]
        for text, color in game_stats:
            stat_surf = self.font_small.render(text, True, color)
            self.screen.blit(stat_surf, stat_surf.get_rect(center=(cx, y_offset)))
            y_offset += 28

        # Breathing stats
        y_offset += 15
        section_title = self.font_small.render("── 呼吸练习 ──", True, (180, 180, 200))
        self.screen.blit(section_title, section_title.get_rect(center=(cx, y_offset)))
        y_offset += 30

        cycle_count = self.storm_sys.cycle_count
        target = self.storm_sys.target_cycles
        rate = int(cycle_count / max(target, 1) * 100)
        breathing_stats = [
            (f"完成次数: {cycle_count} 次", (150, 200, 255)),
            (f"完成率: {cycle_count}/{target} ({rate}%)", (150, 200, 255)),
        ]
        for text, color in breathing_stats:
            stat_surf = self.font_small.render(text, True, color)
            self.screen.blit(stat_surf, stat_surf.get_rect(center=(cx, y_offset)))
            y_offset += 25

        # Final message
        y_offset += 25
        final_surf = self.font_small.render("数据已保存，感谢你的参与", True, (150, 150, 150))
        self.screen.blit(final_surf, final_surf.get_rect(center=(cx, y_offset)))

        # Exit hint (show after 3 seconds)
        elapsed_since_end = time.time() - self._game_over_start_time
        if elapsed_since_end >= 3:
            hint = self.font_medium.render("按任意键继续", True, (200, 200, 200))
            hint.set_alpha(int(180 + 75 * math.sin(time.time() * 3)))
            self.screen.blit(hint, hint.get_rect(center=(cx, cy + 200)))
        else:
            countdown = int(3 - elapsed_since_end) + 1
            hint = self.font_medium.render(f"{countdown} 秒后可继续...", True, (150, 150, 150))
            self.screen.blit(hint, hint.get_rect(center=(cx, cy + 200)))
