"""
=============================================================================
 SIGNAL LOST — Narrative EEG Horror Game
 Engine  : Pygame CE
 EEG I/O : pylsl (Lab Streaming Layer)
 Hackathon refactor: multi-scene story with SceneManager
=============================================================================

QUICK-START
-----------
    pip install pygame-ce pylsl numpy
    python main_3.py

KEY BINDINGS (MOCK mode)
-------------------------
    WASD / Arrow keys  → move player
    SPACE              → jump / interact (hold near object)
    UP / DOWN arrows   → raise / lower stress
    E                  → interact with nearest object
    ESC                → quit to menu

SCENE FLOW
----------
    Menu → Calibration → Scene1 (Morning) → Scene2 (Evening)
         → Scene3 (Closet Choice)
              ├─ Yes (calm) → Scene6 (Good Ending)
              └─ No / timeout / high-stress → Scene4 (Nightmare)
                                                  └─ Scene5 (Bad Ending)
    (Any scene) stress ≥ STRESS_PANIC → Scene7 (Fake Crash + Jumpscare)

ASSET SLOTS
-----------
    Search "ASSET:" for every placeholder to swap before demo day.

=============================================================================
"""

# ---------------------------------------------------------------------------
# Standard library
# ---------------------------------------------------------------------------
import sys, math, random, collections, threading, pylsl

# ---------------------------------------------------------------------------
# Third-party
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

MOCK_MODE    = False           # False → real Muse EEG via LSL

SCREEN_W     = 1280
SCREEN_H     = 720
FPS          = 60
TITLE        = "SIGNAL LOST — EEG Horror"

# ── Stress thresholds ────────────────────────────────────────────────────────
STRESS_MED   = 0.35           # vignette / ominous music starts
STRESS_HIGH  = 0.65           # pulse / "I see you"
STRESS_PANIC = 0.95           # → fake crash (Scene 7)

# Scenes where EEG actively affects audio/visuals
EEG_ACTIVE_SCENES = {"scene3", "scene4", "fake_crash", "jumpscare"}

# ── Brainwave monitor ────────────────────────────────────────────────────────
GRAPH_W, GRAPH_H  = 260, 80
GRAPH_HISTORY_LEN = GRAPH_W

# ── Colours ──────────────────────────────────────────────────────────────────
C_BLACK      = (  0,   0,   0)
C_WHITE      = (255, 255, 255)
C_RED        = (200,   0,   0)
C_DARK_RED   = (120,   0,   0)
C_GREEN      = (  0, 200,  50)
C_HUD_TEXT   = (200, 220, 255)
C_MONSTER    = ( 80,   0,   0)
C_PLAYER     = (100, 160, 220)
C_HOUSE_WALL = ( 60,  50,  40)
C_SKY        = (  5,   8,  18)
C_GRAPH_LINE = (  0, 255, 100)
C_GRAPH_BG   = ( 10,  10,  20)

# ── Room palette (used across scene renderers) ────────────────────────────────
C_FLOOR_DAY  = ( 45,  35,  28)
C_FLOOR_NIGHT= ( 20,  15,  12)
C_WALL_DAY   = ( 80,  65,  55)
C_WALL_NIGHT = ( 30,  22,  18)
C_CEILING    = ( 55,  45,  38)

# =============================================================================
# DIALOGUE SYSTEM  (global helper — used by multiple scenes)
# =============================================================================

