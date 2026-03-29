"""
=============================================================================
 EEG HORROR GAME — Hackathon Prototype
 Engine  : Pygame CE
 EEG I/O : pylsl (Lab Streaming Layer)
 Author  : Generated for 36-hour hackathon
=============================================================================

QUICK-START
-----------
1. Install deps:
       pip install pygame-ce pylsl

2. Run in MOCK mode (default — no EEG hardware needed):
       python eeg_horror_game.py

3. Switch to real Muse EEG:
       Set MOCK_MODE = False near the top of the file.
       Make sure your Muse headband is streaming via BlueMuse / muse-lsl.

KEY BINDINGS (MOCK mode)
-------------------------
   UP arrow   → raise stress
   DOWN arrow → lower stress
   ESC        → quit

ASSET SLOTS
-----------
   Search for "ASSET:" comments to find every placeholder that should be
   replaced with a real .png / .wav file before the final demo.

=============================================================================
"""

# ---------------------------------------------------------------------------
# Standard library
# ---------------------------------------------------------------------------
import sys
import math
import time
import random
import collections
import threading

# ---------------------------------------------------------------------------
# Third-party — graceful fallback if pylsl is not installed
# ---------------------------------------------------------------------------
import pygame

try:
    from pylsl import StreamInlet, resolve_byprop
    LSL_AVAILABLE = True
except ImportError:
    LSL_AVAILABLE = False
    print("[WARN] pylsl not found — forcing MOCK_MODE=True")

# =============================================================================
# GLOBAL CONFIGURATION
# =============================================================================

# ── Toggle this to switch between keyboard simulation and real EEG ──────────
MOCK_MODE = True          # Set False to use Muse / any LSL EEG stream

# ── Window ───────────────────────────────────────────────────────────────────
SCREEN_W, SCREEN_H = 1280, 720
FPS            = 60
TITLE          = "SIGNAL LOST — EEG Horror"

# ── Stress thresholds ────────────────────────────────────────────────────────
STRESS_MED     = 0.35
STRESS_HIGH    = 0.65
STRESS_PANIC   = 0.95

# ── Brainwave monitor (HUD graph) ────────────────────────────────────────────
GRAPH_W, GRAPH_H     = 260, 80
GRAPH_HISTORY_LEN    = GRAPH_W          # one pixel per sample column

# ── Colours ──────────────────────────────────────────────────────────────────
C_BLACK        = (0,   0,   0)
C_WHITE        = (255, 255, 255)
C_RED          = (200, 0,   0)
C_DARK_RED     = (120, 0,   0)
C_GREEN        = (0,   200, 50)
C_BLUE_DARK    = (10,  20,  40)
C_PANEL        = (15,  15,  30)
C_GRAPH_LINE   = (0,   255, 100)
C_GRAPH_BG     = (10,  10,  20)
C_HUD_TEXT     = (200, 220, 255)
C_MONSTER      = (80,  0,   0)
C_PLAYER       = (100, 160, 220)
C_HOUSE_WALL   = (60,  50,  40)
C_HOUSE_WIN    = (180, 200, 120)
C_GROUND       = (30,  25,  20)
C_SKY          = (5,   8,   18)
C_TRANSP       = (0, 0, 0, 0)

# =============================================================================
# STRESS MANAGER
# =============================================================================

