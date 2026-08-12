# Therapeutic Game Configuration
# All tunable parameters in one place

# Display
FULLSCREEN = True
FPS = 60
BACKGROUND_COLOR = (20, 30, 40)  # Dark blue-grey

# Tutorial duration
TUTORIAL_DURATION = 10.0  # Show tutorial for 10 seconds

# Game duration
GAME_DURATION_SECONDS = 480       # 8 minutes
STORM_MIN_INTERVAL = 30           # Seconds between storm events
STORM_MAX_INTERVAL = 60
BREATHING_CYCLE_SECONDS = 19      # 4-in + 7-hold + 8-out

# Go/No-Go parameters
RESOURCE_SPAWN_INTERVAL = 2.0     # Seconds between spawns (slower)
GOOD_RESOURCE_RADIUS = 25         # Larger for visibility
BAD_RESOURCE_RADIUS = 25          # Larger for visibility
PLAYER_SPEED = 5
PLAYER_RADIUS = 20
MAX_RESOURCES_ON_SCREEN = 6       # Fewer on screen
HEALTH_MAX = 100
HEALTH_LOSS_BAD_PICKUP = 20       # Less punishing
GOOD_RESOURCE_RATIO = 0.70        # 70% good, 30% bad
RESOURCE_LIFETIME = 10.0          # Longer lifetime

# Colors (RGB tuples)
COLOR_PLAYER = (100, 200, 255)        # Bright blue
COLOR_PLAYER_GLOW = (50, 150, 255, 80) # Blue glow
COLOR_GOOD_RESOURCE = (80, 255, 80)   # Bright green
COLOR_BAD_RESOURCE = (255, 60, 60)    # Bright red
COLOR_STORM_OVERLAY = (40, 40, 80)    # Blue-purple (used with alpha)
COLOR_BREATH_CIRCLE = (200, 200, 255) # Light blue
COLOR_CAMP_STRUCTURE = (255, 200, 100) # Gold
COLOR_HEALTH_BAR = (80, 220, 80)      # Green health bar
COLOR_HEALTH_BG = (60, 60, 60)        # Dark grey
COLOR_RESOURCE_COUNTER = (255, 215, 0) # Gold dots
COLOR_PAUSE_OVERLAY = (0, 0, 0)       # Black (used with alpha)
COLOR_MAP_GRID = (30, 40, 50)         # Subtle grid
COLOR_TEXT = (255, 255, 255)           # White text
COLOR_TEXT_SHADOW = (0, 0, 0)         # Black shadow

# Map
MAP_WIDTH = 1600
MAP_HEIGHT = 1000