def draw_dialogue(surface: pygame.Surface,
                  text: str,
                  speaker: str = "",
                  portrait_col: tuple = (80, 60, 100)) -> None:
    """
    Renders a black dialogue box at the bottom-centre of the screen.

    Parameters
    ----------
    surface     : target pygame Surface
    text        : body text to display (auto-wraps at ~60 chars)
    speaker     : name printed above the box (e.g. "Mom")
    portrait_col: placeholder portrait colour
                  ASSET: replace portrait rect with surface.blit(portrait_img, ...)

    Layout
    ------
        ┌──────────────────────────────────────────────────┐
        │ [portrait] SPEAKER NAME                          │
        │            Body text wraps here...               │
        └──────────────────────────────────────────────────┘
    """
    BOX_W, BOX_H = 900, 140
    BOX_X = (SCREEN_W - BOX_W) // 2
    BOX_Y = SCREEN_H - BOX_H - 16
    PAD   = 14
    PORT  = 90   # portrait square size

    # Semi-transparent background
    box = pygame.Surface((BOX_W, BOX_H), pygame.SRCALPHA)
    box.fill((0, 0, 0, 210))
    pygame.draw.rect(box, (140, 100, 80), box.get_rect(), 2)
    surface.blit(box, (BOX_X, BOX_Y))

    # Portrait placeholder
    port_rect = pygame.Rect(BOX_X + PAD, BOX_Y + PAD, PORT, PORT)
    pygame.draw.rect(surface, portrait_col, port_rect, border_radius=6)
    pygame.draw.rect(surface, (180, 160, 140), port_rect, 2, border_radius=6)
    # ASSET: surface.blit(portrait_sprites[speaker], port_rect.topleft)

    # Speaker name
    f_name = pygame.font.SysFont("monospace", 15, bold=True)
    if speaker:
        surface.blit(f_name.render(speaker, True, (255, 220, 140)),
                     (BOX_X + PORT + PAD * 2, BOX_Y + PAD))

    # Body text — simple word-wrap
    f_body  = pygame.font.SysFont("monospace", 16)
    max_w   = BOX_W - PORT - PAD * 4
    words   = text.split()
    lines   = []
    line    = ""
    for w in words:
        test = line + (" " if line else "") + w
        if f_body.size(test)[0] > max_w:
            if line:
                lines.append(line)
            line = w
        else:
            line = test
    if line:
        lines.append(line)

    ty = BOX_Y + PAD + 22
    for ln in lines[:3]:   # max 3 visible lines
        surface.blit(f_body.render(ln, True, C_WHITE),
                     (BOX_X + PORT + PAD * 2, ty))
        ty += 22

    # Advance-prompt blink
    if (pygame.time.get_ticks() // 500) % 2 == 0:
        surface.blit(f_name.render("▶ E / SPACE to continue", True, (120, 120, 100)),
                     (BOX_X + BOX_W - 230, BOX_Y + BOX_H - 22))


# =============================================================================
# STRESS MANAGER  (unchanged from previous iteration)
# =============================================================================

class StressManager:
    """
    Dual-input stress provider with 20-second baseline calibration.
    Public: stress_level (0‥1), is_calibrated, calibration_progress
    """
    CALIBRATION_DURATION = 20.0
    MOCK_DELTA           = 0.008
    SENSITIVITY          = 350.0

    def __init__(self, mock_mode: bool = True):
        self.mock_mode            = mock_mode or not LSL_AVAILABLE
        self.stress_level         = 0.0
        self.is_calibrated        = False
        self.baseline_value       = 0.0
        self.calibration_progress = 0.0
        self._calib_timer         = 0.0
        self.baseline_buffer: list= []
        self._inlet               = None
        self._eeg_buffer          = collections.deque(maxlen=256)
        self._lsl_thread          = None
        self._running             = False
        if not self.mock_mode:
            self._connect_lsl()

    def update(self, keys, dt: float):
        if not self.is_calibrated:
            self._update_calibration(dt)
        else:
            self._update_mock(keys) if self.mock_mode else self._update_eeg()

    def shutdown(self):
        self._running = False

    # ── calibration ──────────────────────────────────────────────────────────
    def _update_calibration(self, dt):
        self._calib_timer        += dt
        self.calibration_progress = min(1.0, self._calib_timer / self.CALIBRATION_DURATION)
        raw = self._read_raw_amplitude()
        if raw is not None:
            self.baseline_buffer.append(raw)
        if self._calib_timer >= self.CALIBRATION_DURATION:
            self._finalise_calibration()

    def _read_raw_amplitude(self):
        if self.mock_mode:
            t = self._calib_timer
            return 200.0 + 30.0 * math.sin(t * 1.1) + random.gauss(0, 15)
        if not self._eeg_buffer:
            return None
        recent = list(self._eeg_buffer)[-8:]
        return sum(abs(s[0]) for s in recent) / len(recent)

    def _finalise_calibration(self):
        self.baseline_value = (sum(self.baseline_buffer) / len(self.baseline_buffer)
                               if self.baseline_buffer else 200.0)
        self.is_calibrated = True
        print(f"[Calibration] Complete. Baseline={self.baseline_value:.1f}µV "
              f"({len(self.baseline_buffer)} samples)")

    # ── mock ─────────────────────────────────────────────────────────────────
    def _update_mock(self, keys):
        if keys[pygame.K_UP]:
            self.stress_level = min(1.0, self.stress_level + self.MOCK_DELTA)
        if keys[pygame.K_DOWN]:
            self.stress_level = max(0.0, self.stress_level - self.MOCK_DELTA)

    # ── real EEG ─────────────────────────────────────────────────────────────
    def _connect_lsl(self):
        print("[LSL] Searching for EEG stream…")
        try:
            streams = resolve_byprop("type", "EEG", timeout=5)
            if not streams:
                print("[LSL] None found — MOCK_MODE")
                self.mock_mode = True
                return
            self._inlet   = StreamInlet(streams[0])
            self._running = True
            self._lsl_thread = threading.Thread(target=self._lsl_reader, daemon=True)
            self._lsl_thread.start()
            print("[LSL] Connected.")
        except Exception as e:
            print(f"[LSL] Error: {e} — MOCK_MODE")
            self.mock_mode = True

    def _lsl_reader(self):
        while self._running and self._inlet:
            s, _ = self._inlet.pull_sample(timeout=0.0)
            if s:
                self._eeg_buffer.append(s)

    def _update_eeg(self):
        if len(self._eeg_buffer) < 8:
            return
        recent   = list(self._eeg_buffer)[-64:]
        mean_amp = sum(abs(s[0]) for s in recent) / len(recent)
        raw      = (mean_amp - self.baseline_value) / self.SENSITIVITY
        a        = 0.05
        self.stress_level = max(0.0, min(1.0,
            a * raw + (1 - a) * self.stress_level))

    @staticmethod
    def alpha_beta_ratio_to_stress(alpha_power, beta_power):
        if alpha_power <= 0:
            return 0.5
        ratio = beta_power / alpha_power
        return 1.0 / (1.0 + math.exp(-3.0 * (ratio - 1.5)))


# =============================================================================
# AUDIO MANAGER
# =============================================================================

class AudioManager:
    """
    Scene-aware audio.  EEG-driven stings only fire when
    `eeg_active` is True (set by the SceneManager).
    """

    def __init__(self):
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
        pygame.mixer.set_num_channels(16)

        self._ch_music   = pygame.mixer.Channel(0)
        self._ch_sting   = pygame.mixer.Channel(1)
        self._ch_ambient = pygame.mixer.Channel(2)
        self._ch_sfx     = pygame.mixer.Channel(3)

        self.eeg_active = False   # set True only in Scenes 3, 4, 7

        self._sounds = {
            # ── Music loops ──────────────────────────────────────────────────
            "calm_music"    : pygame.mixer.Sound("./assets/calm_loop (quiter).wav"),
            "tense_music"   : pygame.mixer.Sound("./assets/tense_loop.wav"),
            # ── Narrative stings ─────────────────────────────────────────────
            "ominous_sting" : pygame.mixer.Sound("./assets/ominous_sting.wav"),
            "sting_med"     : pygame.mixer.Sound("./assets/know_youre_in.wav"),
            "sting_high"    : pygame.mixer.Sound("./assets/I_see_you.wav"),
            "crash_sound"   : pygame.mixer.Sound("./assets/crash.wav"),
            # ── Ambient / SFX ────────────────────────────────────────────────
            "ambient_wind"  : self._make_tone(55,  4.0, vol=0.07, wave="noise"),
            "closet_bang"   : self._make_tone(80,  0.4, vol=0.9,  wave="noise"),
            "heartbeat"     : self._make_tone(60,  0.3, vol=0.5),
            # ASSET: "ambient_wind"  : pygame.mixer.Sound("./assets/wind.wav"),
            # ASSET: "closet_bang"   : pygame.mixer.Sound("./assets/closet_bang.wav"),
            # ASSET: "heartbeat"     : pygame.mixer.Sound("./assets/heartbeat.wav"),
        }

        self._current_music_key  = None
        self._sting_cooldown     = 0.0
        self._ambient_playing    = False
        self._closet_bang_timer  = 0.0

    # ── Public ───────────────────────────────────────────────────────────────

    def update(self, stress: float, dt: float):
        self._sting_cooldown    = max(0.0, self._sting_cooldown - dt)
        self._closet_bang_timer = max(0.0, self._closet_bang_timer - dt)
        self._update_music(stress)
        if self.eeg_active:
            self._update_stings(stress)

    def play_music(self, key: str, fade_ms: int = 2000):
        if key != self._current_music_key:
            self._current_music_key = key
            snd = self._sounds.get(key)
            if snd:
                self._ch_music.play(snd, loops=-1, fade_ms=fade_ms)

    def play_one_shot(self, key: str):
        snd = self._sounds.get(key)
        if snd:
            self._ch_sting.play(snd)

    def play_sfx(self, key: str):
        snd = self._sounds.get(key)
        if snd:
            self._ch_sfx.play(snd)

    def play_ambient(self):
        if not self._ambient_playing:
            snd = self._sounds.get("ambient_wind")
            if snd:
                self._ch_ambient.play(snd, loops=-1, fade_ms=3000)
            self._ambient_playing = True

    def stop_ambient(self):
        self._ch_ambient.fadeout(1500)
        self._ambient_playing = False

    def fade_in_music(self, key: str = "calm_music"):
        self._current_music_key = key
        snd = self._sounds.get(key)
        if snd:
            self._ch_music.play(snd, loops=-1, fade_ms=3000)

    def stop_all(self):
        pygame.mixer.stop()
        self._current_music_key = None
        self._ambient_playing   = False

    def play_closet_bang(self):
        """Periodic banging during the closet scene."""
        if self._closet_bang_timer <= 0:
            self.play_sfx("closet_bang")
            self._closet_bang_timer = random.uniform(1.5, 4.0)

    # ── Internal ─────────────────────────────────────────────────────────────

    def _update_music(self, stress: float):
        if not self.eeg_active:
            return
        key = "tense_music"
        if key != self._current_music_key:
            self.play_music(key)

    def _update_stings(self, stress: float):
        if self._sting_cooldown > 0:
            return
        if STRESS_MED <= stress < STRESS_HIGH:
            self.play_one_shot("sting_med")
            self._sting_cooldown = 12.0
        elif stress >= STRESS_HIGH:
            self.play_one_shot("sting_high")
            self._sting_cooldown = 8.0

    @staticmethod
    def _make_tone(freq, duration, vol=0.5, wave="sine"):
        import numpy as np
        rate = 44100
        n    = int(rate * duration)
        t    = np.linspace(0, duration, n, endpoint=False)
        if wave == "sine":
            data = np.sin(2 * math.pi * freq * t)
        elif wave == "saw":
            data = 2 * (t * freq - np.floor(t * freq + 0.5))
        else:
            data = np.random.uniform(-1, 1, n)
        data   = (data * vol * 32767).astype(np.int16)
        stereo = np.column_stack([data, data])
        return pygame.sndarray.make_sound(stereo)


# =============================================================================
# PLAYER ENTITY  (reused across platformer scenes)
# =============================================================================

class Player:
    W, H       = 28, 52
    SPEED      = 180
    JUMP_VEL   = -500

    def __init__(self, x: float, y: float):
        self.x, self.y         = x, y
        self.vel_x = self.vel_y = 0.0
        self.on_ground          = False
        self.facing_right       = True

    def update(self, keys, dt: float, bounds: pygame.Rect):
        self.vel_x = 0
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            self.vel_x = -self.SPEED;  self.facing_right = False
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            self.vel_x =  self.SPEED;  self.facing_right = True
        if (keys[pygame.K_w] or keys[pygame.K_SPACE]) and self.on_ground:
            self.vel_y     = self.JUMP_VEL
            self.on_ground = False
        self.vel_y = min(self.vel_y + 1400 * dt, 900)
        self.x += self.vel_x * dt
        self.y += self.vel_y * dt
        floor = float(bounds.bottom - self.H)
        if self.y >= floor:
            self.y = floor; self.vel_y = 0; self.on_ground = True
        self.x = max(float(bounds.left), min(self.x, float(bounds.right - self.W)))

    @property
    def rect(self) -> pygame.Rect:
        return pygame.Rect(int(self.x), int(self.y), self.W, self.H)

    @property
    def world_center(self) -> tuple:
        return (self.x + self.W / 2, self.y + self.H / 2)

    def draw(self, surface: pygame.Surface, cam: int = 0):
        r = self.rect.move(-cam, 0)
        pygame.draw.circle(surface, (200, 150, 120), (r.centerx, r.top - 12), 12)

        pygame.draw.rect(surface, C_PLAYER, r, border_radius=3)
        # ASSET: surface.blit(player_sprite, r.topleft)
        # ey = r.top + 10
        # for ex in (r.centerx - 5, r.centerx + 5):
            # pygame.draw.circle(surface, C_WHITE, (ex, ey), 3)
            # pygame.draw.circle(surface, C_BLACK, (ex, ey), 1)


# =============================================================================
# NIGHTMARE MONSTER  (Scene 4)
# =============================================================================

class NightmareMonster:
    """
    In Scene 4 the monster chases the player but the goal is to TOUCH it.
    The monster stays a fixed distance ahead — lower stress = slower monster
    = easier to catch.
    """
    W, H         = 50, 90
    BASE_SPEED   = 120.0   # px/s at stress=0
    STRESS_BONUS = 280.0   # additional px/s at stress=1
    # Minimum gap the monster tries to maintain in front of the player
    LEAD_DIST    = 260.0

    def __init__(self, x: float, y: float):
        self.x, self.y  = x, y
        self._bob       = 0.0

    def update(self, stress: float, player_x: float, dt: float):
        speed      = self.BASE_SPEED + stress * self.STRESS_BONUS
        target_x   = player_x + self.LEAD_DIST
        dx         = target_x - self.x
        move       = math.copysign(min(speed * dt, abs(dx)), dx) if abs(dx) > 1 else 0
        self.x    += move
        self._bob += dt * (2.0 + stress * 4.0)

    @property
    def rect(self) -> pygame.Rect:
        bob = math.sin(self._bob) * 8
        return pygame.Rect(int(self.x), int(self.y + bob), self.W, self.H)

    def draw(self, surface: pygame.Surface, cam: int, stress: float):
        r = self.rect.move(-cam, 0)
        alpha = int(80 + stress * 175)
        tmp   = pygame.Surface((self.W, self.H), pygame.SRCALPHA)
        tmp.fill((*C_MONSTER, alpha))
        # ASSET: tmp.blit(nightmare_sprite, (0,0))
        ec = (255, int(255 * (1 - stress)), 0)
        for ex in (15, 35):
            pygame.draw.circle(tmp, ec,      (ex, 22), 7)
            pygame.draw.circle(tmp, C_BLACK, (ex, 22), 3)
        for i in range(7):
            tx = 5 + i * 6
            pygame.draw.polygon(tmp, (200, 0, 0),
                                 [(tx, 55), (tx + 4, 68), (tx + 8, 55)])
        surface.blit(tmp, r.topleft)


# =============================================================================
# INTERACTIVE PROP  (used in narrative scenes)
# =============================================================================

class Prop:
    """
    A labelled interactable object placed in a scene.

    Attributes
    ----------
    world_x, world_y : int   — world-space position
    w, h             : int   — dimensions
    label            : str   — shown when player is nearby
    colour           : tuple — placeholder fill colour
    sprite_path      : str   — ASSET path (see comment below)
    collected        : bool  — True once the player has interacted
    """

    INTERACT_RADIUS = 90   # pixels (world-space)

    def __init__(self, world_x, world_y, w, h,
                 label="", colour=(100, 80, 60), sprite_path=""):
        self.world_x     = world_x
        self.world_y     = world_y
        self.w           = w
        self.h           = h
        self.label       = label
        self.colour      = colour
        self.sprite_path = sprite_path  # ASSET: load with pygame.image.load()
        self.collected   = False
        self._sprite     = None         # loaded lazily

    @property
    def world_rect(self) -> pygame.Rect:
        return pygame.Rect(self.world_x, self.world_y, self.w, self.h)

    def in_range(self, player: Player) -> bool:
        px, py = player.world_center
        cx = self.world_x + self.w / 2
        cy = self.world_y + self.h / 2
        return math.hypot(px - cx, py - cy) < self.INTERACT_RADIUS

    def draw(self, surface: pygame.Surface, cam: int, in_range: bool):
        if self.collected:
            return
        r = pygame.Rect(self.world_x - cam, self.world_y, self.w, self.h)
        if self._sprite:
            surface.blit(self._sprite, r.topleft)
        else:
            pygame.draw.rect(surface, self.colour, r, border_radius=4)
            # ASSET: replace block above with sprite blit
        if in_range:
            f = pygame.font.SysFont("monospace", 13)
            lbl = f.render(f"[E] {self.label}", True, (255, 240, 160))
            surface.blit(lbl, (r.centerx - lbl.get_width() // 2, r.top - 20))
        pygame.draw.rect(surface, (180, 160, 130), r, 2, border_radius=4)


# =============================================================================
# VFX LAYER  (unchanged – only applied when eeg_active)
# =============================================================================

class VFXLayer:
    def __init__(self):
        self._vignette = self._build_vignette()
        self._pulse_t  = 0.0

    def update(self, stress, dt):
        self._pulse_t += dt * (1.0 + stress * 5.0)

    def draw(self, surface, stress):
        if stress < STRESS_MED:
            return
        ratio = (stress - STRESS_MED) / (1.0 - STRESS_MED)
        tint  = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        tint.fill((180, 0, 0, int(ratio * 160)))
        surface.blit(tint, (0, 0))
        self._vignette.set_alpha(int(ratio * 200))
        surface.blit(self._vignette, (0, 0))
        if stress >= STRESS_HIGH:
            p   = (math.sin(self._pulse_t * 4) + 1) / 2
            pa  = int(p * 80 * (stress - STRESS_HIGH) / (1 - STRESS_HIGH))
            ps  = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
            ps.fill((200, 0, 0, pa))
            surface.blit(ps, (0, 0))
        if stress >= STRESS_PANIC - 0.1:
            intensity = (stress - (STRESS_PANIC - 0.1)) / 0.1
            for _ in range(int(intensity * 120)):
                pygame.draw.line(surface, (255, 255, 255),
                                 (random.randint(0, SCREEN_W - 1),
                                  random.randint(0, SCREEN_H - 1)),
                                 (random.randint(0, SCREEN_W - 1),
                                  random.randint(0, SCREEN_H - 1)), 1)

    @staticmethod
    def _build_vignette():
        surf = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        cx, cy = SCREEN_W // 2, SCREEN_H // 2
        max_r  = math.hypot(cx, cy)
        for y in range(0, SCREEN_H, 4):
            for x in range(0, SCREEN_W, 4):
                d = math.hypot(x - cx, y - cy) / max_r
                pygame.draw.rect(surf, (0, 0, 0, int(d ** 2.2 * 255)), (x, y, 4, 4))
        return surf


# =============================================================================
# BRAINWAVE MONITOR HUD
# =============================================================================

class BrainwaveMonitor:
    def __init__(self, x, y):
        self.x, self.y = x, y
        self.history   = collections.deque([0.0] * GRAPH_HISTORY_LEN,
                                            maxlen=GRAPH_HISTORY_LEN)
        self._surf     = pygame.Surface((GRAPH_W, GRAPH_H))

    def push(self, v): self.history.append(v)

    def draw(self, screen, stress, label):
        s = self._surf
        s.fill(C_GRAPH_BG)
        for th, col in [(STRESS_MED,   (100, 100, 0)),
                        (STRESS_HIGH,  (150,  50, 0)),
                        (STRESS_PANIC, (200,   0, 0))]:
            ty = int(GRAPH_H - th * GRAPH_H)
            pygame.draw.line(s, col, (0, ty), (GRAPH_W, ty), 1)
        pts = [(i, int(GRAPH_H - v * GRAPH_H)) for i, v in enumerate(self.history)]
        if len(pts) > 1:
            pygame.draw.lines(s, C_GRAPH_LINE, False, pts, 2)
        pygame.draw.rect(s, (50, 80, 50), s.get_rect(), 1)
        screen.blit(s, (self.x, self.y))
        f = pygame.font.SysFont("monospace", 11)
        screen.blit(f.render(f"STRESS  {stress:.2f}", True, C_HUD_TEXT),
                    (self.x, self.y - 14))
        mc = (100, 255, 100) if "MOCK" in label else (255, 180, 0)
        screen.blit(f.render(label, True, mc), (self.x + GRAPH_W - 90, self.y - 14))


# =============================================================================
# FAKE CRASH / JUMPSCARE HELPERS  (Scene 7)
# =============================================================================

def build_fake_crash_surface():
    # ASSET: return pygame.image.load("assets/images/fake_crash.png").convert()
    surf     = pygame.Surface((SCREEN_W, SCREEN_H)); surf.fill((0, 0, 120))
    f_big    = pygame.font.SysFont("monospace", 48, bold=True)
    f_mid    = pygame.font.SysFont("monospace", 22)
    f_sm     = pygame.font.SysFont("monospace", 15)
    y = 80
    for line in [":(", "Your PC ran into a problem"]:
        surf.blit(f_big.render(line, True, C_WHITE), (100, y)); y += 60
    y += 20
    for line in ["and needs to restart.", "", "SIGNAL_LOST_CRITICAL_FAILURE", "",
                 "If you'd like to know more, search online for this error:",
                 "EEG_HORROR_EXCEPTION  (0x0000DEAD)"]:
        surf.blit(f_mid.render(line, True, C_WHITE), (100, y)); y += 32
    y += 40
    for line in ["Collecting error info ...  0%", "", "(Press any key to continue)"]:
        surf.blit(f_sm.render(line, True, (180, 180, 220)), (100, y)); y += 22
    return surf


def build_jumpscare_surface():
    # ASSET: return pygame.image.load("assets/images/jumpscare.png").convert()
    surf = pygame.Surface((SCREEN_W, SCREEN_H)); surf.fill((140, 0, 0))
    cx, cy = SCREEN_W // 2, SCREEN_H // 2
    pygame.draw.ellipse(surf, (30, 20, 20), (cx - 200, cy - 240, 400, 480))
    for ex in (cx - 70, cx + 70):
        pygame.draw.ellipse(surf, C_WHITE, (ex - 35, cy - 120, 70, 90))
        pygame.draw.ellipse(surf, C_BLACK, (ex - 15, cy -  90, 30, 50))
        pygame.draw.circle(surf, (255, 0, 0), (ex, cy - 70), 8)
    pygame.draw.ellipse(surf, C_BLACK, (cx - 120, cy + 60, 240, 140))
    for i in range(7):
        tx = cx - 100 + i * 33
        pygame.draw.polygon(surf, C_WHITE, [(tx, cy+60),(tx+15, cy+100),(tx+30, cy+60)])
    f = pygame.font.SysFont("impact", 120)
    lbl = f.render("YOU DIED", True, (255, 255, 0))
    surf.blit(lbl, lbl.get_rect(center=(cx, cy + 260)))
    return surf


# =============================================================================
# BASE STATE
# =============================================================================

class State:
    def on_enter(self, game): pass
    def on_exit(self, game):  pass
    def handle_event(self, game, event): pass
    def update(self, game, dt): pass
    def draw(self, game, surface): pass

    # Shared panic check — every scene that wants Scene 7 can call this
    def check_panic(self, game) -> bool:
        if game.stress_mgr.stress_level >= STRESS_PANIC:
            game.change_state("fake_crash")
            return True
        return False

    # Shared HUD bar
    @staticmethod
    def draw_stress_bar(surface, stress):
        bx, by, bw, bh = 20, 40, 200, 14
        pygame.draw.rect(surface, (30, 30, 30), (bx, by, bw, bh))
        col = (int(stress * 220), int((1-stress) * 180), 0)
        pygame.draw.rect(surface, col, (bx, by, int(bw * stress), bh))
        pygame.draw.rect(surface, C_HUD_TEXT, (bx, by, bw, bh), 1)

    # Shared fade helpers
    @staticmethod
    def draw_fade(surface, alpha):
        if alpha > 0:
            ov = pygame.Surface((SCREEN_W, SCREEN_H)); ov.fill(C_BLACK)
            ov.set_alpha(alpha); surface.blit(ov, (0, 0))


# =============================================================================
# SCENE MANAGER
# =============================================================================

class SceneManager:
    """
    Thin wrapper that owns the narrative game-state dictionary and routes
    change_state() calls from the Game object.

    It tracks `current_scene_name` so the AudioManager can know whether to
    apply EEG effects, and exposes a `game_flags` dict for cross-scene data:

        game_flags["has_backpack"]  bool  set in Scene 1
        game_flags["has_eaten"]     bool  set in Scene 1
        game_flags["closet_closed"] bool  set in Scene 3 (True → Good Ending)
    """

    EEG_SCENES = {"scene3", "scene4", "fake_crash", "jumpscare"}

    def __init__(self):
        self.game_flags = {
            "has_backpack"  : False,
            "has_eaten"     : False,
            "closet_closed" : False,
        }
        self.current_scene_name = ""

    def is_eeg_active(self) -> bool:
        return self.current_scene_name in self.EEG_SCENES


# =============================================================================
# CALIBRATION STATE
# =============================================================================

class CalibrationState(State):
    _C_BG       = (4, 8, 20)
    _C_RING     = (20, 60, 120)
    _C_RING_FIL = (40, 140, 220)
    _C_BAR_BG   = (20, 30, 50)
    _C_BAR_FILL = (50, 160, 255)
    _C_TITLE    = (160, 200, 255)
    _C_BODY     = (100, 140, 190)
    _C_TIMER    = (200, 230, 255)
    _DUR        = StressManager.CALIBRATION_DURATION

    def on_enter(self, game):
        game.audio.play_ambient()
        self._pulse_t    = 0.0
        self._fade_alpha = 255
        self._done       = False
        self._f_title    = pygame.font.SysFont("monospace", 28, bold=True)
        self._f_body     = pygame.font.SysFont("monospace", 17)
        self._f_timer    = pygame.font.SysFont("monospace", 52, bold=True)
        self._f_small    = pygame.font.SysFont("monospace", 13)

    def on_exit(self, game):
        game.audio.stop_ambient()
        game.audio.fade_in_music("calm_music")

    def handle_event(self, game, event):
        if (game.stress_mgr.mock_mode and event.type == pygame.KEYDOWN
                and event.key == pygame.K_RETURN):
            game.stress_mgr._finalise_calibration()

    def update(self, game, dt):
        self._pulse_t    += dt
        self._fade_alpha  = max(0, self._fade_alpha - int(255 * dt * 1.5))
        if game.stress_mgr.is_calibrated and not self._done:
            self._done = True
            game.change_state("scene1")

    def draw(self, game, surface):
        progress  = game.stress_mgr.calibration_progress
        remaining = max(0.0, self._DUR - game.stress_mgr._calib_timer)
        surface.fill(self._C_BG)
        cx, cy = SCREEN_W // 2, SCREEN_H // 2
        pulse  = (math.sin(self._pulse_t * 1.4) + 1) / 2
        radius = int(110 + pulse * 18)
        pygame.draw.circle(surface, self._C_RING, (cx, cy - 60), radius, 3)
        if progress > 0.01:
            steps = max(4, int(progress * 60))
            sa    = -math.pi / 2
            ea    = sa + 2 * math.pi * progress
            pts   = [(cx + radius * math.cos(sa + (ea-sa)*i/steps),
                      cy - 60 + radius * math.sin(sa + (ea-sa)*i/steps))
                     for i in range(steps + 1)]
            pygame.draw.lines(surface, self._C_RING_FIL, False, pts, 4)
        ts = self._f_timer.render(f"{int(remaining)+1:02d}", True, self._C_TIMER)
        surface.blit(ts, ts.get_rect(center=(cx, cy - 60)))
        t2 = self._f_title.render("◈  NEURAL SYNCING  ◈", True, self._C_TITLE)
        surface.blit(t2, t2.get_rect(center=(cx, cy + 80)))
        for i, ln in enumerate(["Please remain still and breathe deeply",
                                  "to calibrate the neural interface."]):
            s = self._f_body.render(ln, True, self._C_BODY)
            surface.blit(s, s.get_rect(center=(cx, cy + 125 + i * 26)))
        bw, bh = 480, 16
        bx = cx - bw // 2; by2 = cy + 200
        pygame.draw.rect(surface, self._C_BAR_BG,  (bx-2, by2-2, bw+4, bh+4), border_radius=10)
        if progress > 0:
            pygame.draw.rect(surface, self._C_BAR_FILL, (bx, by2, int(bw*progress), bh), border_radius=8)
        pygame.draw.rect(surface, self._C_RING_FIL, (bx-2, by2-2, bw+4, bh+4), 2, border_radius=10)
        pl = self._f_small.render(f"Signal Loading ...  {int(progress*100):3d}%", True, self._C_BODY)
        surface.blit(pl, pl.get_rect(center=(cx, by2 + bh + 16)))
        if game.stress_mgr.mock_mode:
            h = self._f_small.render("MOCK — press ENTER to skip", True, (60, 80, 110))
            surface.blit(h, h.get_rect(center=(cx, SCREEN_H - 28)))
        self.draw_fade(surface, self._fade_alpha)


# =============================================================================
# MENU STATE
# =============================================================================

class MenuState(State):
    def on_enter(self, game):
        self._alpha = 0
        game.audio.stop_all()

    def handle_event(self, game, event):
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_RETURN:
                game.change_state("calibration")
            elif event.key == pygame.K_ESCAPE:
                game.running = False

    def update(self, game, dt):
        self._alpha = min(255, self._alpha + int(255 * dt * 0.8))

    def draw(self, game, surface):
        surface.fill(C_BLACK)
        fi = pygame.font.SysFont("impact",    72)
        fs = pygame.font.SysFont("monospace", 22)
        fh = pygame.font.SysFont("monospace", 16)
        for surf, cy in [
            (fi.render("SIGNAL LOST",             True, (180, 0, 0)),   200),
            (fs.render("An EEG Horror Experience", True, (160,140,160)), 290),
        ]:
            surf.set_alpha(self._alpha)
            surface.blit(surf, surf.get_rect(center=(SCREEN_W//2, cy)))
        mode = "MOCK (Keyboard)" if game.stress_mgr.mock_mode else "MUSE EEG"
        surface.blit(fh.render(f"Input: {mode}", True, (100,200,100)),
                     pygame.Rect(0,0,0,0).move(SCREEN_W//2 - 80, 380))
        hints = [
            ("ENTER — Begin Calibration    ESC — Quit", SCREEN_H - 60),
            ("WASD/Arrows — Move   SPACE/E — Interact   ↑↓ — Stress (mock)", SCREEN_H - 35),
        ]
        for txt, y in hints:
            s = fh.render(txt, True, C_HUD_TEXT); s.set_alpha(self._alpha)
            surface.blit(s, s.get_rect(center=(SCREEN_W//2, y)))


# =============================================================================
# ── SCENE 1: MORNING  ────────────────────────────────────────────────────────
# =============================================================================

class Scene1Morning(State):
    """
    Domestic morning scene.
    - Player must collect Backpack AND eat Breakfast.
    - Door is locked until both flags are True.
    - EEG has NO effect on audio/visuals here.

    Room layout (world-space, world width = 2000px):
        Left wall   x=0
        Kitchen     x=200–700   (table with breakfast at ~350)
        Living area x=700–1400  (sofa, bookshelf)
        Stairs      x=1450      (leads up — not used yet)
        Front door  x=1750      (exit trigger)
        Backpack    x=900, on sofa area floor

    Mom NPC stands near the kitchen (x≈300).
    """

    WORLD_W  = 2000
    FLOOR_Y  = SCREEN_H - 100
    WALL_COL = (75, 60, 50)

    def on_enter(self, game):
        game.scene_mgr.current_scene_name = "scene1"
        game.audio.eeg_active = False
        game.audio.play_music("calm_music", fade_ms=2000)

        fl = float(self.FLOOR_Y - Player.H)
        self.player  = Player(x=300.0, y=fl)
        self.bounds  = pygame.Rect(0, 0, self.WORLD_W, self.FLOOR_Y)

        self.props = {
            "backpack":  Prop(850,  self.FLOOR_Y - 44, 30, 40,
                              "Pick up Backpack", (60, 80, 160)),
            "breakfast": Prop(400,  self.FLOOR_Y - 62, 20, 7,
                              "Eat Breakfast",    (180, 140, 80)),
            "door":      Prop(1760, self.FLOOR_Y - 175, 85, 175,
                              "Leave for School", (100, 80, 60)),
        }

        # self.props["backpack"].sprite_path  = "./assets/backpack.png"
        # self.props["breakfast"].sprite_path = "./assets/breakfast.png"

        # ASSET: props["door"].sprite_path      = "assets/images/front_door.png"

        self._dialogue     = None   # (text, speaker) or None
        self._dia_timer    = 0.0
        self._fade_alpha   = 255
        self._monitor      = BrainwaveMonitor(SCREEN_W - GRAPH_W - 20, 20)
        self._font         = pygame.font.SysFont("monospace", 14)

    def handle_event(self, game, event):
        if event.type != pygame.KEYDOWN:
            return
        if event.key in (pygame.K_e, pygame.K_SPACE):
            self._try_interact(game)
        if event.key == pygame.K_ESCAPE:
            game.change_state("menu")

    def _try_interact(self, game):
        bp  = self.props["backpack"]
        brk = self.props["breakfast"]
        door= self.props["door"]
        f   = game.scene_mgr.game_flags

        if not bp.collected and bp.in_range(self.player):
            bp.collected         = True
            f["has_backpack"]    = True
            self._show_dialogue("Mom: Have a good day at school, sweetie!", "Mom")
            return

        if not brk.collected and brk.in_range(self.player):
            brk.collected     = True
            f["has_eaten"]    = True
            self._show_dialogue("Mom: Its the most important meal of the day!", "Mom")
            return

        if door.in_range(self.player):
            if not f["has_backpack"]:
                self._show_dialogue("Mom: Don't forget your backpack!", "Mom")
            elif not f["has_eaten"]:
                self._show_dialogue("Mom: Eat something before you leave!", "Mom")
            else:
                game.change_state("scene2")

    def _show_dialogue(self, text, speaker, duration=2.0):
        self._dialogue  = (text, speaker)
        self._dia_timer = duration
        

    def update(self, game, dt):
        keys = pygame.key.get_pressed()
        self.player.update(keys, dt, self.bounds)
        self._monitor.push(game.stress_mgr.stress_level)
        if self._dia_timer > 0:
            self._dia_timer -= dt
            if self._dia_timer <= 0:
                self._dialogue = None
        self._fade_alpha = max(0, self._fade_alpha - int(255 * dt * 1.5))

    def draw(self, game, surface):
        cam    = self._cam()
        stress = game.stress_mgr.stress_level   # unused for vfx here

        # ── Background ───────────────────────────────────────────────────────
        surface.fill((180, 200, 220))   # bright daytime sky
        # Floor
        pygame.draw.rect(surface, C_FLOOR_DAY,
                         (-cam, self.FLOOR_Y, self.WORLD_W, 200))
        # Wall
        pygame.draw.rect(surface, self.WALL_COL,
                         (-cam, 0, self.WORLD_W, self.FLOOR_Y))
        # Plank lines
        for px in range(0, self.WORLD_W, 60):
            pygame.draw.line(surface, (35, 25, 18),
                             (px - cam, self.FLOOR_Y),
                             (px - cam, self.FLOOR_Y + 100), 2)
        # Window (bright outside)
        pygame.draw.rect(surface, (200, 230, 255),
                         (1100 - cam, 120, 200, 150))
        pygame.draw.rect(surface, (160, 140, 110),
                         (1100 - cam, 120, 200, 150), 5)
        # Mom NPC placeholder
        self._draw_mom(surface, cam)
        # Furniture
        # Kitchen table
        pygame.draw.rect(surface, (90, 60, 40),
                         (280 - cam, self.FLOOR_Y - 55, 175, 10))
        pygame.draw.rect(surface, (90, 60, 40), (435 - cam, self.FLOOR_Y - 45, 10, 45))
        pygame.draw.rect(surface, (90, 60, 40), (290 - cam, self.FLOOR_Y - 45, 10, 45))
                          
        # Sofa
        pygame.draw.rect(surface, (80, 55, 90),
                         (750 - cam, self.FLOOR_Y - 85, 220, 85))
        # ASSET: blit room background / furniture sprites here

        # Props
        for key, prop in self.props.items():
            # door greyed out until ready
            if key == "door":
                f = game.scene_mgr.game_flags
                col = (90, 70, 55) if (f["has_backpack"] and f["has_eaten"]) else (50, 40, 35)
                prop.colour = col
            prop.draw(surface, cam, prop.in_range(self.player))

        self.player.draw(surface, cam)

        # HUD
        self.draw_stress_bar(surface, game.stress_mgr.stress_level)
        self._monitor.draw(surface, game.stress_mgr.stress_level,
                           "MOCK" if game.stress_mgr.mock_mode else "MUSE EEG")

        # Objectives
        f = game.scene_mgr.game_flags
        fnt = pygame.font.SysFont("monospace", 13)
        for i, (done, txt) in enumerate([
            (f["has_backpack"], "✓ Grab backpack"  if f["has_backpack"] else "○ Grab backpack"),
            (f["has_eaten"],    "✓ Eat breakfast"  if f["has_eaten"]    else "○ Eat breakfast"),
        ]):
            col = (100, 220, 100) if done else (200, 200, 200)
            surface.blit(fnt.render(txt, True, col), (20, 68 + i * 18))

        if self._dialogue:
            draw_dialogue(surface, self._dialogue[0], self._dialogue[1])

        self.draw_fade(surface, self._fade_alpha)

    def _cam(self) -> int:
        cx = int(self.player.x + Player.W // 2 - SCREEN_W // 2)
        return max(0, min(cx, self.WORLD_W - SCREEN_W))

    @staticmethod
    def _draw_mom(surface, cam):
        # Mom NPC — simple humanoid silhouette
        mx = 300 - cam
        my = Scene1Morning.FLOOR_Y - 72
        pygame.draw.rect(surface, (200, 150, 120), (mx, my, 26, 72), border_radius = 3)  # body
        pygame.draw.circle(surface, (230, 180, 140), (mx + 13, my - 16), 16)  # head
        # ASSET: surface.blit(mom_sprite, (mx, my - 16))


# =============================================================================
# ── SCENE 2: EVENING  ────────────────────────────────────────────────────────
# =============================================================================

class Scene2Evening(State):
    """
    Player comes home from school.
    1. Mom greets them → ominous music plays regardless of EEG.
    2. Interact with Sink → sink cutscene dialogue plays.
    3. Go upstairs (Bed prop) to end the scene.
    EEG still does NOT control visuals/music here.
    """

    WORLD_W = 1800
    FLOOR_Y = SCREEN_H - 100

    # Dialogue sequence for the sink cutscene
    SINK_LINES = [
        ("Mom: Hey, how was school? Did you have fun with your buddies?",      "Mom"),
        ("Mom: Listen, I know you like hanging out with your friends...",      "Mom"),
        ("Mom: ...but you can't hang out after dark.",                         "Mom"),
        ("Mom: There have been stories on the news lately...",                 "Mom"),
        ("Mom: ...of a dangerous figure around this neighborhood.",            "Mom"),
        ("Mom: I want you to be safe.",                                        "Mom"),
    ]

    def on_enter(self, game):
        game.scene_mgr.current_scene_name = "scene2"
        game.audio.eeg_active = False
        # Ominous music plays regardless of EEG for atmospheric reasons
        game.audio.play_music("ominous_sting", fade_ms=3000)

        fl = float(self.FLOOR_Y - Player.H)
        self.player = Player(x=200.0, y=fl)
        self.bounds = pygame.Rect(0, 0, self.WORLD_W, self.FLOOR_Y)

        self.props = {
            "sink": Prop(400, self.FLOOR_Y - 70, 80, 70, "Help with dishes", (80, 100, 120)),
            "bed":  Prop(1500, self.FLOOR_Y - 90, 120, 90, "Go to sleep",    (60, 50, 90)),
        }
        # ASSET: props["sink"].sprite_path = "assets/images/sink.png"
        # ASSET: props["bed"].sprite_path  = "assets/images/bed.png"

        self._dia_queue    = []
        self._dia_active   = None
        self._dia_wait     = True     # waiting for keypress to advance
        self._sink_done    = False
        self._fade_alpha   = 255
        self._dark_alpha   = 0        # scene darkens after sink cutscene
        self._monitor      = BrainwaveMonitor(SCREEN_W - GRAPH_W - 20, 20)

        # Greet on entry
        self._queue_dialogue([
            ("Mom: Welcome home! Would you like to help me with the dishes?", "Mom")])

    def handle_event(self, game, event):
        if event.type != pygame.KEYDOWN:
            return
        if event.key == pygame.K_ESCAPE:
            game.change_state("menu")
            return
        if event.key in (pygame.K_e, pygame.K_SPACE):
            if self._dia_active:
                self._advance_dialogue()
                return
            self._try_interact(game)

    def _queue_dialogue(self, lines):
        self._dia_queue  = list(lines)
        self._dia_active = self._dia_queue.pop(0) if self._dia_queue else None

    def _advance_dialogue(self):
        if self._dia_queue:
            self._dia_active = self._dia_queue.pop(0)
        else:
            self._dia_active = None

    def _try_interact(self, game):
        sink = self.props["sink"]
        bed  = self.props["bed"]

        if not self._sink_done and sink.in_range(self.player):
            sink.collected = True
            self._sink_done = True
            self._queue_dialogue(self.SINK_LINES)
            return

        if self._sink_done and bed.in_range(self.player):
            game.change_state("scene3")

    def update(self, game, dt):
        keys = pygame.key.get_pressed()
        if not self._dia_active:
            self.player.update(keys, dt, self.bounds)
        if self._sink_done:
            self._dark_alpha = min(180, self._dark_alpha + int(60 * dt))
        self._fade_alpha = max(0, self._fade_alpha - int(255 * dt * 1.5))
        self._monitor.push(game.stress_mgr.stress_level)

    def draw(self, game, surface):
        cam = self._cam()
        # Slightly darkened evening palette
        surface.fill((30, 25, 40))
        pygame.draw.rect(surface, C_FLOOR_NIGHT,
                         (-cam, self.FLOOR_Y, self.WORLD_W, 200))
        pygame.draw.rect(surface, C_WALL_NIGHT,
                         (-cam, 0, self.WORLD_W, self.FLOOR_Y))
        for px in range(0, self.WORLD_W, 60):
            pygame.draw.line(surface, (15, 10, 8),
                             (px - cam, self.FLOOR_Y),
                             (px - cam, self.FLOOR_Y + 100), 2)
        # Window — dark outside
        pygame.draw.rect(surface, (10, 15, 30), (900 - cam, 120, 200, 150))
        pygame.draw.rect(surface, (80, 70, 60),  (900 - cam, 120, 200, 150), 5)

        self._draw_mom(surface, cam)

        for prop in self.props.values():
            prop.draw(surface, cam, prop.in_range(self.player))

        self.player.draw(surface, cam)

        # Room darkening overlay
        if self._dark_alpha > 0:
            ov = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
            ov.fill((0, 0, 0, self._dark_alpha))
            surface.blit(ov, (0, 0))

        self.draw_stress_bar(surface, game.stress_mgr.stress_level)
        self._monitor.draw(surface, game.stress_mgr.stress_level,
                           "MOCK" if game.stress_mgr.mock_mode else "MUSE EEG")

        if not self._sink_done:
            fnt = pygame.font.SysFont("monospace", 13)
            surface.blit(fnt.render("○ Help Mom with the dishes", True, (200,200,200)),
                         (20, 68))
        else:
            fnt = pygame.font.SysFont("monospace", 13)
            surface.blit(fnt.render("○ Go to bed", True, (200, 200, 200)),
                         (20, 68))

        if self._dia_active:
            draw_dialogue(surface, self._dia_active[0], self._dia_active[1])

        self.draw_fade(surface, self._fade_alpha)

    def _cam(self):
        cx = int(self.player.x + Player.W // 2 - SCREEN_W // 2)
        return max(0, min(cx, self.WORLD_W - SCREEN_W))

    @staticmethod
    def _draw_mom(surface, cam):
        mx = 350 - cam
        my = Scene2Evening.FLOOR_Y - 72
        pygame.draw.rect(surface, (160, 115, 90), (mx, my, 26, 72))
        pygame.draw.circle(surface, (185, 145, 115), (mx + 13, my - 16), 16)
        # ASSET: surface.blit(mom_sprite_evening, (mx, my - 16))


# =============================================================================
# ── SCENE 3: CLOSET CHOICE  ──────────────────────────────────────────────────
# =============================================================================

class Scene3Closet(State):
    """
    Player tries to sleep → closet creaks open.
    A timed menu asks: "Close the closet? Yes | No"

    EEG rules
    ---------
    - stress > STRESS_MED → "Yes" shrinks + greys out + becomes un-clickable
    - "No" grows bigger
    - Both buttons shake with amplitude ∝ stress
    - 5-second countdown auto-selects "No"
    - Closet banging SFX plays on a random interval
    """

    MENU_TIMEOUT = 6.5
    EYES_BLINK   = 1.2   # seconds per blink cycle

    def on_enter(self, game):
        game.scene_mgr.current_scene_name = "scene3"
        game.audio.eeg_active = True
        game.audio.play_music("tense_music", fade_ms=1000)
        # ASSET: game.audio.play_sfx("closet_creak")

        self._timer      = self.MENU_TIMEOUT
        self._chosen     = None          # "yes" | "no" | None
        self._blink_t    = 0.0
        self._fade_alpha = 255
        self._out_alpha  = 0             # fade out after choice
        self._vfx        = VFXLayer()
        self._monitor    = BrainwaveMonitor(SCREEN_W - GRAPH_W - 20, 20)
        self._done       = False

    def handle_event(self, game, event):
        if self._chosen or self._done:
            return
        stress = game.stress_mgr.stress_level
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            yes_r, no_r = self._button_rects(stress)
            mx, my = pygame.mouse.get_pos()
            if stress <= STRESS_MED and yes_r.collidepoint(mx, my):
                self._choose(game, "yes")
            elif no_r.collidepoint(mx, my):
                self._choose(game, "no")
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                game.change_state("menu")

    def _choose(self, game, choice):
        self._chosen = choice
        game.scene_mgr.game_flags["closet_closed"] = (choice == "yes")

    def update(self, game, dt):
        stress = game.stress_mgr.stress_level
        self._timer   = max(0.0, self._timer - dt)
        self._blink_t += dt
        self._vfx.update(stress, dt)
        self._monitor.push(stress)
        game.audio.update(stress, dt)
        game.audio.play_closet_bang()
        self._fade_alpha = max(0, self._fade_alpha - int(255 * dt * 1.5))

        # Auto-No on timeout
        if self._timer <= 0 and not self._chosen:
            self._choose(game, "no")

        # Fade out then transition
        if self._chosen and not self._done:
            self._out_alpha = min(255, self._out_alpha + int(255 * dt * 1.2))
            if self._out_alpha >= 255:
                self._done = True
                if self._chosen == "yes":
                    game.change_state("scene6")
                else:
                    game.change_state("scene4")

        self.check_panic(game)

    def draw(self, game, surface):
        stress = game.stress_mgr.stress_level

        # Bedroom — dark
        surface.fill((12, 8, 20))
        # Floor
        pygame.draw.rect(surface, (25, 18, 15),
                         (0, SCREEN_H - 100, SCREEN_W, 100))
        # Bed silhouette
        pygame.draw.rect(surface, (40, 30, 55),
                         (SCREEN_W // 2 - 180, SCREEN_H - 170, 360, 90))
        # ASSET: blit bedroom background here

        # Closet door (left side, ajar)
        self._draw_closet(surface, stress)

        # VFX
        self._vfx.draw(surface, stress)

        # Menu
        if not self._chosen:
            self._draw_menu(surface, stress)

        # Countdown bar
        pct = self._timer / self.MENU_TIMEOUT
        bar_w = 300
        bx = SCREEN_W // 2 - bar_w // 2
        by = SCREEN_H // 2 + 120
        pygame.draw.rect(surface, (40, 40, 40), (bx, by, bar_w, 10))
        pygame.draw.rect(surface, (200, 150, 0), (bx, by, int(bar_w * pct), 10))

        # HUD
        self.draw_stress_bar(surface, stress)
        self._monitor.draw(surface, stress,
                           "MOCK" if game.stress_mgr.mock_mode else "MUSE EEG")

        self.draw_fade(surface, self._fade_alpha)
        if self._chosen:
            self.draw_fade(surface, self._out_alpha)

    # ── Closet renderer ──────────────────────────────────────────────────────

    def _draw_closet(self, surface, stress):
        # Door frame
        pygame.draw.rect(surface, (35, 25, 20), (80, 150, 200, 380))
        # Ajar door (perspective transform faked with a narrow rect)
        ajar = int(30 + stress * 60)
        pygame.draw.rect(surface, (20, 14, 12), (80, 150, ajar, 380))
        pygame.draw.rect(surface, (80, 60, 45), (80, 150, 200, 380), 4)

        # Eyes in the darkness — blink with stress
        blink_on = (self._blink_t % self.EYES_BLINK) > (self.EYES_BLINK * 0.2)
        if blink_on:
            eye_alpha = int(80 + stress * 175)
            ec        = (255, int(200 * (1 - stress)), 0, eye_alpha)
            for ex in (120, 160):
                pygame.draw.circle(surface, ec[:3], (ex, 340), 10)
                pygame.draw.circle(surface, C_BLACK, (ex, 340), 4)
        # ASSET: blit closet sprite / eyes sprite here

    # ── Menu renderer ─────────────────────────────────────────────────────────

    def _button_rects(self, stress):
        """Returns (yes_rect, no_rect) in screen-space (already shaken)."""
        # Base sizes
        yes_base = 60;  no_base = 60
        scale     = (stress - STRESS_MED) / (1.0 - STRESS_MED) if stress > STRESS_MED else 0
        yes_size  = max(28, int(yes_base - scale * 30))
        no_size   = min(120, int(no_base + scale * 60))
        cx        = SCREEN_W // 2
        cy        = SCREEN_H // 2
        yes_r     = pygame.Rect(0, 0, yes_size * 4, yes_size)
        no_r      = pygame.Rect(0, 0, no_size  * 4, no_size)
        yes_r.center = (cx - 140, cy + 20)
        no_r.center  = (cx + 140, cy + 20)
        return yes_r, no_r

    def _draw_menu(self, surface, stress):
        shake = int(stress * 18)
        ox    = random.randint(-shake, shake) if shake else 0
        oy    = random.randint(-shake, shake) if shake else 0

        yes_r, no_r = self._button_rects(stress)
        yes_r = yes_r.move(ox, oy)
        no_r  = no_r.move(ox, oy)

        scale   = max(0.0, (stress - STRESS_MED) / (1.0 - STRESS_MED))
        yes_dis = stress > STRESS_MED   # disabled

        # Background panel
        panel = pygame.Surface((500, 180), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 190))
        cx = SCREEN_W // 2;  cy = SCREEN_H // 2
        surface.blit(panel, panel.get_rect(center=(cx + ox, cy + oy)))

        # Question text
        f_q = pygame.font.SysFont("monospace", 20, bold=True)
        q   = f_q.render("Close the closet?", True, C_WHITE)
        surface.blit(q, q.get_rect(center=(cx + ox, cy - 30 + oy)))

        # YES button
        yes_col  = (60, 60, 60) if yes_dis else (50, 150, 80)
        yes_tcol = (100, 100, 100) if yes_dis else C_WHITE
        pygame.draw.rect(surface, yes_col, yes_r, border_radius=6)
        pygame.draw.rect(surface, (180, 180, 180), yes_r, 2, border_radius=6)
        f_yes = pygame.font.SysFont("monospace", max(12, yes_r.height - 10), bold=True)
        yt    = f_yes.render("YES", True, yes_tcol)
        surface.blit(yt, yt.get_rect(center=yes_r.center))

        # NO button
        no_col = (160, 20, 20)
        pygame.draw.rect(surface, no_col, no_r, border_radius=6)
        pygame.draw.rect(surface, (255, 80, 80), no_r, 2, border_radius=6)
        f_no = pygame.font.SysFont("monospace", max(14, no_r.height - 10), bold=True)
        nt   = f_no.render("NO", True, C_WHITE)
        surface.blit(nt, nt.get_rect(center=no_r.center))

        if yes_dis:
            f_warn = pygame.font.SysFont("monospace", 12)
            w = f_warn.render("(too scared)", True, (180, 60, 60))
            surface.blit(w, w.get_rect(center=(yes_r.centerx, yes_r.bottom + 12)))


# =============================================================================
# ── SCENE 4: THE NIGHTMARE  ──────────────────────────────────────────────────
# =============================================================================

class Scene4Nightmare(State):
    """
    Terraria-style chase.  Monster stays ahead of the player by LEAD_DIST.
    Lower stress → monster slows → player can catch it.
    Touching the monster → Scene 5 (Bad Ending).
    """

    WORLD_W = 3000
    FLOOR_Y = SCREEN_H - 100

    def on_enter(self, game):
        game.scene_mgr.current_scene_name = "scene4"
        game.audio.eeg_active = True
        game.audio.play_music("tense_music", fade_ms=500)

        fl = float(self.FLOOR_Y - Player.H)
        self.player  = Player(x=300.0, y=fl)
        self.monster = NightmareMonster(x=600.0, y=float(self.FLOOR_Y - NightmareMonster.H))
        self.bounds  = pygame.Rect(0, 0, self.WORLD_W, self.FLOOR_Y)
        self._vfx    = VFXLayer()
        self._monitor= BrainwaveMonitor(SCREEN_W - GRAPH_W - 20, 20)
        self._fade_alpha = 255
        self._out_alpha  = 0
        self._touched    = False
        self._done       = False

    def handle_event(self, game, event):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            game.change_state("menu")

    def update(self, game, dt):
        keys   = pygame.key.get_pressed()
        stress = game.stress_mgr.stress_level
        self.player.update(keys, dt, self.bounds)
        self.monster.update(stress, self.player.x, dt)
        self._vfx.update(stress, dt)
        game.audio.update(stress, dt)
        self._monitor.push(stress)
        self._fade_alpha = max(0, self._fade_alpha - int(255 * dt * 1.5))
        self.check_panic(game)

        # Touch detection (player rect overlaps monster rect)
        if not self._touched:
            cam = self._cam()
            pr  = self.player.rect
            mr  = self.monster.rect
            if pr.colliderect(mr):
                self._touched = True

        if self._touched and not self._done:
            self._out_alpha = min(255, self._out_alpha + int(255 * dt * 0.8))
            if self._out_alpha >= 255:
                self._done = True
                game.change_state("scene5")

    def draw(self, game, surface):
        stress = game.stress_mgr.stress_level
        cam    = self._cam()

        # Dream-world background — deep purple/black
        surface.fill((8, 4, 20))
        pygame.draw.rect(surface, (18, 10, 30),
                         (-cam, self.FLOOR_Y, self.WORLD_W, 200))
        # Surreal "floating islands" floor segments
        for ix in range(0, self.WORLD_W, 200):
            h = 20 + int(10 * math.sin(ix * 0.02))
            pygame.draw.rect(surface, (40, 20, 60),
                             (ix - cam, self.FLOOR_Y, 140, h))

        self.monster.draw(surface, cam, stress)
        self.player.draw(surface, cam)
        self._vfx.draw(surface, stress)

        self.draw_stress_bar(surface, stress)
        self._monitor.draw(surface, stress,
                           "MOCK" if game.stress_mgr.mock_mode else "MUSE EEG")

        # Hint
        fnt = pygame.font.SysFont("monospace", 14)
        surface.blit(fnt.render("Stay calm to slow the monster — touch it to wake up",
                                True, (160, 120, 200)), (20, 68))

        self.draw_fade(surface, self._fade_alpha)
        if self._touched:
            self.draw_fade(surface, self._out_alpha)

    def _cam(self):
        cx = int(self.player.x + Player.W // 2 - SCREEN_W // 2)
        return max(0, min(cx, self.WORLD_W - SCREEN_W))


# =============================================================================
# ── SCENE 5: BAD ENDING  ─────────────────────────────────────────────────────
# =============================================================================

class Scene5BadEnding(State):
    """
    Player wakes up screaming → Mom appears → transforms into monster.
    Dialogue sequence, then fade to black, then return to menu.
    """

    LINES = [
        ("Mom: Is everything alright dear? It sounded like you were screaming just now.",       "Mom",  3.5),
        ("You: I just had a nightmare about a monster! It was chasing me all over the house!",  "You",  3.5),
        ("Mom: Oh honey. We've talked about this. Monsters aren't real.",                       "Mom",  3.0),
        ("You: But there was something in my closet! I saw it before I had my nightmare!",      "Mom",  3.0),
        ("Mom: It was just a dream. Go back to bed.",                                           "Mom",  4.0),
        ("You: But I'm too scared, Mom... I can't sleep.",                                      "You",  3.5),
        ("Mom: Ugh. When will you learn to grow up and face your fears?",                       "Mom",  4.5),
        ("...",                                                             "",     1.5),
    ]

    def on_enter(self, game):
        game.scene_mgr.current_scene_name = "scene5"
        game.audio.eeg_active = False
        game.audio.stop_all()

        self._line_idx   = 0
        self._line_timer = 0.0
        self._black_t    = 0.0
        self._in_black   = False
        self._fade_alpha = 255
        self._transform  = False   # mom has transformed

    def handle_event(self, game, event):
        if event.type == pygame.KEYDOWN and event.key in (pygame.K_e, pygame.K_SPACE):
            self._advance()

    def _advance(self):
        if self._line_idx < len(self.LINES) - 1:
            self._line_idx  += 1
            self._line_timer = 0.0
        else:
            self._in_black = True

    def update(self, game, dt):
        self._fade_alpha  = max(0, self._fade_alpha - int(255 * dt * 1.5))
        self._line_timer += dt

        if self._line_timer > self.LINES[self._line_idx][2]:
            self._advance()

        if self._line_idx == len(self.LINES) - 1:
            self._transform = True

        if self._in_black:
            self._black_t += dt
            if self._black_t >= 3.5:
                game.change_state("menu")

    def draw(self, game, surface):
        surface.fill((10, 5, 15))

        # Simple bedroom
        pygame.draw.rect(surface, (25, 18, 30),
                         (0, SCREEN_H - 100, SCREEN_W, 100))

        # Mom / monster
        if self._transform:
            self._draw_monster_mom(surface)
        else:
            self._draw_mom(surface)

        # Dialogue
        if self._line_idx < len(self.LINES) and not self._in_black:
            txt, spk, _ = self.LINES[self._line_idx]
            col = (120, 180, 120) if spk == "You" else (180, 120, 120)
            draw_dialogue(surface, txt, spk, portrait_col=col)

        self.draw_fade(surface, self._fade_alpha)
        if self._in_black:
            self.draw_fade(surface, int(min(255, self._black_t / 1.5 * 255)))

    @staticmethod
    def _draw_mom(surface):
        cx = SCREEN_W // 2
        pygame.draw.rect(surface, (160, 115, 90),  (cx - 20, SCREEN_H - 270, 40, 120))
        pygame.draw.circle(surface, (185, 145, 115), (cx, SCREEN_H - 290), 28)
        # ASSET: surface.blit(mom_sprite, (...))

    @staticmethod
    def _draw_monster_mom(surface):
        cx = SCREEN_W // 2
        # Distorted, red-tinted
        pygame.draw.rect(surface, (100, 20, 20), (cx - 30, SCREEN_H - 280, 60, 140))
        pygame.draw.ellipse(surface, (60, 10, 10), (cx - 50, SCREEN_H - 330, 100, 90))
        tmp   = pygame.Surface((50, 90), pygame.SRCALPHA)
        for i in range(7):
            tx = 5 + i * 6
            pygame.draw.polygon(tmp, (200, 0, 0), [(tx, 55), (tx + 4, 68), (tx + 8, 55)])
        for ex in (cx - 20, cx + 20):
            pygame.draw.circle(surface, (255, 50, 0), (ex, SCREEN_H - 300), 12)
            pygame.draw.circle(surface, C_BLACK, (ex, SCREEN_H - 300), 5)
        # ASSET: surface.blit(mom_monster_sprite, (...))


# =============================================================================
# ── SCENE 6: GOOD ENDING  ────────────────────────────────────────────────────
# =============================================================================

class Scene6GoodEnding(State):
    """
    Player wakes up refreshed → Mom is proud → vacation announcement.
    """

    LINES = [
        ("Mom: Good morning! How did you sleep?",                          "Mom", 4.0),
        ("You: I slept really well.",                                      "You", 3.0),
        ("Mom: Really? After our talk last night I thought you'd be scared.", "Mom", 4.5),
        ("You: No, I'm not scared. I feel great.",                         "You", 3.0),
        ("Mom: Wow, I'm so proud of you! You've really grown up and learned to face your fears.", "Mom", 4.0),
        ("Mom: Oh! I almost forgot. I finally saved enough money...",      "Mom", 4.0),
        ("You: Money for what, Mom?",                                      "You", 2.5),
        ("Mom: You know that vacation you've always wanted to go on?",     "Mom", 4.0),
        ("Mom: We're going to Cancun!",                                    "Mom", 5.0),
        ("...",                                                             "",   2.0),
    ]

    def on_enter(self, game):
        game.scene_mgr.current_scene_name = "scene6"
        game.audio.eeg_active = False
        game.audio.play_music("calm_music", fade_ms=3000)

        self._line_idx   = 0
        self._line_timer = 0.0
        self._black_t    = 0.0
        self._in_black   = False
        self._fade_alpha = 255
        self._sun_t      = 0.0

    def handle_event(self, game, event):
        if event.type == pygame.KEYDOWN and event.key in (pygame.K_e, pygame.K_SPACE):
            self._advance()

    def _advance(self):
        if self._line_idx < len(self.LINES) - 1:
            self._line_idx  += 1
            self._line_timer = 0.0
        else:
            self._in_black = True

    def update(self, game, dt):
        self._fade_alpha  = max(0, self._fade_alpha - int(255 * dt * 1.5))
        self._line_timer += dt
        self._sun_t      += dt
        if self._line_timer > self.LINES[self._line_idx][2]:
            self._advance()
        if self._in_black:
            self._black_t += dt
            if self._black_t >= 3.0:
                game.change_state("menu")

    def draw(self, game, surface):
        # Bright morning bedroom
        surface.fill((200, 220, 255))
        pygame.draw.rect(surface, (160, 130, 100),
                         (0, SCREEN_H - 100, SCREEN_W, 100))
        # Sunny window
        wx, wy = SCREEN_W // 2 - 100, 80
        pygame.draw.rect(surface, (255, 240, 150), (wx, wy, 200, 160))
        pygame.draw.rect(surface, (140, 120, 100), (wx, wy, 200, 160), 6)
        # Sun rays
        sc   = (wx + 100, wy + 80)
        rays = 8
        for i in range(rays):
            a = 2 * math.pi * i / rays + self._sun_t * 0.3
            ex= sc[0] + int(math.cos(a) * 90)
            ey= sc[1] + int(math.sin(a) * 90)
            pygame.draw.line(surface, (255, 220, 80), sc, (ex, ey), 3)
        pygame.draw.circle(surface, (255, 200, 50), sc, 30)

        # Mom
        cx = SCREEN_W // 2 + 150
        pygame.draw.rect(surface,   (200, 155, 120), (cx - 18, SCREEN_H - 260, 36, 110))
        pygame.draw.circle(surface, (225, 180, 145), (cx,      SCREEN_H - 275), 24)
        # ASSET: surface.blit(mom_happy_sprite, (...))

        # Dialogue
        if self._line_idx < len(self.LINES) and not self._in_black:
            txt, spk, _ = self.LINES[self._line_idx]
            col = (100, 160, 220) if spk == "You" else (220, 160, 100)
            draw_dialogue(surface, txt, spk, portrait_col=col)

        self.draw_fade(surface, self._fade_alpha)
        if self._in_black:
            ov = pygame.Surface((SCREEN_W, SCREEN_H)); ov.fill(C_WHITE)
            ov.set_alpha(int(min(255, self._black_t / 1.5 * 255)))
            surface.blit(ov, (0, 0))


# =============================================================================
# ── SCENE 7: FAKE CRASH + JUMPSCARE  ─────────────────────────────────────────
# =============================================================================

class FakeCrashState(State):
    HOLD = 3.5

    def on_enter(self, game):
        game.scene_mgr.current_scene_name = "fake_crash"
        game.audio.stop_all()
        self._surf  = build_fake_crash_surface()
        self._timer = 0.0
        self._done  = False

    def handle_event(self, game, event): pass  # swallow input

    def update(self, game, dt):
        self._timer += dt
        if self._timer >= self.HOLD and not self._done:
            self._done = True
            game.change_state("jumpscare")

    def draw(self, game, surface):
        surface.blit(self._surf, (0, 0))
        pct = min(100, int(self._timer / self.HOLD * 100))
        f   = pygame.font.SysFont("monospace", 15)
        surface.blit(f.render(f"Collecting error info ...  {pct}%",
                               True, (180, 180, 220)), (100, SCREEN_H - 130))


class JumpscareState(State):
    HOLD = 4.0

    def on_enter(self, game):
        game.scene_mgr.current_scene_name = "jumpscare"
        self._surf  = build_jumpscare_surface()
        self._timer = 0.0
        self._shake = 18.0
        game.audio.play_sfx("crash_sound");

    def handle_event(self, game, event):
        if event.type == pygame.KEYDOWN:
            game.change_state("menu")

    def update(self, game, dt):
        self._timer += dt
        self._shake  = max(0.0, self._shake - dt * 8)
        if self._timer >= self.HOLD:
            game.change_state("menu")

    def draw(self, game, surface):
        ox = random.randint(-int(self._shake), int(self._shake))
        oy = random.randint(-int(self._shake), int(self._shake))
        surface.blit(self._surf, (ox, oy))


# =============================================================================
# GAME  (main loop + state registry)
# =============================================================================

class Game:
    """
    Owns the window, clock, StressManager, AudioManager, SceneManager,
    and the state dictionary.  change_state() is the only way to transition.
    """

    def __init__(self):
        pygame.init()
        self.screen  = pygame.display.set_mode((SCREEN_W, SCREEN_H))
        pygame.display.set_caption(TITLE)
        self.clock   = pygame.time.Clock()
        self.running = True

        self.stress_mgr = StressManager(mock_mode=MOCK_MODE)
        self.audio      = AudioManager()
        self.scene_mgr  = SceneManager()

        # ── State registry ────────────────────────────────────────────────────
        # States that need fresh instances on every visit are re-created in
        # change_state() below.  Long-lived singletons live here permanently.
        self._states: dict[str, State] = {
            "menu"        : MenuState(),
            "calibration" : CalibrationState(),
            "scene1"      : Scene1Morning(),
            "scene2"      : Scene2Evening(),
            "scene3"      : Scene3Closet(),
            "scene4"      : Scene4Nightmare(),
            "scene5"      : Scene5BadEnding(),
            "scene6"      : Scene6GoodEnding(),
            "fake_crash"  : FakeCrashState(),
            "jumpscare"   : JumpscareState(),
        }
        self._current: State | None = None
        self.change_state("menu")

    # ── State machine ─────────────────────────────────────────────────────────

    # States that should always be freshly constructed on entry
    _FRESH = {"calibration", "scene1", "scene2", "scene3",
              "scene4", "scene5", "scene6", "fake_crash", "jumpscare"}

    _STATE_CLASSES = {
        "calibration" : CalibrationState,
        "scene1"      : Scene1Morning,
        "scene2"      : Scene2Evening,
        "scene3"      : Scene3Closet,
        "scene4"      : Scene4Nightmare,
        "scene5"      : Scene5BadEnding,
        "scene6"      : Scene6GoodEnding,
        "fake_crash"  : FakeCrashState,
        "jumpscare"   : JumpscareState,
    }

    def change_state(self, name: str):
        if self._current:
            self._current.on_exit(self)

        # Re-construct fresh states so they always initialise cleanly
        if name in self._FRESH:
            cls = self._STATE_CLASSES[name]
            self._states[name] = cls()
            # Also reset StressManager when starting a new calibration run
            if name == "calibration":
                self.stress_mgr = StressManager(mock_mode=MOCK_MODE)

        self._current = self._states[name]
        self.scene_mgr.current_scene_name = name

        # Tell AudioManager whether EEG effects should apply
        self.audio.eeg_active = self.scene_mgr.is_eeg_active()

        self._current.on_enter(self)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        while self.running:
            dt   = min(self.clock.tick(FPS) / 1000.0, 0.05)
            keys = pygame.key.get_pressed()

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                self._current.handle_event(self, event)

            self.stress_mgr.update(keys, dt)
            self._current.update(self, dt)
            self._current.draw(self, self.screen)

            # Persistent FPS counter
            fps_surf = pygame.font.SysFont("monospace", 11).render(
                f"FPS {int(self.clock.get_fps())}", True, (60, 60, 80))
            self.screen.blit(fps_surf, (SCREEN_W - 55, SCREEN_H - 16))

            pygame.display.flip()

        self.stress_mgr.shutdown()
        pygame.quit()
        sys.exit()


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    Game().run()