class StressManager:
    """
    Dual-input stress provider.

    MOCK_MODE  → stress controlled by UP / DOWN arrow keys (delta per frame).
    MUSE_MODE  → stress derived from a live LSL EEG stream.

    Public API
    ----------
    update(keys, dt) → call every frame; updates self.stress_level
    stress_level     → float in [0.0, 1.0]
    """

    MOCK_DELTA = 0.008       # stress change per frame in mock mode

    def __init__(self, mock_mode: bool = True):
        self.mock_mode    = mock_mode or not LSL_AVAILABLE
        self.stress_level = 0.0          # authoritative stress value

        # ── LSL / EEG internals ──────────────────────────────────────────────
        self._inlet       = None
        self._eeg_buffer  = collections.deque(maxlen=256)  # raw EEG samples
        self._lsl_thread  = None
        self._running     = False

        if not self.mock_mode:
            self._connect_lsl()

    # ── Public ───────────────────────────────────────────────────────────────

    def update(self, keys, dt: float):
        if self.mock_mode:
            self._update_mock(keys)
        else:
            self._update_eeg()

    def shutdown(self):
        self._running = False

    # ── Mock mode ─────────────────────────────────────────────────────────────

    def _update_mock(self, keys):
        if keys[pygame.K_UP]:
            self.stress_level = min(1.0, self.stress_level + self.MOCK_DELTA)
        if keys[pygame.K_DOWN]:
            self.stress_level = max(0.0, self.stress_level - self.MOCK_DELTA)

    # ── Muse / LSL mode ───────────────────────────────────────────────────────

    def _connect_lsl(self):
        print("[LSL] Searching for EEG stream …")
        try:
            streams = resolve_byprop("type", "EEG", timeout=5)
            if not streams:
                print("[LSL] No EEG stream found — falling back to MOCK_MODE")
                self.mock_mode = True
                return
            self._inlet  = StreamInlet(streams[0])
            self._running = True
            self._lsl_thread = threading.Thread(
                target=self._lsl_reader, daemon=True)
            self._lsl_thread.start()
            print("[LSL] Connected to EEG stream.")
        except Exception as exc:
            print(f"[LSL] Connection error: {exc} — falling back to MOCK_MODE")
            self.mock_mode = True

    def _lsl_reader(self):
        """Background thread: pulls samples from LSL and appends to buffer."""
        while self._running and self._inlet:
            sample, _ = self._inlet.pull_sample(timeout=0.0)
            if sample:
                self._eeg_buffer.append(sample)

    def _update_eeg(self):
        """
        ASSET: Replace this with a proper spectral analysis (FFT → Alpha/Beta
        power ratio) once you have stable EEG data.

        For the hackathon we use a simple amplitude heuristic on the most
        recent 64 samples from channel 0 (TP9 on Muse 2).

        Stress ≈ clamp( mean(|raw_amplitude|) / CALIBRATION_MAX, 0, 1 )
        """
        if len(self._eeg_buffer) < 8:
            return

        CALIBRATION_MAX = 600.0   # µV — tune after recording baseline
        recent = list(self._eeg_buffer)[-64:]
        mean_amp = sum(abs(s[0]) for s in recent) / len(recent)

        raw_stress = mean_amp / CALIBRATION_MAX
        # Smooth with a simple exponential moving average
        alpha = 0.05
        self.stress_level = (
            alpha * min(1.0, raw_stress) +
            (1 - alpha) * self.stress_level
        )

    # ── Future: proper spectral stress ───────────────────────────────────────
    @staticmethod
    def alpha_beta_ratio_to_stress(alpha_power: float, beta_power: float) -> float:
        """
        Placeholder for a spectral approach.

        Higher alpha  → relaxed  → low stress
        Higher beta   → alert    → high stress
        Returns a value in [0, 1].
        """
        if alpha_power <= 0:
            return 0.5
        ratio = beta_power / alpha_power   # high ratio → stressed
        # Sigmoid normalisation (tune k / midpoint for your subject)
        k, midpoint = 3.0, 1.5
        return 1.0 / (1.0 + math.exp(-k * (ratio - midpoint)))


# =============================================================================
# AUDIO MANAGER
# =============================================================================

