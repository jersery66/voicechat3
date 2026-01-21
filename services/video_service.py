import os
import time
import pygame
from moviepy import VideoFileClip
import threading

class VideoPlayer:
    def __init__(self):
        pass

    def play_video(self, file_path):
        """
        Play video in fullscreen (Kiosk mode).
        Blocks until video finishes or (Win + Esc) is pressed.
        """
        if not os.path.exists(file_path):
            print(f"[ERROR] Video file not found: {file_path}")
            return

        # Initialize Pygame
        pygame.init()
        pygame.mouse.set_visible(False) # Hide cursor

        # Fullscreen, No Frame
        screen_info = pygame.display.Info()
        screen_width = screen_info.current_w
        screen_height = screen_info.current_h
        
        # Create display surface - use (0,0) to use current desktop resolution
        # screen = pygame.display.set_mode((screen_width, screen_height), pygame.FULLSCREEN | pygame.NOFRAME)
        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN | pygame.NOFRAME)
        screen_width, screen_height = screen.get_size() # Get actual size
        pygame.display.set_caption("Relaxation Video")
        
        # Load Video using MoviePy
        try:
            clip = VideoFileClip(file_path)
            
            # Resize clip to fit screen maintenance aspect ratio
            # 'resize' might differ in moviepy v1 vs v2. Assuming v2 compat or v1.
            # safe resizing:
            w, h = clip.size
            ratio = min(screen_width/w, screen_height/h)
            new_size = (int(w * ratio), int(h * ratio))
            
            # Use 'resized(new_size)' method (MoviePy 2.0+) or 'resize(new_size)' (1.0)
            # Safe check
            if hasattr(clip, 'resized'):
                 clip_resized = clip.resized(new_size)
            else:
                 clip_resized = clip.resize(new_size)
                 
            # Centering offset
            x_pos = (screen_width - new_size[0]) // 2
            y_pos = (screen_height - new_size[1]) // 2

            # Prepare Audio
            # We use a hash of the file path to cache the audio, avoiding re-extraction delays
            import hashlib
            file_hash = hashlib.md5(file_path.encode()).hexdigest()
            temp_dir = os.path.join(os.path.dirname(file_path), "temp")
            if not os.path.exists(temp_dir):
                os.makedirs(temp_dir)
            
            audio_path = os.path.join(temp_dir, f"{file_hash}.wav")
            
            if not os.path.exists(audio_path):
                print(f"[INFO] Extracting audio (first time only)... Please wait.")
                # Show a loading screen using standard font if possible, or just console log
                # PyGAme default font
                font = pygame.font.SysFont("Arial", 36)
                text_surface = font.render(f"Loading...", True, (255, 255, 255))
                screen.blit(text_surface, (screen_width//2 - 50, screen_height//2))
                pygame.display.flip()
                
                try:
                    clip.audio.write_audiofile(audio_path, logger=None)
                except Exception as e:
                    print(f"[ERROR] Failed to extract audio: {e}")
                    # Try to play without audio or exit
            else:
                print(f"[INFO] Using cached audio: {audio_path}")
            
            try:
                pygame.mixer.init()
                pygame.mixer.music.load(audio_path)
                pygame.mixer.music.play()
            except Exception as e:
                 print(f"[WARNING] Audio play failed: {e}")

            clock = pygame.time.Clock()
            start_time = time.time()
            fps = clip.fps
            
            running = True
            
            for frame in clip_resized.iter_frames(fps=fps, dtype="uint8"):
                if not running:
                    break
                
                # Check events
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        # Ignore standard quit
                        pass
                    elif event.type == pygame.KEYDOWN:
                         # Check strict Backdoor: 
                         # Win+Esc is unreliable due to Start Menu focus stealing.
                         # Changed to: CTRL + ALT + Q
                         keys = pygame.key.get_pressed()
                         
                         ctrl_pressed = keys[pygame.K_LCTRL] or keys[pygame.K_RCTRL]
                         alt_pressed = keys[pygame.K_LALT] or keys[pygame.K_RALT]
                         q_pressed = keys[pygame.K_q]
                         
                         # Also keep Win+Esc just in case it works for some
                         win_pressed = keys[pygame.K_LGUI] or keys[pygame.K_RGUI]
                         esc_pressed = keys[pygame.K_ESCAPE]
                         
                         if (ctrl_pressed and alt_pressed and q_pressed) or (win_pressed and esc_pressed):
                             print("[INFO] Backdoor triggered: Forced Exit")
                             running = False
                
                # Convert frame (numpy) -> pygame surface
                # MoviePy frames are (Height, Width, 3) RGB
                # PyGame expects surfaces. 
                # make_surface expects (Width, Height, 3) but often needs transpose
                # MoviePy frame is typically Y, X. Pygame uses X, Y.
                # Actually MoviePy 'iter_frames' yields HxWx3 arrays. 
                # pygame.pixelcopy or just image.frombuffer is faster?
                # Simple implementation:
                # Transpose needed: No, MoviePy (y,x) vs Pygame (x,y)
                # Actually, pygame.surfarray.make_surface requires (W, H, 3).
                # MoviePy yields (H, W, 3). So we need swapaxes(0,1).
                
                import numpy as np
                frame_surface = pygame.surfarray.make_surface(frame.swapaxes(0, 1))
                
                # Blit centered
                screen.fill((0, 0, 0))
                screen.blit(frame_surface, (x_pos, y_pos))
                pygame.display.flip()
                
                # Sync logic
                # Ensure we track audio time
                # Simple FPS wait
                clock.tick(fps)
                
                # Check if audio finished?
                if not pygame.mixer.music.get_busy():
                    # Audio finished, wait a bit then exit? 
                    # Actually logic depends on frames. If frames done, we exit loop.
                    pass

        except Exception as e:
            print(f"[ERROR] Video playback error: {e}")
            import traceback
            traceback.print_exc()
            
        finally:
            print("[INFO] Cleaning up video player...")
            try:
                if pygame.mixer.get_init():
                    pygame.mixer.music.stop()
                    pygame.mixer.quit()
            except: pass
            
            try:
                pygame.quit()
            except: pass
            
            # Keep audio cache for faster subsequent plays
            # try:
            #     if os.path.exists(audio_path):
            #         os.remove(audio_path)
            # except: pass
            
        return

_video_player = None
def get_video_player():
    global _video_player
    if _video_player is None:
        _video_player = VideoPlayer()
    return _video_player