class AudioManager:
    """
    Manages music layers and one-shot stings.

    ASSET: Replace pygame.sndarray.make_sound() calls with
           pygame.mixer.Sound("assets/audio/<file>.wav")
    """

    def __init__(self):
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
        pygame.mixer.set_num_channels(8)

        # Channels
        self._ch_music   = pygame.mixer.Channel(0)
        self._ch_sting   = pygame.mixer.Channel(1)
        self._ch_ambient = pygame.mixer.Channel(2)

        # Synthesise placeholder sounds programmatically ─────────────────────
        self._sounds = {
            "calm_music"  : self._make_tone(110, 3.0, vol=0.25),
            "tense_music" : self._make_tone(80,  2.0, vol=0.35, wave="saw"),
            "sting_med"   : self._make_tone(300, 1.5, vol=0.6),
            "sting_high"  : self._make_tone(200, 0.8, vol=0.8),
            "crash_sound" : self._make_tone(60,  0.5, vol=1.0,  wave="noise"),
        }
        # ASSET: swap the dict values above, e.g.:
        #   "calm_music"  : pygame.mixer.Sound("assets/audio/calm_loop.wav"),
        #   "sting_med"   : pygame.mixer.Sound("assets/audio/know_youre_in.wav"),
        #   "sting_high"  : pygame.mixer.Sound("assets/audio/i_see_you.wav"),
        #   "crash_sound" : pygame.mixer.Sound("assets/audio/crash.wav"),

        self._last_sting_stress = -1.0
        self._sting_cooldown    = 0.0
        self._current_music_key = None

    # ── Public ───────────────────────────────────────────────────────────────

    def update(self, stress: float, dt: float):
        self._sting_cooldown = max(0.0, self._sting_cooldown - dt)
        self._update_music(stress)
        self._update_stings(stress)

    def play_one_shot(self, key: str):
        snd = self._sounds.get(key)
        if snd:
            self._ch_sting.play(snd)

    def stop_all(self):
        pygame.mixer.stop()

    # ── Internal ─────────────────────────────────────────────────────────────

    def _update_music(self, stress: float):
        key = "calm_music" if stress < STRESS_HIGH else "tense_music"
        if key != self._current_music_key:
            self._current_music_key = key
            snd = self._sounds[key]
            self._ch_music.play(snd, loops=-1, fade_ms=2000)

    def _update_stings(self, stress: float):
        if self._sting_cooldown > 0:
            return
        if STRESS_MED <= stress < STRESS_HIGH:
            # "I know you're in there"
            self.play_one_shot("sting_med")
            self._sting_cooldown = 12.0
        elif stress >= STRESS_HIGH:
            # "I see you"
            self.play_one_shot("sting_high")
            self._sting_cooldown = 8.0

    # ── Procedural sound synthesis (hackathon placeholder) ───────────────────
    @staticmethod
    def _make_tone(freq: float, duration: float, vol: float = 0.5,
                   wave: str = "sine") -> pygame.mixer.Sound:
        import numpy as np
        rate    = 44100
        n       = int(rate * duration)
        t       = np.linspace(0, duration, n, endpoint=False)
        if wave == "sine":
            data = np.sin(2 * math.pi * freq * t)
        elif wave == "saw":
            data = 2 * (t * freq - np.floor(t * freq + 0.5))
        else:   # noise
            data = np.random.uniform(-1, 1, n)
        data = (data * vol * 32767).astype(np.int16)
        stereo = np.column_stack([data, data])
        return pygame.sndarray.make_sound(stereo)


# =============================================================================
# WORLD / ENTITIES
# =============================================================================

class Player:
    """Simple rectangle placeholder for the player character."""

    W, H = 28, 52

    def __init__(self, x: float, y: float):
        self.x, self.y = x, y
        self.vel_x, self.vel_y = 0.0, 0.0
        self.on_ground = False
        self.speed     = 180      # px/s
        self.jump_vel  = -500

    def update(self, keys, dt: float, world_rect: pygame.Rect):
        # Horizontal movement
        self.vel_x = 0
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            self.vel_x = -self.speed
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            self.vel_x =  self.speed

        # Jump
        if (keys[pygame.K_w] or keys[pygame.K_SPACE]) and self.on_ground:
            self.vel_y    = self.jump_vel
            self.on_ground = False

        # Gravity
        self.vel_y = min(self.vel_y + 1400 * dt, 900)

        # Integrate position
        self.x += self.vel_x * dt
        self.y += self.vel_y * dt

        # Floor collision
        floor = world_rect.bottom - self.H
        if self.y >= floor:
            self.y        = float(floor)
            self.vel_y    = 0
            self.on_ground = True

        # Wall clamp (inside house)
        self.x = max(float(world_rect.left), min(self.x, world_rect.right - self.W))

    @property
    def rect(self) -> pygame.Rect:
        return pygame.Rect(int(self.x), int(self.y), self.W, self.H)

    def draw(self, surface: pygame.Surface, cam_offset: int):
        r = self.rect.move(-cam_offset, 0)
        pygame.draw.rect(surface, C_PLAYER, (r.x, r.y, r.width, 40))
        # ASSET: surface.blit(player_sprite, r.topleft)
        # Eyes
        eye_y = r.top + 10
        pygame.draw.circle(surface, C_WHITE, (r.centerx - 5, eye_y), 3) # Left
        pygame.draw.circle(surface, C_WHITE, (r.centerx + 5, eye_y), 3) # Right
        pygame.draw.circle(surface, C_BLACK, (r.centerx - 5, eye_y), 1)
        pygame.draw.circle(surface, C_BLACK, (r.centerx + 5, eye_y), 1)

        # Left
        pygame.draw.rect(surface, C_PLAYER, (r.centerx - 11, r.bottom - 14, 5, 15))

        # Right leg
        pygame.draw.rect(surface, C_PLAYER, (r.centerx + 6, r.bottom - 14, 5, 15))
                         

class Monster:
    """The antagonist that lurks outside the house window."""

    W, H = 40, 80

    def __init__(self, x: float, y: float):
        self.x, self.y    = x, y
        self.base_speed   = 40.0     # px/s calm
        self.target_x     = x
        self._bob_phase   = 0.0

    def update(self, stress: float, player_x: float, dt: float):
        # Speed scales with stress
        speed = self.base_speed + stress * 220.0

        # Chase player (stays outside — limited x range)
        chase_target = player_x - 280   # peek from outside window
        self.target_x = max(-300.0, min(self.target_x, -60.0))
        self.target_x = chase_target if stress > STRESS_MED else -250.0

        dx = self.target_x - self.x
        if abs(dx) > 1:
            self.x += math.copysign(min(speed * dt, abs(dx)), dx)

        self._bob_phase += dt * (1.5 + stress * 3.0)

    @property
    def rect(self) -> pygame.Rect:
        bob = math.sin(self._bob_phase) * 6
        return pygame.Rect(int(self.x), int(self.y + bob), self.W, self.H)

    def draw(self, surface: pygame.Surface, cam_offset: int, stress: float):
        r = self.rect.move(-cam_offset, 0)
        if r.right < 0 or r.left > SCREEN_W:
            return   # off-screen

        # Opacity scales with stress (fake with alpha surface)
        alpha = int(40 + stress * 215)
        tmp   = pygame.Surface((self.W, self.H), pygame.SRCALPHA)
        tmp.fill((*C_MONSTER, alpha))
        # ASSET: tmp.blit(monster_sprite, (0, 0))
        # Simple toothy face
        eye_col = (255, int(255 * (1 - stress)), 0)
        pygame.draw.circle(tmp, eye_col, (12, 20), 6)
        pygame.draw.circle(tmp, eye_col, (28, 20), 6)
        pygame.draw.circle(tmp, C_BLACK,  (12, 20), 2)
        pygame.draw.circle(tmp, C_BLACK,  (28, 20), 2)
        for i in range(6):
            pygame.draw.polygon(tmp, (180, 0, 0), [
                (4 + i * 6, 50), (7 + i * 6, 62), (10 + i * 6, 50)])
        surface.blit(tmp, r.topleft)


class Interactable(pygame.sprite.Sprite):
    def __init__(self, x, y, width, height):
        super().__init__()
        self.image = pygame.Surface((width, height))
        self.image.fill((0, 255, 0)) # Green box
        self.rect = self.image.get_rect(topleft=(x, y))
        self.interact_range = 100 

    def is_near(self, player_rect):
        # Create vectors for the centers of both objects
        player_vec = pygame.math.Vector2(player_rect.center)
        target_vec = pygame.math.Vector2(self.rect.center)
        
        # Calculate distance
        distance = player_vec.distance_to(target_vec)
        return distance < self.interact_range


# =============================================================================
# WORLD RENDERER
# =============================================================================

class World:
    """
    Draws the house interior (foreground) and the night outside (background).
    The camera follows the player horizontally.
    """

    WORLD_W = 1800      # total world pixel width
    FLOOR_Y = SCREEN_H - 120

    def __init__(self):
        self.inner_rect = pygame.Rect(200, 0, self.WORLD_W - 400, self.FLOOR_Y)
        # Window in the left wall (the monster peeks through here)
        self.window_rect = pygame.Rect(210, SCREEN_H // 2 - 80, 80, 120)
        
        self.table = Interactable(600, self.FLOOR_Y - 70, 140, 70)

    def get_camera_offset(self, player_x: float) -> int:
        """Centre camera on player, clamped to world bounds."""
        cx = int(player_x + Player.W // 2 - SCREEN_W // 2)
        return max(0, min(cx, self.WORLD_W - SCREEN_W))

    def draw(self, surface: pygame.Surface, cam: int, stress: float):
        # Sky / outside
        surface.fill(C_SKY)

        # Outside ground (visible through window area)
        outside_ground = pygame.Rect(-cam, self.FLOOR_Y, self.WORLD_W, 200)
        pygame.draw.rect(surface, (15, 12, 8), outside_ground)

        # House back wall
        wall_rect = pygame.Rect(self.inner_rect.left - cam,
                                0,
                                self.inner_rect.width,
                                SCREEN_H)
        pygame.draw.rect(surface, C_HOUSE_WALL, wall_rect)

        # Window (glows more red under high stress)
        wr = self.window_rect.move(-cam, 0)
        win_col = (
            int(80 + stress * 120),
            int(100 * (1 - stress * 0.8)),
            int(60 * (1 - stress)),
        )
        pygame.draw.rect(surface, win_col, wr)
        pygame.draw.rect(surface, (200, 200, 160), wr, 4)  # frame

        # Floor
        floor_r = pygame.Rect(-cam, self.FLOOR_Y, self.WORLD_W, 200)
        pygame.draw.rect(surface, (45, 35, 28), floor_r)  # wood floor
        # planks
        for px in range(0, self.WORLD_W, 60):
            pygame.draw.line(surface, (35, 25, 18),
                             (px - cam, self.FLOOR_Y),
                             (px - cam, self.FLOOR_Y + 120), 2)

        # Furniture silhouettes ────────────────────────────────────────────
        # Table
        #self._draw_rect(surface, cam, 600, self.FLOOR_Y - 70, 140, 70, (55, 40, 30))
        table = Interactable(600, self.FLOOR_Y - 70, 140, 70)
        # Sofa
        self._draw_rect(surface, cam, 900, self.FLOOR_Y - 90, 200, 90, (50, 35, 60))
        # Bookshelf
        self._draw_rect(surface, cam, 1300, 200, 60, self.FLOOR_Y - 200, (40, 30, 22))
        # ASSET: blit furniture sprites here

    @staticmethod
    def _draw_rect(surf, cam, wx, wy, w, h, col):
        pygame.draw.rect(surf, col, pygame.Rect(wx - cam, wy, w, h))


# =============================================================================
# HUD — BRAINWAVE MONITOR
# =============================================================================

class BrainwaveMonitor:
    """Scrolling line graph of stress level shown in the HUD corner."""

    def __init__(self, x: int, y: int):
        self.x, self.y = x, y
        self.history   = collections.deque(
            [0.0] * GRAPH_HISTORY_LEN, maxlen=GRAPH_HISTORY_LEN)
        self._surface  = pygame.Surface((GRAPH_W, GRAPH_H))

    def push(self, value: float):
        self.history.append(value)

    def draw(self, screen: pygame.Surface, stress: float, mode_label: str):
        s = self._surface
        s.fill(C_GRAPH_BG)

        # Threshold lines
        for threshold, col in [(STRESS_MED, (100, 100, 0)),
                                (STRESS_HIGH, (150, 50, 0)),
                                (STRESS_PANIC, (200, 0, 0))]:
            ty = int(GRAPH_H - threshold * GRAPH_H)
            pygame.draw.line(s, col, (0, ty), (GRAPH_W, ty), 1)

        # Waveform
        pts = []
        for i, v in enumerate(self.history):
            px = i
            py = int(GRAPH_H - v * GRAPH_H)
            pts.append((px, py))
        if len(pts) > 1:
            pygame.draw.lines(s, C_GRAPH_LINE, False, pts, 2)

        # Border
        pygame.draw.rect(s, (50, 80, 50), s.get_rect(), 1)

        screen.blit(s, (self.x, self.y))

        # Labels
        font_sm = pygame.font.SysFont("monospace", 11)
        screen.blit(font_sm.render(f"STRESS  {stress:.2f}", True, C_HUD_TEXT),
                    (self.x, self.y - 14))
        mode_col = (100, 255, 100) if "MOCK" in mode_label else (255, 180, 0)
        screen.blit(font_sm.render(mode_label, True, mode_col),
                    (self.x + GRAPH_W - 90, self.y - 14))


# =============================================================================
# VISUAL EFFECTS
# =============================================================================

class VFXLayer:
    """
    Draws post-processing overlays on top of the world:
      - Red vignette that intensifies with stress
      - Screen pulse at high stress
      - Static noise at panic level
    """

    def __init__(self):
        self._vignette  = self._build_vignette()
        self._pulse_t   = 0.0
        self._noise_surf = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)

    def update(self, stress: float, dt: float):
        self._pulse_t += dt * (1.0 + stress * 5.0)

    def draw(self, surface: pygame.Surface, stress: float):
        if stress < STRESS_MED:
            return

        # Red tint overlay (alpha 0–160)
        tint_alpha = int((stress - STRESS_MED) / (1.0 - STRESS_MED) * 160)
        tint = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        tint.fill((180, 0, 0, tint_alpha))
        surface.blit(tint, (0, 0))

        # Vignette
        v_alpha = int((stress - STRESS_MED) / (1.0 - STRESS_MED) * 200)
        self._vignette.set_alpha(v_alpha)
        surface.blit(self._vignette, (0, 0))

        # Pulse at high stress
        if stress >= STRESS_HIGH:
            pulse = (math.sin(self._pulse_t * 4) + 1) / 2   # 0..1
            pulse_a = int(pulse * 80 * (stress - STRESS_HIGH) / (1 - STRESS_HIGH))
            pulse_s = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
            pulse_s.fill((200, 0, 0, pulse_a))
            surface.blit(pulse_s, (0, 0))

        # Scanline / static near panic
        if stress >= STRESS_PANIC - 0.1:
            self._draw_static(surface, stress)

    def _draw_static(self, surface: pygame.Surface, stress: float):
        intensity = (stress - (STRESS_PANIC - 0.1)) / 0.1
        for _ in range(int(intensity * 120)):
            rx = random.randint(0, SCREEN_W - 1)
            ry = random.randint(0, SCREEN_H - 1)
            rw = random.randint(1, 80)
            a  = random.randint(30, 120)
            pygame.draw.line(surface, (255, 255, 255, a),
                             (rx, ry), (rx + rw, ry), 1)

    @staticmethod
    def _build_vignette() -> pygame.Surface:
        surf = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        cx, cy = SCREEN_W // 2, SCREEN_H // 2
        max_r  = math.hypot(cx, cy)
        for y in range(0, SCREEN_H, 4):
            for x in range(0, SCREEN_W, 4):
                d = math.hypot(x - cx, y - cy) / max_r
                a = int(d ** 2.2 * 255)
                pygame.draw.rect(surf, (0, 0, 0, a), (x, y, 4, 4))
        return surf


# =============================================================================
# FAKE CRASH ASSETS
# =============================================================================

def build_fake_crash_surface() -> pygame.Surface:
    """
    Renders a fake Windows-style BSOD / crash screen.

    ASSET: Replace with pygame.image.load("assets/images/fake_crash.png")
    """
    surf = pygame.Surface((SCREEN_W, SCREEN_H))
    surf.fill((0, 0, 120))   # classic BSOD blue

    font_big = pygame.font.SysFont("monospace", 48, bold=True)
    font_mid = pygame.font.SysFont("monospace", 22)
    font_sm  = pygame.font.SysFont("monospace", 15)

    lines_big = [":(", "Your PC ran into a problem"]
    lines_mid = [
        "and needs to restart.",
        "",
        "SIGNAL_LOST_CRITICAL_FAILURE",
        "",
        "If you'd like to know more, you can search online for",
        "this error: EEG_HORROR_EXCEPTION  (0x0000DEAD)",
    ]
    lines_sm = ["Collecting error info …  0%", "",
                "(Press any key to continue)"]

    y = 80
    for l in lines_big:
        surf.blit(font_big.render(l, True, C_WHITE), (100, y))
        y += 60
    y += 20
    for l in lines_mid:
        surf.blit(font_mid.render(l, True, C_WHITE), (100, y))
        y += 32
    y += 40
    for l in lines_sm:
        surf.blit(font_sm.render(l, True, (180, 180, 220)), (100, y))
        y += 22
    return surf


def build_jumpscare_surface() -> pygame.Surface:
    """
    Procedural jumpscare face (solid red background, crude face).

    ASSET: Replace with pygame.image.load("assets/images/jumpscare.png")
    """
    surf = pygame.Surface((SCREEN_W, SCREEN_H))
    surf.fill((140, 0, 0))

    # Crude screaming face
    cx, cy = SCREEN_W // 2, SCREEN_H // 2
    # Head
    pygame.draw.ellipse(surf, (30, 20, 20), (cx - 200, cy - 240, 400, 480))
    # Eyes
    for ex in (cx - 70, cx + 70):
        pygame.draw.ellipse(surf, C_WHITE,   (ex - 35, cy - 120, 70, 90))
        pygame.draw.ellipse(surf, C_BLACK,   (ex - 15, cy -  90, 30, 50))
        pygame.draw.circle(surf,  (255, 0, 0), (ex, cy - 70), 8)
    # Mouth — wide open
    pygame.draw.ellipse(surf, C_BLACK, (cx - 120, cy + 60, 240, 140))
    for i in range(7):
        tx = cx - 100 + i * 33
        pygame.draw.polygon(surf, C_WHITE, [
            (tx, cy + 60), (tx + 15, cy + 100), (tx + 30, cy + 60)])
    font = pygame.font.SysFont("impact", 120)
    label = font.render("YOU DIED", True, (255, 255, 0))
    surf.blit(label, label.get_rect(center=(cx, cy + 260)))
    # ASSET: surf = pygame.image.load("assets/images/jumpscare.png").convert()
    return surf


# =============================================================================
# STATE MACHINE
# =============================================================================

class State:
    """Abstract base class for game states."""
    def on_enter(self, game): pass
    def on_exit(self, game):  pass
    def handle_event(self, game, event): pass
    def update(self, game, dt: float):   pass
    def draw(self, game, surface):       pass


# ── MENU STATE ────────────────────────────────────────────────────────────────

class MenuState(State):
    def on_enter(self, game):
        self._alpha   = 0
        self._fade_in = True

    def handle_event(self, game, event):
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_RETURN:
                game.change_state("gameplay")
            elif event.key == pygame.K_ESCAPE:
                game.running = False

    def update(self, game, dt):
        self._alpha = min(255, self._alpha + int(255 * dt * 0.8))

    def draw(self, game, surface):
        surface.fill(C_BLACK)
        font_title = pygame.font.SysFont("impact", 72)
        font_sub   = pygame.font.SysFont("monospace", 22)
        font_hint  = pygame.font.SysFont("monospace", 16)

        title = font_title.render("SIGNAL LOST", True, (180, 0, 0))
        title.set_alpha(self._alpha)
        surface.blit(title, title.get_rect(center=(SCREEN_W // 2, 200)))

        sub = font_sub.render("An EEG Horror Experience", True, (160, 140, 160))
        sub.set_alpha(self._alpha)
        surface.blit(sub, sub.get_rect(center=(SCREEN_W // 2, 290)))

        mode = "MOCK (Keyboard)" if game.stress_mgr.mock_mode else "MUSE EEG"
        info = font_hint.render(f"Input mode: {mode}", True, (100, 200, 100))
        surface.blit(info, info.get_rect(center=(SCREEN_W // 2, 380)))

        hint = font_hint.render("ENTER — Start    ESC — Quit", True, C_HUD_TEXT)
        hint.set_alpha(self._alpha)
        surface.blit(hint, hint.get_rect(center=(SCREEN_W // 2, SCREEN_H - 60)))

        controls = font_hint.render(
            "WASD / Arrows — Move   SPACE — Jump   ↑↓ — Stress (mock)",
            True, (80, 80, 100))
        surface.blit(controls, controls.get_rect(center=(SCREEN_W // 2, SCREEN_H - 35)))


# ── GAMEPLAY STATE ─────────────────────────────────────────────────────────────

class GameplayState(State):
    def on_enter(self, game):
        self.world   = World()
        self.player  = Player(
            x=float(self.world.inner_rect.centerx - Player.W // 2),
            y=float(self.world.FLOOR_Y - Player.H)
        )
        self.monster = Monster(x=-250.0, y=float(self.world.FLOOR_Y - Monster.H))
        self.vfx     = VFXLayer()
        self.monitor = BrainwaveMonitor(x=SCREEN_W - GRAPH_W - 20, y=20)
        self._font   = pygame.font.SysFont("monospace", 14)
        self._panic_triggered = False

    def handle_event(self, game, event):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            game.change_state("menu")

    def update(self, game, dt: float):
        keys   = pygame.key.get_pressed()
        stress = game.stress_mgr.stress_level

        self.player.update(keys, dt, self.world.inner_rect)
        self.monster.update(stress, self.player.x, dt)
        self.vfx.update(stress, dt)
        game.audio.update(stress, dt)
        self.monitor.push(stress)

        # Panic trigger → FakeCrash
        if stress >= STRESS_PANIC and not self._panic_triggered:
            self._panic_triggered = True
            game.change_state("fake_crash")

    def draw(self, game, surface):
        stress = game.stress_mgr.stress_level
        cam    = self.world.get_camera_offset(self.player.x)

        self.world.draw(surface, cam, stress)
        self.monster.draw(surface, cam, stress)
        self.player.draw(surface, cam)
        self.vfx.draw(surface, stress)

        # HUD stress bar
        self._draw_stress_bar(surface, stress)
        self.monitor.draw(surface, stress,
                          "MOCK" if game.stress_mgr.mock_mode else "MUSE EEG")

        # Director label
        label = self._stress_label(stress)
        lbl_surf = self._font.render(label, True, C_HUD_TEXT)
        surface.blit(lbl_surf, (20, 20))

        # Controls reminder (mock mode)
        if game.stress_mgr.mock_mode:
            hint = self._font.render("↑↓ — adjust stress   ESC — menu",
                                     True, (70, 70, 90))
            surface.blit(hint, (20, SCREEN_H - 25))

    @staticmethod
    def _draw_stress_bar(surface, stress):
        bx, by, bw, bh = 20, 40, 200, 14
        pygame.draw.rect(surface, (30, 30, 30), (bx, by, bw, bh))
        col = (
            int(stress * 220),
            int((1 - stress) * 180),
            0
        )
        pygame.draw.rect(surface, col, (bx, by, int(bw * stress), bh))
        pygame.draw.rect(surface, C_HUD_TEXT, (bx, by, bw, bh), 1)

    @staticmethod
    def _stress_label(s):
        if s < STRESS_MED:
            return "● CALM"
        if s < STRESS_HIGH:
            return "◆ UNEASY — 'I know you're in there…'"
        if s < STRESS_PANIC:
            return "◈ TERROR — 'I see you…'"
        return "☠ PANIC"


# ── FAKE CRASH STATE ───────────────────────────────────────────────────────────

class FakeCrashState(State):
    HOLD_DURATION = 3.5   # seconds before jumpscare

    def on_enter(self, game):
        game.audio.stop_all()
        # ASSET: game.audio.play_one_shot("crash_sound")
        self._surface = build_fake_crash_surface()
        self._timer   = 0.0
        self._done    = False

    def handle_event(self, game, event):
        pass   # swallow all input

    def update(self, game, dt):
        self._timer += dt
        if self._timer >= self.HOLD_DURATION and not self._done:
            self._done = True
            game.change_state("jumpscare")

    def draw(self, game, surface):
        surface.blit(self._surface, (0, 0))
        # Animate the "collecting error info" percentage
        pct = min(100, int(self._timer / self.HOLD_DURATION * 100))
        font = pygame.font.SysFont("monospace", 15)
        txt  = font.render(f"Collecting error info …  {pct}%", True, (180, 180, 220))
        surface.blit(txt, (100, SCREEN_H - 130))


# ── JUMPSCARE STATE ─────────────────────────────────────────────────────────────

class JumpscareState(State):
    HOLD_DURATION = 4.0   # seconds to show jumpscare

    def on_enter(self, game):
        self._surface = build_jumpscare_surface()
        self._timer   = 0.0
        self._shake   = 18.0    # px shake amplitude
        # ASSET: game.audio.play_one_shot("jumpscare_scream")

    def handle_event(self, game, event):
        if event.type == pygame.KEYDOWN:
            game.change_state("menu")

    def update(self, game, dt):
        self._timer += dt
        self._shake  = max(0.0, self._shake - dt * 8)
        if self._timer >= self.HOLD_DURATION:
            game.change_state("menu")

    def draw(self, game, surface):
        ox = random.randint(-int(self._shake), int(self._shake))
        oy = random.randint(-int(self._shake), int(self._shake))
        surface.blit(self._surface, (ox, oy))


# =============================================================================
# MAIN GAME LOOP
# =============================================================================

class Game:
    def __init__(self):
        pygame.init()
        self.screen  = pygame.display.set_mode((SCREEN_W, SCREEN_H))
        pygame.display.set_caption(TITLE)
        self.clock   = pygame.time.Clock()
        self.running = True

        self.stress_mgr = StressManager(mock_mode=MOCK_MODE)
        self.audio      = AudioManager()

        # Register all states
        self._states: dict[str, State] = {
            "menu"      : MenuState(),
            "gameplay"  : GameplayState(),
            "fake_crash": FakeCrashState(),
            "jumpscare" : JumpscareState(),
        }
        self._current_state: State | None = None
        self.change_state("menu")

    # ── State machine ─────────────────────────────────────────────────────────

    def change_state(self, name: str):
        if self._current_state:
            self._current_state.on_exit(self)
        self._current_state = self._states[name]
        # Re-instantiate gameplay so it resets properly
        if name == "gameplay":
            self._states["gameplay"] = GameplayState()
            self._current_state = self._states["gameplay"]
        if name == "fake_crash":
            self._states["fake_crash"] = FakeCrashState()
            self._current_state = self._states["fake_crash"]
        if name == "jumpscare":
            self._states["jumpscare"] = JumpscareState()
            self._current_state = self._states["jumpscare"]
        self._current_state.on_enter(self)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        while self.running:
            dt   = self.clock.tick(FPS) / 1000.0
            dt   = min(dt, 0.05)   # clamp to avoid spiral-of-death
            keys = pygame.key.get_pressed()

            # Events
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                self._current_state.handle_event(self, event)

            # Update stress
            self.stress_mgr.update(keys, dt)

            # Update current state
            self._current_state.update(self, dt)

            # Draw
            self._current_state.draw(self, self.screen)

            # FPS counter
            fps_surf = pygame.font.SysFont("monospace", 11).render(
                f"FPS {int(self.clock.get_fps())}", True, (60, 60, 80))
            self.screen.blit(fps_surf, (SCREEN_W - 60, SCREEN_H - 18))

            pygame.display.flip()

        # Cleanup
        self.stress_mgr.shutdown()
        pygame.quit()
        sys.exit()


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    Game().run()