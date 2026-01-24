# Voice Chat Application - Tkinter Version
import os
import sys
import time
import threading
import queue
from typing import Any
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from PIL import Image, ImageTk, ImageFilter, ImageEnhance

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.stt_service import STTService
from services.llm_service import LLMService
from services.tts_service import TTSService
from services.video_service import get_video_player
from services.report_service import ReportService, EndType
from services.report_generator import get_pdf_generator
from data.data_manager import DataManager
import hashlib
import soundfile as sf
from config import APP_NAME, CRISIS_HOTLINES, GREETING_MESSAGE, GREETING_VARIANTS, POST_RELAXATION_MESSAGE, FILL_INFO_PROMPT, TRANSITION_PROMPT, SUGGESTIONS_PROMPT, CONTINUE_CHAT_MESSAGE, MIN_ROUNDS_FOR_RELAXATION, POST_RELAXATION_TIMEOUT, TIMEOUT_END_MESSAGE, VOICE_PROMPT_PATH, VOICE_PROMPT_TEXT

class VoiceChatApp:
    def __init__(self):
        self.stt_service = None
        self.llm_service = None
        self.tts_service = None
        self.data_manager = None
        self.report_service = None  # Report generation service
        
        self.is_recording = False
        self.models_loaded = False
        self.processing_queue = queue.Queue()
        self.current_video_process = None
        
        # Session tracking for report generation
        self.session_ended = False
        self.current_user_id = None  # Track current user ID for change detection
        
        # UI related
        self.root = None
        self.status_var = None
        self.current_user_var = None
        self.chat_text = None
        self.load_thread = None
        
        self.createUI()


    def _create_glass_image(self, x, y, width, height, alpha=0.85):
        """Create a frosted glass effect image from background for a specific region."""
        if not hasattr(self, 'bg_image_with_padding'):
            return None
            
        try:
            # Crop the region
            crop = self.bg_image_with_padding.crop((x, y, x + width, y + height))
            
            # Blur
            blur = crop.filter(ImageFilter.GaussianBlur(radius=15))
            
            # Brighten/Overlay
            enhancer = ImageEnhance.Brightness(blur)
            bright = enhancer.enhance(1.1)
            
            # Create white overlay
            overlay = Image.new('RGBA', bright.size, (255, 255, 255, int(255 * alpha)))
            
            # Composite (convert bright to RGBA first)
            final = Image.alpha_composite(bright.convert('RGBA'), overlay)
            
            return ImageTk.PhotoImage(final)
        except Exception as e:
            print(f"Failed to create glass effect: {e}")
            return None

    def createUI(self):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        
        # Fullscreen mode (keep window in taskbar for easy alt-tab switching)
        self.root.attributes('-fullscreen', True)
        # Note: Not using overrideredirect(True) to allow Alt+Tab and taskbar access
        
        # Get screen dimensions and set geometry
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        self.root.geometry(f"{screen_width}x{screen_height}+0+0")

        # Set window opacity
        self.root.attributes('-alpha', 1)

        # Status variable
        self.status_var = tk.StringVar()
        self.status_var.set("正在初始化...")
        
        # Loading progress variable
        self.loading_progress_var = tk.DoubleVar()
        self.loading_progress_var.set(0)
        
        # Loading step variable
        self.loading_step_var = tk.StringVar()
        self.loading_step_var.set("准备中...")
        
        # Store flag to track if main UI is buil
        self.main_ui_built = False

        # Load background image first
        app_dir = os.path.dirname(os.path.abspath(__file__))
        bg_path = os.path.join(app_dir, "ui", "background.jpg")
        
        try:
            if os.path.exists(bg_path):
                self.bg_image_orig = Image.open(bg_path)
                screen_width = self.root.winfo_screenwidth()
                screen_height = self.root.winfo_screenheight()

                img_ratio = self.bg_image_orig.width / self.bg_image_orig.height
                screen_ratio = screen_width / screen_height

                self.bg_image_with_padding = Image.new('RGB', (screen_width, screen_height), (255, 255, 255))

                if img_ratio > screen_ratio:
                    new_width = screen_width
                    new_height = int(screen_width / img_ratio)
                    y_pos = (screen_height - new_height) // 2
                    x_pos = 0
                else:
                    new_height = screen_height
                    new_width = int(screen_height * img_ratio)
                    x_pos = (screen_width - new_width) // 2
                    y_pos = 0

                self.bg_image_resized = self.bg_image_orig.resize((new_width, new_height), Image.Resampling.LANCZOS)
                self.bg_image_with_padding.paste(self.bg_image_resized, (x_pos, y_pos))

                self.bg_photo = ImageTk.PhotoImage(self.bg_image_with_padding)

                self.bg_label = tk.Label(self.root, image=self.bg_photo)
                self.bg_label.place(x=0, y=0, relwidth=1, relheight=1)
            else:
                # Create gradient background if no image
                self.root.configure(bg="#1a1a2e")

        except Exception as e:
            print(f"Failed to load background: {e}")
            self.root.configure(bg="#1a1a2e")
        
        # ========== Loading Screen UI ==========
        # Create a frosted glass style card (semi-transparent white)
        # Note: True transparency requires platform-specific solutions
        # We use a light color that simulates frosted glass effect
        self.loading_frame = tk.Frame(self.root, bg="#f5f5f7", bd=0, highlightthickness=1, 
                                       highlightbackground="#d0d0d5")
        self.loading_frame.place(relx=0.5, rely=0.5, anchor="center", width=420, height=300)
        
        # Inner padding frame for cleaner layout
        inner_frame = tk.Frame(self.loading_frame, bg="#f5f5f7")
        inner_frame.pack(expand=True, fill=tk.BOTH, padx=20, pady=15)
        
        # App icon/logo area (using emoji)
        self.logo_label = tk.Label(inner_frame, text="🎙️", font=("Segoe UI Emoji", 42),
                                    bg="#f5f5f7", fg="#333333")
        self.logo_label.pack(pady=(10, 8))
        
        # App title
        self.title_label = tk.Label(inner_frame, text=APP_NAME, 
                                     font=("Microsoft YaHei", 20, "bold"),
                                     bg="#f5f5f7", fg="#2c3e50")
        self.title_label.pack(pady=(0, 3))
        
        # Subtitle
        self.subtitle_label = tk.Label(inner_frame, text="AI语音对话助手", 
                                        font=("Microsoft YaHei", 10),
                                        bg="#f5f5f7", fg="#7f8c8d")
        self.subtitle_label.pack(pady=(0, 18))
        
        # Progress bar with frosted glass style (soft blue-green gradient)
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("Loading.Horizontal.TProgressbar", 
                        troughcolor='#e0e0e5',
                        background='#3498db',
                        lightcolor='#5dade2',
                        darkcolor='#2980b9',
                        bordercolor='#d5d5da',
                        thickness=6)
        
        self.progress_bar = ttk.Progressbar(inner_frame, 
                                             style="Loading.Horizontal.TProgressbar",
                                             orient="horizontal", 
                                             length=320, 
                                             mode="determinate",
                                             variable=self.loading_progress_var)
        self.progress_bar.pack(pady=(8, 12))
        
        # Loading step text
        self.step_label = tk.Label(inner_frame, textvariable=self.loading_step_var,
                                    font=("Microsoft YaHei", 10),
                                    bg="#f5f5f7", fg="#34495e")
        self.step_label.pack(pady=(0, 4))
        
        # Detailed status text  
        self.status_label_loading = tk.Label(inner_frame, textvariable=self.status_var,
                                              font=("Microsoft YaHei", 9),
                                              bg="#f5f5f7", fg="#95a5a6")
        self.status_label_loading.pack(pady=(0, 10))
        
        # Animated dots for visual feedback
        self.dot_animation_index = 0
        self.animate_loading_dots()
        
        self.root.update()

        # ========== Link UI with Background (Glass Effect) ==========
        # Calculate panel positions for Glass Effect
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        
        # Control Panel: 300px wide, 70% height, Left positioned (relx=0.05)
        # Control Panel: 300px wide, 60% height, Left positioned (relx=0.05)
        self.ctrl_w = 300
        self.ctrl_h = int(screen_height * 0.6)
        ctrl_x = int(screen_width * 0.05)
        ctrl_y = int((screen_height - self.ctrl_h) / 2)
        
        self.glass_ctrl_img = self._create_glass_image(ctrl_x, ctrl_y, self.ctrl_w, self.ctrl_h, alpha=0.7)
        
        # Chat Panel: 450px wide, 60% height, Right positioned (relx=0.95 anchor e -> right edge at 0.95)
        self.chat_w = 450
        self.chat_h = int(screen_height * 0.6)
        chat_x = int(screen_width * 0.95) - self.chat_w
        chat_y = int((screen_height - self.chat_h) / 2)
        
        self.glass_chat_img = self._create_glass_image(chat_x, chat_y, self.chat_w, self.chat_h, alpha=0.8)

        # Left Control Panel (hidden during loading)
        # Use a generic bg color that matches the glass tint (light white/gray)
        self.control_frame = tk.Frame(self.root, bg="#f7f7f7", relief=tk.FLAT, bd=0)
        if self.glass_ctrl_img:
            self.control_bg_label = tk.Label(self.control_frame, image=self.glass_ctrl_img, bd=0)
            self.control_bg_label.place(x=0, y=0, relwidth=1, relheight=1)
            # Ensure bg label is at bottom
            self.control_bg_label.lower()

        # Right Chat Area (hidden during loading)
        self.chat_frame = tk.Frame(self.root, bg="#fbfbfb", relief=tk.FLAT, bd=0)
        if self.glass_chat_img:
            self.chat_bg_label = tk.Label(self.chat_frame, image=self.glass_chat_img, bd=0)
            self.chat_bg_label.place(x=0, y=0, relwidth=1, relheight=1)
            self.chat_bg_label.lower()

        # ========== User Info Section ==========
        # Make LabelFrame background match the simplified look
        user_info_frame = tk.LabelFrame(self.control_frame, text="基本信息（必填）", 
                                         bg="#f9f9f9", font=("Arial", 10, "bold"),
                                         fg="#333")
        user_info_frame.pack(pady=10, fill=tk.X, padx=5)
        
        # Store user info
        self.user_info = {}
        self.info_confirmed = False
        
        # Row 1: 编号
        row1 = tk.Frame(user_info_frame, bg="#f9f9f9")
        row1.pack(fill=tk.X, pady=2)
        tk.Label(row1, text="编号:", width=8, anchor="e", bg="#f9f9f9", font=("Arial", 9)).pack(side=tk.LEFT)
        self.user_id_entry = tk.Entry(row1, width=15, font=("Arial", 9))
        self.user_id_entry.pack(side=tk.LEFT, padx=5)
        self.user_id_entry.insert(0, "")
        
        # Row 2: 性别
        row2 = tk.Frame(user_info_frame, bg="#f9f9f9")
        row2.pack(fill=tk.X, pady=2)
        tk.Label(row2, text="性别:", width=8, anchor="e", bg="#f9f9f9", font=("Arial", 9)).pack(side=tk.LEFT)
        self.gender_var = tk.StringVar(value="")
        self.gender_combo = ttk.Combobox(row2, textvariable=self.gender_var, width=12,
                                          values=["男", "女"], state="readonly", font=("Arial", 9))
        self.gender_combo.pack(side=tk.LEFT, padx=5)
        
        # Row 3: 年龄
        row3 = tk.Frame(user_info_frame, bg="#f9f9f9")
        row3.pack(fill=tk.X, pady=2)
        tk.Label(row3, text="年龄:", width=8, anchor="e", bg="#f9f9f9", font=("Arial", 9)).pack(side=tk.LEFT)
        self.age_entry = tk.Entry(row3, width=15, font=("Arial", 9))
        self.age_entry.pack(side=tk.LEFT, padx=5)
        
        # Row 4: 文化程度
        row4 = tk.Frame(user_info_frame, bg="#f9f9f9")
        row4.pack(fill=tk.X, pady=2)
        tk.Label(row4, text="文化程度:", width=8, anchor="e", bg="#f9f9f9", font=("Arial", 9)).pack(side=tk.LEFT)
        self.education_var = tk.StringVar(value="")
        self.education_combo = ttk.Combobox(row4, textvariable=self.education_var, width=12,
                                             values=["小学", "初中", "高中/中专", "大专", "本科及以上"],
                                             state="readonly", font=("Arial", 9))
        self.education_combo.pack(side=tk.LEFT, padx=5)
        
        # Row 5: 婚姻状况
        row5 = tk.Frame(user_info_frame, bg="#f9f9f9")
        row5.pack(fill=tk.X, pady=2)
        tk.Label(row5, text="婚姻状况:", width=8, anchor="e", bg="#f9f9f9", font=("Arial", 9)).pack(side=tk.LEFT)
        self.marital_var = tk.StringVar(value="")
        self.marital_combo = ttk.Combobox(row5, textvariable=self.marital_var, width=12,
                                           values=["未婚", "已婚", "离异", "丧偶"],
                                           state="readonly", font=("Arial", 9))
        self.marital_combo.pack(side=tk.LEFT, padx=5)
        
        # Row 6: 毒品类型
        row6 = tk.Frame(user_info_frame, bg="#f9f9f9")
        row6.pack(fill=tk.X, pady=2)
        tk.Label(row6, text="毒品类型:", width=8, anchor="e", bg="#f9f9f9", font=("Arial", 9)).pack(side=tk.LEFT)
        self.drug_type_var = tk.StringVar(value="")
        self.drug_type_combo = ttk.Combobox(row6, textvariable=self.drug_type_var, width=12,
                                             values=["冰毒", "海洛因", "大麻", "K粉", "摇头丸", "混合", "其他"],
                                             state="readonly", font=("Arial", 9))
        self.drug_type_combo.pack(side=tk.LEFT, padx=5)
        
        # Confirm button
        self.btn_confirm_user = tk.Button(user_info_frame, text="确认信息并开始", width=18, 
                                          command=self._confirm_user_info,
                                          bg="#4CAF50", fg="white", font=("Arial", 9, "bold"))
        self.btn_confirm_user.pack(pady=8)
        
        # Modify/New Session button (initially disabled)
        self.btn_modify_user = tk.Button(user_info_frame, text="修改信息 / 新会话", width=18,
                                         command=self._on_modify_user_click,
                                         bg="#FF9800", fg="white", font=("Arial", 9, "bold"))
        self.btn_modify_user.pack(pady=(0, 8))
        self.btn_modify_user.config(state="disabled", bg="#ddd")
        
        # Status label
        self.confirmed_user_id = ""
        self.current_user_var = tk.StringVar(value="请填写基本信息后开始对话")
        tk.Label(user_info_frame, textvariable=self.current_user_var,
                  bg="#f9f9f9", font=("Arial", 9), fg="red").pack(anchor="w", padx=5)

        # Buttons
        self.btn_start = tk.Button(self.control_frame, text="开始录音", width=20, height=2, 
                                   command=self.startRec, bg="#81C784", fg="white", font=("Arial", 10, "bold"))
        
        self.btn_stop = tk.Button(self.control_frame, text="停止录音并发送", width=20, height=2, 
                                  command=self.stopRec, bg="#E0E0E0", state="disabled") # Gray initially

        self.relaxation_label = tk.Label(self.control_frame, text="放松训练", font=("Arial", 12, "bold"),
                                         bg="#f7f7f7", fg="#333333")
        
        self.btn_breathing = tk.Button(self.control_frame, text="呼吸放松训练", width=20, height=1,
                                       command=lambda: self._play_video_with_animation(self.btn_breathing, "#64B5F6", "呼吸训练.mp4"),
                                       bg="#64B5F6", fg="white", font=("Arial", 10))
        self.btn_muscle = tk.Button(self.control_frame, text="肌肉放松训练", width=20, height=1,
                                    command=lambda: self._play_video_with_animation(self.btn_muscle, "#4DD0E1", "肌肉放松.mp4"),
                                    bg="#4DD0E1", fg="white", font=("Arial", 10))
        self.btn_meditation = tk.Button(self.control_frame, text="冥想放松训练", width=20, height=1,
                                        command=lambda: self._play_video_with_animation(self.btn_meditation, "#9575CD", "冥想训练.mp4"),
                                        bg="#9575CD", fg="white", font=("Arial", 10))
        # self.btn_stop_video removed as requested
        
        self.btn_clear = tk.Button(self.control_frame, text="清除对话历史", width=20, height=2,
                                   command=self.clearHistory, bg="#E0E0E0")
        self.btn_exit = tk.Button(self.control_frame, text="退出程序", width=20, height=2, 
                                  command=self.exitApp, bg="#EF5350", fg="white", font=("Arial", 10, "bold"))

        self.status_label = tk.Label(self.control_frame, textvariable=self.status_var, fg="#1565C0", bg="#f7f7f7",
                                     wraplength=280, justify=tk.LEFT, font=("Arial", 9))

        # Packing
        self.btn_start.pack(pady=5)
        self.btn_stop.pack(pady=5)
        
        self.relaxation_label.pack(pady=(15, 5))
        self.btn_breathing.pack(pady=2)
        self.btn_muscle.pack(pady=2)
        self.btn_meditation.pack(pady=2)
        # self.btn_stop_video.pack(pady=5) - Removed

        self.btn_clear.pack(pady=10)
        self.btn_exit.pack(pady=10) # Added more padding for visibility
        self.status_label.pack(pady=10, fill=tk.X)

        # Chat Area
        self.chat_title = tk.Label(self.chat_frame, text="对话记录", font=("Arial", 14, "bold"), bg="#ffffff")
        self.chat_title.pack(pady=5)

        self.scrollbar = tk.Scrollbar(self.chat_frame)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.chat_text = tk.Text(self.chat_frame, yscrollcommand=self.scrollbar.set,
                                 wrap=tk.WORD, state=tk.DISABLED, bg="#ffffff",
                                 font=("Microsoft YaHei", 12)) # Better font for reading
        self.chat_text.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        self.scrollbar.config(command=self.chat_text.yview)

        # Styles
        self.chat_text.tag_configure("user", foreground="#1E88E5", font=("Microsoft YaHei", 12, "bold"))
        self.chat_text.tag_configure("ai", foreground="#43A047", font=("Microsoft YaHei", 12))
        self.chat_text.tag_configure("system", foreground="grey", font=("Microsoft YaHei", 10, "italic"))

        # Initialize
        self.setButtonsState("disabled")
        
        # Load models in background
        self.load_thread = threading.Thread(target=self.loadModels, daemon=True)
        self.load_thread.start()
        
        # Start queue processor
        self.root.after(100, self.process_queue)
        self.root.mainloop()

    def animate_loading_dots(self):
        """Animate loading dots for visual feedback."""
        if not hasattr(self, 'loading_frame') or self.loading_frame is None:
            return  # Stop animation if loading frame is destroyed
            
        dots = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self.dot_animation_index = (self.dot_animation_index + 1) % len(dots)
        
        # Update logo with animation
        if hasattr(self, 'logo_label') and self.logo_label.winfo_exists():
            current_step = self.loading_step_var.get()
            if "加载完成" not in current_step:
                self.logo_label.config(text=f"🎙️ {dots[self.dot_animation_index]}")
            else:
                self.logo_label.config(text="✨🎙️✨")
        
        # Schedule next animation frame
        self.root.after(100, self.animate_loading_dots)

    def process_queue(self):
        try:
            while True:
                task = self.processing_queue.get_nowait()
                msg_type, content = task
                
                if msg_type == "status":
                    self.status_var.set(content)
                elif msg_type == "loading_progress":
                    # Update progress bar value
                    self.loading_progress_var.set(content)
                elif msg_type == "loading_step":
                    # Update loading step text
                    self.loading_step_var.set(content)
                elif msg_type == "append_chat":
                    role, text = content
                    self.append_to_chat(role, text)
                elif msg_type == "stream_chat":
                     # Directly append text chunk to end (Smart Streaming)
                    self.chat_text.config(state=tk.NORMAL)
                    self.chat_text.insert(tk.END, content, "ai")
                    self.chat_text.see(tk.END)
                    self.chat_text.update_idletasks() # Force redraw for smooth streaming
                    self.chat_text.config(state=tk.DISABLED)
                elif msg_type == "stream_update":
                    # Directly append text chunk to end
                    self.chat_text.config(state=tk.NORMAL)
                    self.chat_text.insert(tk.END, content, "ai")
                    self.chat_text.see(tk.END)
                    self.chat_text.config(state=tk.DISABLED)
                elif msg_type == "stream_end":
                    # Append newline
                    self.chat_text.config(state=tk.NORMAL)
                    self.chat_text.insert(tk.END, "\n")
                    self.chat_text.see(tk.END)
                    self.chat_text.config(state=tk.DISABLED)
                elif msg_type == "clean_last_ai_response":
                    # Replace last AI response with cleaned text (no tags/analysis)
                    self._replace_last_ai_response(content)
                elif msg_type == "enable_ui":
                    # Start transition animation
                    self._animate_transition_to_main_ui()
                elif msg_type == "check_relaxation_recommendation":
                    # Check AI response for relaxation training recommendations
                    self._highlight_recommended_buttons(content)
                elif msg_type == "session_warning":
                    # Show time/round limit warning
                    self.append_to_chat("system", content)
                elif msg_type == "session_end":
                    # Session ended - show report dialog
                    # Unpack potentially 4 elements
                    if len(content) == 4:
                        end_type, visitor_feedback, relaxation_rec, audio_data = content
                    else:
                        end_type, visitor_feedback, relaxation_rec = content
                        audio_data = None
                        
                    self._show_session_end_dialog(end_type, visitor_feedback, relaxation_rec, audio_data)
                elif msg_type == "show_crisis_resources":
                    # Show crisis hotlines for safety endings
                    self._show_crisis_resources_dialog()
                elif msg_type == "play_greeting":
                    # Play opening greeting after UI is ready
                    self.root.after(1000, self._play_opening_greeting)  # Delay 1s after UI shows
                elif msg_type == "post_relaxation_greeting":
                    # Play greeting after relaxation video finishes
                    self.root.after(500, self._play_post_relaxation_greeting)
                elif msg_type == "fill_info_prompt":
                    # Play prompt guiding visitor to fill basic info
                    self.root.after(1000, self._play_fill_info_prompt)
                elif msg_type == "start_keepalive":
                    # Start Ollama keep-alive timer
                    self._start_ollama_keepalive()
                elif msg_type == "error":
                    messagebox.showerror("错误", content)
                    self.status_var.set("发生错误")
                    self.setButtonsState("normal")
                
                elif msg_type == "show_continue_dialog":
                    # Show continue/end dialog after relaxation
                    self.root.after(100, self._show_continue_or_end_dialog)
                
                elif msg_type == "set_buttons_state":
                    self.setButtonsState(content)
                
                elif msg_type == "close_loading_popup":
                    try:
                        content.destroy()
                    except:
                        pass
                
                self.processing_queue.task_done()
        except queue.Empty:
            pass
        
        self.root.after(50, self.process_queue) # Faster UI update for typing effect

    def loadModels(self):
        """Load all models with progress updates."""
        try:
            # Step 1: STT Model (0-30%)
            self.processing_queue.put(("loading_step", "步骤 1/4: 语音识别模块"))
            self.processing_queue.put(("status", "正在加载语音识别模型..."))
            self.processing_queue.put(("loading_progress", 5))
            self.stt_service = STTService()
            self.stt_service.load_model()
            self.processing_queue.put(("loading_progress", 15))
            self.processing_queue.put(("status", "正在预热语音识别..."))
            self.stt_service.warmup()
            self.processing_queue.put(("loading_progress", 25))
            
            # Step 2: TTS Model (30-70%)
            self.processing_queue.put(("loading_step", "步骤 2/4: 语音合成模块"))
            self.processing_queue.put(("status", "正在加载语音合成模型..."))
            self.processing_queue.put(("loading_progress", 30))
            self.tts_service = TTSService()
            self.tts_service.load_model(use_streaming=True)
            self.processing_queue.put(("loading_progress", 50))
            self.processing_queue.put(("status", "正在预热语音合成..."))
            self.tts_service.warmup()
            self.processing_queue.put(("loading_progress", 70))
            
            # Step 3: LLM Service (70-90%)
            self.processing_queue.put(("loading_step", "步骤 3/4: 智能对话模块"))
            self.processing_queue.put(("status", "正在连接智能助手..."))
            self.processing_queue.put(("loading_progress", 75))
            self.llm_service = LLMService()
            if not self.llm_service.test_connection():
                self.processing_queue.put(("error", "无法连接到 Ollama 服务"))
                return
            self.processing_queue.put(("loading_progress", 80))
            self.processing_queue.put(("status", "正在预热智能助手..."))
            self.llm_service.warmup()
            self.processing_queue.put(("loading_progress", 90))
            
            # Step 4: Data Manager & Report Service (90-100%)
            self.processing_queue.put(("loading_step", "步骤 4/4: 初始化数据"))
            self.processing_queue.put(("status", "正在初始化数据管理..."))
            self.data_manager = DataManager()
            self.data_manager.start_new_session()
            
            # Initialize report service
            self.report_service = ReportService(self.llm_service)
            self.report_service.start_session()
            self.session_ended = False
            
            self.processing_queue.put(("loading_progress", 100))
            self.processing_queue.put(("loading_step", "✅ 加载完成！"))
            self.processing_queue.put(("status", "准备就绪"))
            
            # Small delay to show 100%
            time.sleep(0.3)
            
            self.models_loaded = True
            self.processing_queue.put(("enable_ui", None))
            
            # Play voice prompt to guide user to fill basic info
            self.processing_queue.put(("fill_info_prompt", None))
            
            # Start Ollama keep-alive timer (every 3 minutes)
            self.processing_queue.put(("start_keepalive", None))
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.processing_queue.put(("error", f"模型加载失败: {str(e)}"))

    def startRec(self):
        if not self.models_loaded:
            return
        
        # Check if user info is confirmed
        if not self.info_confirmed:
            messagebox.showwarning("请先填写信息", "请先填写并确认基本信息后再开始对话")
            return
        
        # Check if user ID changed - if so, start new session with greeting
        new_user_id = self.user_id_entry.get().strip() or "default_user"
        if self.current_user_id is not None and new_user_id != self.current_user_id:
            print(f"[INFO] 用户编号变更: {self.current_user_id} -> {new_user_id}")
            # Trigger new session
            self.chat_text.config(state=tk.NORMAL)
            self.chat_text.delete(1.0, tk.END)
            self.chat_text.config(state=tk.DISABLED)
            if self.llm_service:
                self.llm_service.reset_conversation()
            if self.data_manager:
                self.data_manager.start_new_session()
            if self.report_service:
                self.report_service.start_session()
            self.session_ended = False
            self.current_user_id = new_user_id
            # Play greeting and return (don't start recording yet)
            self._play_opening_greeting()
            self.status_var.set("用户已更换，请等待问候后再录音")
            return
        
        # Remember current user ID
        if self.current_user_id is None:
            self.current_user_id = new_user_id
        
        self.is_recording = True
        self.btn_start.config(state="disabled", bg="#E57373") # Active Red
        self.btn_stop.config(state="normal", bg="#E0E0E0")
        self.status_var.set("🎙️ 正在录音...")
        
        # Stop any button highlight animation
        self._stop_highlight_animation()
        
        # Start STT recording
        threading.Thread(target=self.stt_service.start_recording, daemon=True).start()

    def stopRec(self):
        if not self.is_recording:
            return
            
        self.is_recording = False
        # self.btn_start.config(state="normal", bg="#81C784") # Keep disabled until processing finishes
        self.btn_stop.config(state="disabled")
        self.status_var.set("⏳ 正在处理...")
        
        # Start processing thread
        threading.Thread(target=self._process_pipeline, daemon=True).start()

    def _process_pipeline(self):
        try:
            # Check if session already ended
            if self.session_ended:
                self.processing_queue.put(("status", "⚠️ 会话已结束，请开始新对话"))
                return
            
            # 1. Get Audio
            audio_data = self.stt_service.stop_recording()
            
            if len(audio_data) == 0:
                self.processing_queue.put(("status", "⚠️ 未检测到语音"))
                return

            # 2. Transcribe
            self.processing_queue.put(("status", "📝 正在转写..."))
            text = self.stt_service.transcribe(audio_data)
            
            if not text.strip():
                self.processing_queue.put(("status", "⚠️ 无法识别内容"))
                return
                
            self.processing_queue.put(("append_chat", ("user", text)))
            
            # Increment round count for report service
            self.report_service.increment_round()
            
            # Check time/round warning
            should_warn, warning_msg = self.report_service.should_warn_time_limit()
            if should_warn:
                self.processing_queue.put(("session_warning", warning_msg))
            
            # Save User Data
            user_id = self.user_id_entry.get().strip() or "default_user"
            self.data_manager.set_user_id(user_id)
            self.data_manager.save_user_message(audio_data, text)

            # 3. LLM Generation (buffered - don't stream raw output to UI)
            self.processing_queue.put(("status", "🤔 正在思考..."))
            
            full_response = ""
            
            # Check round threshold
            current_rounds = self.report_service.get_round_count()
            allow_relaxation = (current_rounds >= MIN_ROUNDS_FOR_RELAXATION)
            
            system_suffix = None
            if not allow_relaxation:
                system_suffix = f"【系统警告】当前仅第{current_rounds}轮对话（少于{MIN_ROUNDS_FOR_RELAXATION}轮）。无论用户说了什么，你绝对禁止推荐放松训练！禁止诱导用户看左边按钮！继续通过对话建立关系。"
                print(f"[DEBUG] Blocking relaxation recommendation (Round {current_rounds}/{MIN_ROUNDS_FOR_RELAXATION})")
            
            # Creating a generator for LLM
            start_time = time.time()
            llm_gen = self.llm_service.chat(text, system_suffix=system_suffix)
            
            # Smart Streaming Implementation
            full_response = ""
            analysis_text = ""
            spoken_text = ""
            found_separator = False
            first_token_time = None
            
            # Start a new AI message line in chat for streaming
            self.processing_queue.put(("append_chat", ("ai_start", "")))
            
            # Streaming Filter Logic
            stream_buffer = ""
            possible_tags = ["[REC_BREATHING]", "[REC_MUSCLE]", "[REC_MEDITATION]", 
                             "[END_GOAL_ACHIEVED]", "[END_TIME_LIMIT]", "[END_SAFETY]", "[END_INVALID]", "[END_QUIT]"]
            
            def stream_and_filter(text_chunk):
                nonlocal stream_buffer
                stream_buffer += text_chunk
                
                # 1. Remove complete tags if present
                for tag in possible_tags:
                    if tag in stream_buffer:
                        print(f"[DEBUG] Detecting and removing tag from stream: {tag}")
                        stream_buffer = stream_buffer.replace(tag, "")
                
                # 2. Determine what is safe to stream
                if "[" in stream_buffer:
                    # Find the first '['
                    idx = stream_buffer.find("[")
                    
                    # Anything before '[' is definitely safe
                    safe_part = stream_buffer[:idx]
                    potential_tag = stream_buffer[idx:]
                    
                    # Check if potential_tag is a prefix of any known tag
                    is_prefix = False
                    for tag in possible_tags:
                         # Check if tag starts with potential_tag
                         if tag.startswith(potential_tag):
                             is_prefix = True
                             break
                    
                    if is_prefix:
                        # It is a valid prefix, HOLD IT (do not stream)
                        if safe_part:
                            self.processing_queue.put(("stream_chat", safe_part))
                        stream_buffer = potential_tag 
                    else:
                        # It matches no tag prefix, so it's not a control tag. Release all.
                        self.processing_queue.put(("stream_chat", stream_buffer))
                        stream_buffer = ""
                else:
                    # No brackets, allow all
                    self.processing_queue.put(("stream_chat", stream_buffer))
                    stream_buffer = ""

            for chunk in llm_gen:
                if first_token_time is None:
                    first_token_time = time.time()
                    print(f"[DEBUG] Time to First Token (TTFT): {first_token_time - start_time:.4f}s")
                
                full_response += chunk
                
                if not found_separator:
                    # Check if separator appeared in this chunk or accumulated buffer
                    if "|||" in full_response:
                        found_separator = True
                        parts = full_response.split("|||", 1)
                        analysis_text = parts[0].strip()
                        
                        # Save analysis early for debugging
                        print(f"[DEBUG] 心理分析已生成 (长度{len(analysis_text)})")
                        
                        # Any text after separator is the start of spoken text
                        new_spoken = parts[1]
                        spoken_text += new_spoken
                        
                        # Stream the start of spoken text to UI (with filtering)
                        if new_spoken:
                            stream_and_filter(new_spoken)
                else:
                    # Already found separator, everything is spoken text
                    spoken_text += chunk
                    stream_and_filter(chunk)
            
            # Flush any remaining buffer (e.g. unclosed brackets or non-tags)
            if stream_buffer:
                 self.processing_queue.put(("stream_chat", stream_buffer))
            
            # Finish streaming (add newline)
            self.processing_queue.put(("stream_chat", "\n"))
            
            # DEBUG: Print full response
            print(f"[DEBUG] LLM完整响应长度: {len(full_response)}")
            
            # Fallback if no separator found (edge case)
            if not found_separator:
                print(f"[DEBUG] 警告: 未找到分隔符|||，假设全部为口语或全部为分析")
                # Heuristic: if response is short and looks like analysis, treat as analysis?
                # For safety, let's treat as spoken text to avoid silence, unless it really looks like JSON/internal
                spoken_text = full_response
                analysis_text = ""
                # Since we didn't stream anything (waiting for separator), show it now
                self.processing_queue.put(("stream_chat", spoken_text + "\n"))
            
            # Save analysis to log
            if analysis_text:
                self._save_analysis_log(user_id, text, analysis_text, spoken_text)
            
            # 5. Check for session end tags
            end_type = self.report_service.check_session_end(spoken_text)
            spoken_text = self.report_service.strip_end_tags(spoken_text)
            
            # 6. Extract relaxation control tags from spoken text
            control_tags = {
                "[REC_BREATHING]": "呼吸",
                "[REC_MUSCLE]": "肌肉",
                "[REC_MEDITATION]": "冥想"
            }
            
            # Check round threshold
            current_rounds = self.report_service.get_round_count()
            allow_relaxation = (current_rounds >= MIN_ROUNDS_FOR_RELAXATION)
            if not allow_relaxation:
                 print(f"[DEBUG] Only {current_rounds} rounds, relaxation recommendation filtered (min: {MIN_ROUNDS_FOR_RELAXATION})")
            
            detected_tag = None
            for tag, keyword in control_tags.items():
                if tag in spoken_text:
                    if allow_relaxation:
                         detected_tag = keyword
                    spoken_text = spoken_text.replace(tag, "").strip()
            
            # If not allowed, also strip verbal recommendations to be safe
            if not allow_relaxation:
                import re
                patterns = [
                     r"试试.*?(呼吸|放松|冥想).*?练习",
                     r"做个.*?(深呼吸|肌肉放松)",
                     r"进行.*?(放松训练)",
                     r"点击.*?(按钮|屏幕)",
                ]
                for pattern in patterns:
                    try:
                        spoken_text = re.sub(pattern, "", spoken_text)
                    except:
                        pass
            
            # 7. Handle edge case: if spoken_text is empty after tag extraction,
            #    the LLM probably put spoken text BEFORE ||| and only tags AFTER
            if not spoken_text.strip() and analysis_text:
                print(f"[DEBUG] 警告: 口语文本为空，使用心理分析部分作为口语文本")
                spoken_text = analysis_text
                # Check for tags in the fallback text too
                for tag, keyword in control_tags.items():
                    if tag in spoken_text:
                        if not detected_tag:
                            detected_tag = keyword
                        spoken_text = spoken_text.replace(tag, "").strip()
            
            # DEBUG: Print final spoken text
            print(f"[DEBUG] 最终口语文本长度: {len(spoken_text)}")
            print(f"[DEBUG] 最终口语文本: {spoken_text[:200] if spoken_text else '空口语文本'}")
            
            # 8. Display cleaned response in UI (Already done via Smart Streaming)
            # self.processing_queue.put(("append_chat", ("ai_start", "")))
            # Code removed as it duplicates streaming logic
            
            # 8. TTS Streaming (only the spoken part, without analysis or tags)
            self.processing_queue.put(("status", "🔊 正在朗读..."))
            
            tts_audio_data = self.tts_service.generate_and_play(spoken_text)
            
            if tts_audio_data is not None and len(tts_audio_data) > 0:
                self.data_manager.save_assistant_message(tts_audio_data, full_response)
            else:
                 print("Warning: No audio generated for saving.")
            
            # 9. Handle session end if detected
            if end_type != EndType.NONE:
                self._handle_session_end(end_type, detected_tag)
            elif detected_tag:
                # Just highlight relaxation button if no session end
                self.processing_queue.put(("check_relaxation_recommendation", detected_tag))
            
            self.processing_queue.put(("status", "✅ 完成"))
            self.processing_queue.put(("set_buttons_state", "normal"))

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.processing_queue.put(("error", f"处理失败: {str(e)}"))

    def _animate_transition_to_main_ui(self):
        """Animate the transition from loading screen to main UI."""
        self.transition_step = 0
        self.transition_total_steps = 15
        
        def animate_step():
            self.transition_step += 1
            progress = self.transition_step / self.transition_total_steps
            
            # Phase 1: Shrink loading frame
            if progress <= 0.6 and hasattr(self, 'loading_frame') and self.loading_frame:
                scale = 1 - (progress / 0.6) * 0.3
                new_width = int(420 * scale)
                new_height = int(300 * scale)
                self.loading_frame.place_configure(width=new_width, height=new_height)
                
            # Phase 2: Remove loading, show main UI
            if progress > 0.6 and hasattr(self, 'loading_frame') and self.loading_frame:
                self.loading_frame.destroy()
                self.loading_frame = None
                
                # Use stored dimensions or defaults
                cw = getattr(self, 'ctrl_w', 300)
                ch = getattr(self, 'ctrl_h', 500)
                chat_w = getattr(self, 'chat_w', 450)
                chat_h = getattr(self, 'chat_h', 500)
                
                self.control_frame.place(relx=-0.3, rely=0.5, anchor="w", width=cw, height=ch)
                self.chat_frame.place(relx=1.3, rely=0.5, anchor="e", width=chat_w, height=chat_h)
            
            # Phase 3: Slide in panels
            if progress > 0.6:
                slide_progress = (progress - 0.6) / 0.4
                slide_eased = 1 - (1 - slide_progress) ** 2
                left_x = -0.3 + (0.35 * slide_eased)
                right_x = 1.3 - (0.35 * slide_eased)
                self.control_frame.place_configure(relx=left_x)
                self.chat_frame.place_configure(relx=right_x)
            
            if self.transition_step < self.transition_total_steps:
                self.root.after(20, animate_step)
            else:
                cw = getattr(self, 'ctrl_w', 300)
                ch = getattr(self, 'ctrl_h', 600)
                chat_w = getattr(self, 'chat_w', 600)
                chat_h = getattr(self, 'chat_h', 600)
                
                self.control_frame.place(relx=0.05, rely=0.5, anchor="w", width=cw, height=ch)
                self.chat_frame.place(relx=0.95, rely=0.5, anchor="e", width=chat_w, height=chat_h)
                self.setButtonsState("normal")
                self.status_var.set("✅ 模型加载完成，请开始")
        
        animate_step()

    def _save_analysis_log(self, user_id: str, user_text: str, analysis: str, spoken: str):
        """Save psychological analysis to log file for research purposes.
        
        Args:
            user_id: The participant ID
            user_text: What the user said
            analysis: The AI's internal psychological analysis (PART 1)
            spoken: The spoken response (PART 2)
        """
        try:
            from datetime import datetime
            
            # Get the session path from data_manager
            if self.data_manager and self.data_manager.current_folder_name:
                session_path = self.data_manager._get_session_path()
                log_path = session_path / "analysis_log.txt"
                
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                
                with open(log_path, 'a', encoding='utf-8') as f:
                    f.write(f"\n{'='*60}\n")
                    f.write(f"[{timestamp}] 用户: {user_text}\n")
                    f.write(f"\n--- 心理分析 ---\n{analysis}\n")
                    f.write(f"\n--- 口语回复 ---\n{spoken}\n")
                    
                print(f"[INFO] 分析日志已保存: {log_path}")
        except Exception as e:
            print(f"[WARNING] 保存分析日志失败: {e}")

    def _animate_button_press(self, button, original_color, callback):
        """Animate button press with visual feedback."""
        def press_effect():
            button.config(relief=tk.SUNKEN, bg="#d0d0d0")
            self.root.after(100, release_effect)
        
        def release_effect():
            button.config(relief=tk.RAISED, bg=original_color)
            self.root.after(50, callback)
        
        press_effect()

    def _highlight_recommended_buttons(self, recommendation_keyword):
        """Highlight relaxation training buttons based on AI recommendation.
        
        Args:
            recommendation_keyword: Exact keyword like "呼吸", "肌肉", or "冥想"
        
        Buttons will blink continuously until user starts recording or clicks a button.
        """
        # Stop any existing highlight animation
        self._stop_highlight_animation()
        
        # Mapping of keywords to buttons and their original colors
        button_map = {
            "呼吸": (self.btn_breathing, "#64B5F6"),
            "肌肉": (self.btn_muscle, "#4DD0E1"),
            "冥想": (self.btn_meditation, "#9575CD"),
        }
        
        # Get the button for the exact keyword match
        self.buttons_to_highlight = []
        if recommendation_keyword in button_map:
            button, color = button_map[recommendation_keyword]
            self.buttons_to_highlight.append((button, color))
        
        if not self.buttons_to_highlight:
            return
        
        # Start continuous highlight animation
        self.highlight_active = True
        self.highlight_step = 0
        highlight_color = "#FFD700"  # Gold color for highlight
        
        def blink_step():
            if not self.highlight_active:
                # Animation stopped - restore original colors
                for button, original_color in self.buttons_to_highlight:
                    button.config(bg=original_color, relief=tk.RAISED)
                return
            
            self.highlight_step += 1
            
            for button, original_color in self.buttons_to_highlight:
                if self.highlight_step % 2 == 1:
                    # Highlight phase
                    button.config(bg=highlight_color, relief=tk.GROOVE)
                else:
                    # Original phase  
                    button.config(bg=original_color, relief=tk.RAISED)
            
            # Continue blinking (slower pace for continuous animation)
            self.root.after(500, blink_step)
        
        blink_step()

    def _stop_highlight_animation(self):
        """Stop the button highlight animation."""
        self.highlight_active = False
        if hasattr(self, 'buttons_to_highlight') and self.buttons_to_highlight:
            for button, original_color in self.buttons_to_highlight:
                button.config(bg=original_color, relief=tk.RAISED)
        self.buttons_to_highlight = []
    
    def _play_opening_greeting(self):
        """Play opening greeting when session starts."""
        import random
        # Ensure fresh randomness
        random.seed(time.time())
        
        def greeting_task():
            try:
                self.processing_queue.put(("set_buttons_state", "disabled"))
                
                # Select random greeting
                selected_greeting = random.choice(GREETING_VARIANTS)
                print(f"[DEBUG] Random greeting selected: {selected_greeting}")
                
                # Display greeting in chat
                self.processing_queue.put(("append_chat", ("ai", selected_greeting)))
                
                # Update status
                self.processing_queue.put(("status", "🔊 正在播放问候..."))
                
                # Save to conversation history so LLM knows what was said
                self.llm_service.conversation_history.append({
                    "role": "assistant",
                    "content": selected_greeting
                })
                
                # Check cache logic
                # Include voice config to invalidate cache when voice changes
                hash_source = selected_greeting + VOICE_PROMPT_PATH + VOICE_PROMPT_TEXT
                greeting_hash = hashlib.md5(hash_source.encode('utf-8')).hexdigest()
                cache_dir = os.path.join(os.getcwd(), "cache")
                os.makedirs(cache_dir, exist_ok=True)
                cache_path = os.path.join(cache_dir, f"greeting_{greeting_hash}.wav")
                
                tts_audio_data = None
                
                if os.path.exists(cache_path):
                    print(f"[INFO] Using cached greeting: {cache_path}")
                    try:
                        data, fs = sf.read(cache_path, dtype='float32')
                        self.tts_service.play_audio(data)
                        tts_audio_data = data
                    except Exception as e:
                        print(f"[WARNING] Cached greeting playback failed: {e}")
                        # Fallback to generate
                        tts_audio_data = self.tts_service.generate_and_play(selected_greeting)
                else:
                    print(f"[INFO] Generating new greeting and caching...")
                    tts_audio_data = self.tts_service.generate_and_play(selected_greeting)
                    
                    # Cache the generated audio
                    if tts_audio_data is not None and len(tts_audio_data) > 0:
                        try:
                            # Save with explicit sample rate (FireRedTTS uses 24000)
                            sf.write(cache_path, tts_audio_data, 24000)
                        except Exception as e:
                            print(f"[WARNING] Failed to cache greeting: {e}")
                
                # Optionally save to data manager (saved as separate file in session dir)
                if tts_audio_data is not None and len(tts_audio_data) > 0:
                    self.data_manager.save_assistant_message(tts_audio_data, selected_greeting)
                
                self.processing_queue.put(("status", "✅ 就绪 - 点击录音开始对话"))
                self.processing_queue.put(("set_buttons_state", "normal"))
                
            except Exception as e:
                print(f"[WARNING] 问候播放失败: {e}")
                import traceback
                traceback.print_exc()
                self.processing_queue.put(("status", "✅ 就绪 - 点击录音开始对话"))
                self.processing_queue.put(("set_buttons_state", "normal"))
        
        threading.Thread(target=greeting_task, daemon=True).start()
    
    def _play_post_relaxation_greeting(self):
        """Play transition + suggestions after relaxation video, then show continue/end dialog."""
        def post_relaxation_task():
            try:
                self.processing_queue.put(("set_buttons_state", "disabled"))
                self.processing_queue.put(("status", "🎯 放松训练完成，正在准备建议..."))
                
                # Wait for background generation to complete (max 60 seconds)
                import time
                wait_start = time.time()
                while not getattr(self, '_background_generation_complete', True):
                    if time.time() - wait_start > 60:
                        print("[WARNING] Background generation timeout, using fallback")
                        break
                    time.sleep(0.5)
                
                # 1. Brief delay for transition
                time.sleep(1)
                
                # 2. Play transition TTS
                self.processing_queue.put(("status", "🔊 正在播放过渡语..."))
                # Display transition message in chat first
                self.processing_queue.put(("append_chat", ("ai", POST_RELAXATION_MESSAGE)))
                transition_audio = getattr(self, '_relaxation_transition_audio', None)
                if transition_audio is not None and len(transition_audio) > 0:
                    self.tts_service.play_audio(transition_audio)
                else:
                    # Fallback: generate on the fly
                    self.tts_service.generate_and_play(POST_RELAXATION_MESSAGE)
                
                # 3. Play suggestions TTS
                self.processing_queue.put(("status", "🔊 正在播放建议..."))
                suggestions_audio = getattr(self, '_relaxation_suggestions_audio', None)
                suggestions_text = getattr(self, '_relaxation_suggestions_text', None)
                
                # Display suggestions in chat BEFORE playing audio
                if suggestions_text:
                    self.processing_queue.put(("append_chat", ("ai", f"【后续建议】{suggestions_text}")))
                
                if suggestions_audio is not None and len(suggestions_audio) > 0:
                    self.tts_service.play_audio(suggestions_audio)
                elif suggestions_text:
                    self.tts_service.generate_and_play(suggestions_text)
                else:
                    # Fallback
                    fallback_suggestions = "睡不着时试试深呼吸。心里堵得慌就写两句。作息尽量规律。有空多走走晒太阳。"
                    self.processing_queue.put(("append_chat", ("ai", f"【后续建议】{fallback_suggestions}")))
                    self.tts_service.generate_and_play(fallback_suggestions)
                    suggestions_text = fallback_suggestions
                
                # 4. Show continue/end dialog
                self.processing_queue.put(("status", "✅ 建议播放完成"))
                self.processing_queue.put(("show_continue_dialog", None))
                
            except Exception as e:
                print(f"[ERROR] Post-relaxation flow failed: {e}")
                import traceback
                traceback.print_exc()
                self.processing_queue.put(("status", "✅ 就绪"))
                self.processing_queue.put(("set_buttons_state", "normal"))
        
        threading.Thread(target=post_relaxation_task, daemon=True).start()
    
    def _show_continue_or_end_dialog(self):
        """Show dialog asking user if they want to continue chatting."""
        print("[INFO] Showing continue/end dialog...")
        result = messagebox.askyesno(
            "继续对话", 
            "放松训练完成了！\n\n还想继续聊聊天吗？",
            parent=self.root
        )
        
        print(f"[INFO] User selected: {'Continue (Yes)' if result else 'End (No)'}")
        
        if result:  # Yes - continue chatting
            self._continue_chat_after_relaxation()
        else:  # No - end session
            self._end_session_after_relaxation()
    
    def _continue_chat_after_relaxation(self):
        """Resume normal conversation after relaxation training."""
        print("[INFO] Continuing chat after relaxation...")
        self.status_var.set("✅ 就绪 - 点击录音继续对话")
        self.setButtonsState("normal")
        
        # Add a system message to chat
        self.append_to_chat("system", "放松训练已完成，可以继续对话。")
        
        # Save interim PDF report in background (checkpoint)
        def save_interim_report():
            try:
                print("[INFO] Saving interim PDF report (continue chat checkpoint)...")
                
                user_id = self.user_id_entry.get().strip() or "default_user"
                current_user_info = getattr(self, "user_info", {})
                conversation_history = self.llm_service.conversation_history
                suggestions_text = getattr(self, '_relaxation_suggestions_text', None)
                
                # Use interim report from background generation
                researcher_report = getattr(self, '_interim_report', None)
                if not researcher_report:
                    researcher_report = self.report_service.generate_researcher_report(
                        conversation_history, user_id, EndType.GOAL_ACHIEVED, user_info=current_user_info
                    )
                
                # Add relaxation info
                if isinstance(researcher_report, dict):
                    researcher_report["relaxation_completed"] = True
                    researcher_report["relaxation_type"] = getattr(self, '_current_relaxation_type', "unknown")
                    researcher_report["suggestions_provided"] = suggestions_text or ""
                    researcher_report["session_continued"] = True  # Mark as interim
                
                # Save JSON report
                save_result = self.data_manager.save_session_report(
                    researcher_report, "Relaxation completed - continuing chat", EndType.GOAL_ACHIEVED.value
                )
                print(f"[INFO] Interim report saved: {save_result}")
                
                # Generate interim PDF
                pdf_generator = get_pdf_generator()
                pdf_data = researcher_report.copy() if isinstance(researcher_report, dict) else {}
                if "subject_id" not in pdf_data:
                    pdf_data["subject_id"] = user_id
                
                pdf_data.update({
                    "report_date": self.report_service.get_session_start_time().strftime("%Y年%m月%d日") if self.report_service.session_start_time else "未知",
                    "session_duration_minutes": self.report_service.get_session_duration_minutes(),
                    "conversation_rounds": self.report_service.round_count,
                    "end_type": "RELAXATION_CHECKPOINT",
                })
                
                session_folder = os.path.dirname(save_result.get("report_path", "")) if save_result else None
                if session_folder and os.path.isdir(session_folder):
                    pdf_path = pdf_generator.generate_report(pdf_data, session_folder)
                    if pdf_path:
                        print(f"[INFO] Interim PDF generated: {pdf_path}")
                        
            except Exception as e:
                print(f"[ERROR] Interim PDF generation failed: {e}")
                import traceback
                traceback.print_exc()
        
        threading.Thread(target=save_interim_report, daemon=True).start()
        
        # Play continue message
        def play_continue():
            try:
                self.tts_service.generate_and_play(CONTINUE_CHAT_MESSAGE)
            except Exception as e:
                print(f"[ERROR] Continue message playback failed: {e}")
        
        threading.Thread(target=play_continue, daemon=True).start()
    
    def _end_session_after_relaxation(self):
        """End session after relaxation, generate final report."""
        def end_task():
            try:
                self.processing_queue.put(("set_buttons_state", "disabled"))
                self.processing_queue.put(("status", "📊 正在生成最终总结..."))
                
                # Get conversation and suggestions
                suggestions_text = getattr(self, '_relaxation_suggestions_text', None)
                conversation_history = self.llm_service.conversation_history
                
                # Generate comprehensive session summary via LLM
                print("[INFO] Generating session summary via LLM...")
                try:
                    farewell = self.report_service.generate_session_summary(
                        conversation_history, suggestions_text or ""
                    )
                    print(f"[INFO] Session summary generated: {farewell}")
                except Exception as e:
                    print(f"[ERROR] Failed to generate session summary: {e}")
                    farewell = "今天聊了不少，辛苦你了。回去记得试试那几条建议，有事儿随时再来找我。"
                
                # Display farewell in chat first, then play TTS
                self.processing_queue.put(("append_chat", ("ai", f"【会话总结】{farewell}")))
                
                print(f"[INFO] Playing session summary TTS...")
                try:
                    self.tts_service.generate_and_play(farewell)
                    print(f"[INFO] Session summary TTS playback completed")
                except Exception as e:
                    print(f"[ERROR] Session summary TTS failed: {e}")
                    import traceback
                    traceback.print_exc()
                
                # Use interim report if available, otherwise generate fresh
                user_id = self.user_id_entry.get().strip() or "default_user"
                current_user_info = getattr(self, "user_info", {})
                conversation_history = self.llm_service.conversation_history
                
                # Use interim report from background generation
                researcher_report = getattr(self, '_interim_report', None)
                if not researcher_report:
                    researcher_report = self.report_service.generate_researcher_report(
                        conversation_history, user_id, EndType.GOAL_ACHIEVED, user_info=current_user_info
                    )
                
                # Add relaxation info to report
                if isinstance(researcher_report, dict):
                    researcher_report["relaxation_completed"] = True
                    researcher_report["relaxation_type"] = getattr(self, '_current_relaxation_type', "unknown")
                    researcher_report["suggestions_provided"] = getattr(self, '_relaxation_suggestions_text', "")
                
                # Save report
                save_result = self.data_manager.save_session_report(
                    researcher_report, "Relaxation completed", EndType.GOAL_ACHIEVED.value
                )
                print(f"[INFO] Final report saved: {save_result}")
                
                # Generate PDF
                try:
                    pdf_generator = get_pdf_generator()
                    pdf_data = researcher_report.copy() if isinstance(researcher_report, dict) else {}
                    if "subject_id" not in pdf_data:
                        pdf_data["subject_id"] = user_id
                    
                    pdf_data.update({
                        "report_date": self.report_service.get_session_start_time().strftime("%Y年%m月%d日") if self.report_service.session_start_time else "未知",
                        "session_duration_minutes": self.report_service.get_session_duration_minutes(),
                        "conversation_rounds": self.report_service.round_count,
                        "end_type": "RELAXATION_COMPLETED",
                    })
                    
                    session_folder = os.path.dirname(save_result.get("report_path", "")) if save_result else None
                    if session_folder and os.path.isdir(session_folder):
                        pdf_path = pdf_generator.generate_report(pdf_data, session_folder)
                        if pdf_path:
                            print(f"[INFO] Final PDF generated: {pdf_path}")
                except Exception as e:
                    print(f"[ERROR] PDF generation failed: {e}")
                
                self.session_ended = True
                self.processing_queue.put(("status", "✅ 会话结束，报告已生成 - 请下一位来访者录入信息"))
                
                # Delay then partial reset (keep chat visible)
                self.root.after(2000, lambda: self._reset_ui_for_new_session(clear_chat=False))
                
            except Exception as e:
                print(f"[ERROR] End session failed: {e}")
                import traceback
                traceback.print_exc()
                self.processing_queue.put(("status", "✅ 就绪"))
                self.processing_queue.put(("set_buttons_state", "normal"))
        
        threading.Thread(target=end_task, daemon=True).start()
    
    def _play_fill_info_prompt(self):
        """Play voice prompt to guide visitor to fill basic info form."""
        def prompt_task():
            try:
                self.processing_queue.put(("set_buttons_state", "disabled"))
                # Update status
                self.processing_queue.put(("status", "🔊 正在播放引导..."))
                
                # Play prompt via TTS (don't add to chat or history - it's just guidance)
                self.tts_service.generate_and_play(FILL_INFO_PROMPT)
                
                self.processing_queue.put(("status", "✅ 请填写左侧基本信息"))
                self.processing_queue.put(("set_buttons_state", "normal"))
                
            except Exception as e:
                print(f"[WARNING] 填写引导播放失败: {e}")
                self.processing_queue.put(("status", "✅ 请填写左侧基本信息"))
                self.processing_queue.put(("set_buttons_state", "normal"))
        
        threading.Thread(target=prompt_task, daemon=True).start()
    
    def _start_ollama_keepalive(self):
        """Start periodic Ollama warmup to prevent model unloading."""
        # Keep-alive interval: 3 minutes (180000 ms)
        KEEPALIVE_INTERVAL_MS = 180000
        
        def keepalive_ping():
            if self.models_loaded and self.llm_service:
                try:
                    # Send a minimal request to keep model loaded
                    self.llm_service.client.chat(
                        model=self.llm_service.model,
                        messages=[{"role": "user", "content": "ping"}],
                        stream=False
                    )
                    print("[INFO] Ollama keep-alive ping successful")
                except Exception as e:
                    print(f"[WARNING] Ollama keep-alive failed: {e}")
            
            # Schedule next ping
            self.root.after(KEEPALIVE_INTERVAL_MS, keepalive_ping)
        
        # Start the keep-alive loop
        self.root.after(KEEPALIVE_INTERVAL_MS, keepalive_ping)
        print("[INFO] Ollama keep-alive started (interval: 3 min)")
    
    def _handle_session_end(self, end_type: EndType, relaxation_tag: str = None, 
                          pre_generated_feedback: str = None, pre_generated_audio: Any = None):
        """
        Handle session end: generate reports and trigger UI updates.
        
        Args:
            end_type: The type of session ending
            relaxation_tag: Optional detected relaxation recommendation
            pre_generated_feedback: Optional pre-generated visitor feedback text
            pre_generated_audio: Optional pre-generated TTS audio for feedback
        """
        self.session_ended = True
        
        # Ensure relaxation is recommended if not done/tagged yet
        # If user hasn't done relaxation (_current_relaxation_type is None) 
        # AND AI didn't suggest it in the final turn (relaxation_tag is None)
        if not relaxation_tag and not getattr(self, '_current_relaxation_type', None):
            print("[INFO] 自动分析并推荐放松训练 (未检测到放松历史)...")
            try:
                # Use LLM to recommend based on history
                conversation_history = self.llm_service.conversation_history
                relaxation_tag = self.report_service.recommend_relaxation_strategy(conversation_history)
                print(f"[INFO] 智能推荐结果: {relaxation_tag}")
            except Exception as e:
                print(f"[WARNING] 智能推荐失败 ({e})，使用默认: 呼吸")
                relaxation_tag = "呼吸"
            
        self.processing_queue.put(("status", "📊 正在生成反馈..."))
        
        # Run report generation in thread to not block
        def generate_reports():
            try:
                user_id = self.user_id_entry.get().strip() or "default_user"
                
                # Collect user info available
                current_user_info = getattr(self, "user_info", {})
                
                # Get conversation history from LLM service
                conversation_history = self.llm_service.conversation_history
                
                # Get relaxation recommendation from detected tag (if any)
                relaxation_rec = None
                if relaxation_tag:
                    tag_map = {"呼吸": "BREATHING", "肌肉": "MUSCLE", "冥想": "MEDITATION"}
                    relaxation_rec = tag_map.get(relaxation_tag)
                
                # ===== STEP 1: Generate visitor feedback (Streamed) =====
                print("[INFO] 生成来访者反馈(流式)...")
                
                if pre_generated_feedback:
                    print("[INFO] 使用预生成反馈...")
                    visitor_feedback = pre_generated_feedback
                    # Simulate streaming for pre-generated content
                    self.processing_queue.put(("append_chat", ("ai_start", f"[反馈] {visitor_feedback}")))
                    self.processing_queue.put(("stream_chat", "\n"))
                else:
                    # Get end type name for system message
                    end_type_names = {
                        "GOAL_ACHIEVED": "目标达成",
                        "TIME_LIMIT": "时间结束",
                        "SAFETY": "安全转介",
                        "INVALID": "对话结束",
                        "QUIT": "用户退出",
                        "NONE": "会话结束"
                    }
                    end_name = end_type_names.get(end_type.value, "会话结束")
                    self.processing_queue.put(("append_chat", ("system", f"[{end_name}]")))
                    
                    # Start streaming feedback
                    self.processing_queue.put(("append_chat", ("ai_start", "[反馈] ")))
                    
                    full_feedback = ""
                    stream_gen = self.report_service.generate_visitor_feedback(
                        conversation_history, end_type, relaxation_rec, stream=True
                    )
                    
                    for chunk in stream_gen:
                        full_feedback += chunk
                        self.processing_queue.put(("stream_chat", chunk))
                    
                    self.processing_queue.put(("stream_chat", "\n"))
                    visitor_feedback = self.report_service._clean_for_tts(full_feedback)
                
                # ===== STEP 2: Immediately trigger UI and TTS playback =====
                # Pass pre_generated_audio if available
                # Note: valid_feedback is cleaned
                self.processing_queue.put(("session_end", (end_type, visitor_feedback, relaxation_rec, pre_generated_audio)))
                
                # Show crisis resources for safety endings
                if end_type == EndType.SAFETY:
                    self.processing_queue.put(("show_crisis_resources", None))
                
                # ===== STEP 3: Generate researcher report in background (slow) =====
                print("[INFO] 后台生成研究人员报告...")
                # Pass user_info to ensure it's included in the report JSON and PDF
                researcher_report = self.report_service.generate_researcher_report(
                    conversation_history, user_id, end_type, user_info=current_user_info
                )
                
                # Update relaxation recommendation if found in report
                report_rec = self.report_service.get_relaxation_recommendation(researcher_report)
                if report_rec and not relaxation_rec:
                    relaxation_rec = report_rec
                
                # ===== STEP 4: Save reports =====
                print("[INFO] 保存报告...")
                save_result = self.data_manager.save_session_report(
                    researcher_report, visitor_feedback, end_type.value
                )
                print(f"[INFO] 报告已保存: {save_result}")
                
                # ===== STEP 5: Generate PDF Report =====
                print("[INFO] 生成PDF报告...")
                try:
                    pdf_generator = get_pdf_generator()
                    
                    # Prepare PDF data from researcher report (which now contains user_info)
                    pdf_data = researcher_report.copy() if isinstance(researcher_report, dict) else {}
                    
                    # Ensure basic stats fields are present if not already
                    if "subject_id" not in pdf_data:
                        pdf_data["subject_id"] = user_id
                    
                    # Add session stats
                    pdf_data.update({
                        "report_date": self.report_service.get_session_start_time().strftime("%Y年%m月%d日") if self.report_service.session_start_time else "未知",
                        "session_duration_minutes": self.report_service.get_session_duration_minutes(),
                        "conversation_rounds": self.report_service.round_count,
                        "end_type": end_type.value,
                    })
                    
                    # Get session folder path
                    session_folder = os.path.dirname(save_result.get("report_path", "")) if save_result else None
                    if session_folder and os.path.isdir(session_folder):
                        pdf_path = pdf_generator.generate_report(pdf_data, session_folder)
                        if pdf_path:
                            print(f"[INFO] PDF报告已生成: {pdf_path}")
                    else:
                        print("[WARNING] 无法确定会话文件夹，跳过PDF生成")
                except Exception as pdf_error:
                    print(f"[WARNING] PDF生成失败: {pdf_error}")
                    import traceback
                    traceback.print_exc()
                
                # Report generation finished
                self.processing_queue.put(("status", "✅ 会话已结束 - 可点击'修改信息'开始新会话"))
                
                # Unlock modify button to allow new session
                def unlock_modify():
                    self.btn_modify_user.config(state="normal", bg="#FF9800")
                
                self.root.after(0, unlock_modify)
                
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.processing_queue.put(("error", f"报告生成失败: {str(e)}"))
        
        threading.Thread(target=generate_reports, daemon=True).start()
    
    def _show_session_end_dialog(self, end_type: EndType, visitor_feedback: str, relaxation_rec: str = None, audio_data: Any = None):
        """
        Show session end dialog with visitor feedback.
        
        Args:
            end_type: The type of session ending
            visitor_feedback: The oral-style feedback text
            relaxation_rec: Optional relaxation recommendation
            audio_data: Optional pre-generated audio data
        """
        # Update status
        self.status_var.set("📋 正在准备会话总结...")
        
        # Add feedback to chat as system message
        end_type_names = {
            EndType.GOAL_ACHIEVED: "目标达成",
            EndType.TIME_LIMIT: "时间结束",
            EndType.SAFETY: "安全转介",
            EndType.INVALID: "对话结束"
        }
        end_name = end_type_names.get(end_type, "会话结束")
        
        # Show loading popup while generating audio
        loading_popup = tk.Toplevel(self.root)
        loading_popup.title("正在生成总结")
        loading_popup.geometry("300x120")
        loading_popup.transient(self.root)
        loading_popup.grab_set()
        loading_popup.overrideredirect(True)  # No title bar
        
        # Center the popup
        loading_popup.update_idletasks()
        x = (self.root.winfo_screenwidth() - 300) // 2
        y = (self.root.winfo_screenheight() - 120) // 2
        loading_popup.geometry(f"300x120+{x}+{y}")
        
        loading_frame = tk.Frame(loading_popup, bg="#f0f0f0", relief=tk.RAISED, bd=2)
        loading_frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        
        tk.Label(loading_frame, text="⏳", font=("Segoe UI Emoji", 32), bg="#f0f0f0").pack(pady=10)
        loading_label = tk.Label(loading_frame, text="正在生成语音，请稍候...", 
                                  font=("Microsoft YaHei", 11), bg="#f0f0f0")
        loading_label.pack()
        
        # Generate and play in background thread
        def generate_and_play_summary():
            try:
                # Generate complete audio first (no streaming)
                def update_progress(msg):
                    try:
                        loading_label.config(text=msg)
                    except:
                        pass
                
                if audio_data is not None:
                    # Use pre-generated audio
                    print("[INFO] Using pre-generated feedback audio.")
                    self.tts_service.play_audio(audio_data)
                else:
                    # Use full generation instead of streaming to prevent stuttering
                    print("[INFO] Pre-generating full summary audio to ensure smooth playback...")
                    full_audio = self.tts_service.generate(visitor_feedback)
                    self.tts_service.play_audio(full_audio)
                
                # After TTS, highlight relaxation button if recommended
                if relaxation_rec:
                    rec_map = {
                        "BREATHING": "呼吸",
                        "MUSCLE": "肌肉", 
                        "MEDITATION": "冥想"
                    }
                    keyword = rec_map.get(relaxation_rec.upper())
                    if keyword:
                        self.processing_queue.put(("check_relaxation_recommendation", keyword))
                
                self.processing_queue.put(("status", "✅ 会话已结束 - 请开始新对话或进行放松训练"))
                
            except Exception as e:
                print(f"[WARNING] TTS反馈播放失败: {e}")
                self.processing_queue.put(("status", "✅ 会话已结束"))
            finally:
                # Close loading popup on main thread
                self.processing_queue.put(("close_loading_popup", loading_popup))
        
        # Add queue handler for closing popup
        threading.Thread(target=generate_and_play_summary, daemon=True).start()
        
        # Display feedback in chat - REMOVED (Handled by streaming in _handle_session_end)
        # self.append_to_chat("system", f"[{end_name}]")
        # self.append_to_chat("ai", f"[反馈] {visitor_feedback}")
        
        # Show simple dialog for important endings (after audio finishes)
        if end_type in [EndType.GOAL_ACHIEVED, EndType.SAFETY]:
            title = "会话总结" if end_type == EndType.GOAL_ACHIEVED else "重要提示"
            # Delay popup until after audio generation
            self.root.after(3000, lambda: self._show_feedback_popup(title, visitor_feedback, end_type))
    
    def _show_feedback_popup(self, title: str, feedback: str, end_type: EndType):
        """Show a non-blocking popup with feedback summary."""
        popup = tk.Toplevel(self.root)
        popup.title(title)
        popup.geometry("450x300")
        popup.transient(self.root)
        popup.grab_set()
        
        # Icon based on end type
        icon_text = "🎉" if end_type == EndType.GOAL_ACHIEVED else "⚠️"
        
        frame = tk.Frame(popup, padx=20, pady=15)
        frame.pack(fill=tk.BOTH, expand=True)
        
        # Icon
        tk.Label(frame, text=icon_text, font=("Segoe UI Emoji", 36)).pack()
        
        # Title
        tk.Label(frame, text=title, font=("Microsoft YaHei", 14, "bold")).pack(pady=(5, 10))
        
        # Feedback text
        text_widget = tk.Text(frame, wrap=tk.WORD, height=6, font=("Microsoft YaHei", 11))
        text_widget.pack(fill=tk.BOTH, expand=True, pady=5)
        text_widget.insert("1.0", feedback)
        text_widget.config(state=tk.DISABLED)
        
        # Close button
        btn = tk.Button(frame, text="确认", command=popup.destroy, 
                       bg="#4CAF50", fg="white", font=("Arial", 10), width=10)
        btn.pack(pady=10)
        
        # Center the popup
        popup.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - popup.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - popup.winfo_height()) // 2
        popup.geometry(f"+{x}+{y}")
    
    def _show_crisis_resources_dialog(self):
        """Show crisis hotlines dialog for safety endings."""
        popup = tk.Toplevel(self.root)
        popup.title("紧急求助资源")
        popup.geometry("400x350")
        popup.transient(self.root)
        popup.grab_set()
        
        frame = tk.Frame(popup, padx=20, pady=15, bg="#fff3e0")
        frame.pack(fill=tk.BOTH, expand=True)
        
        # Warning icon
        tk.Label(frame, text="🆘", font=("Segoe UI Emoji", 42), bg="#fff3e0").pack()
        
        # Title
        tk.Label(frame, text="紧急求助热线", font=("Microsoft YaHei", 16, "bold"), 
                bg="#fff3e0", fg="#e65100").pack(pady=(5, 15))
        
        # Hotlines
        hotlines_frame = tk.Frame(frame, bg="#fff3e0")
        hotlines_frame.pack(fill=tk.X)
        
        for name, number in CRISIS_HOTLINES.items():
            row = tk.Frame(hotlines_frame, bg="#fff3e0")
            row.pack(fill=tk.X, pady=3)
            tk.Label(row, text=f"• {name}:", font=("Microsoft YaHei", 11), 
                    bg="#fff3e0", anchor="w").pack(side=tk.LEFT)
            tk.Label(row, text=number, font=("Microsoft YaHei", 11, "bold"), 
                    bg="#fff3e0", fg="#d84315", anchor="w").pack(side=tk.LEFT, padx=5)
        
        # Note
        tk.Label(frame, text="以上热线24小时在线，请随时拨打", 
                font=("Microsoft YaHei", 9), bg="#fff3e0", fg="#666").pack(pady=(15, 5))
        
        # Close button
        btn = tk.Button(frame, text="我知道了", command=popup.destroy,
                       bg="#ff9800", fg="white", font=("Arial", 10), width=12)
        btn.pack(pady=10)
        
        # Center the popup
        popup.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - popup.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - popup.winfo_height()) // 2
        popup.geometry(f"+{x}+{y}")

    def _replace_last_ai_response(self, clean_text: str):
        """Replace the last AI response in chat with cleaned text.
        
        This removes the streamed text (which may contain ||| and tags)
        and replaces it with the clean spoken text.
        """
        self.chat_text.config(state=tk.NORMAL)
        
        # Get all text content
        content = self.chat_text.get("1.0", tk.END)
        
        # Find the last occurrence of "心医生: "
        prefix = "心医生: "
        last_pos = content.rfind(prefix)
        
        if last_pos != -1:
            # Calculate line and column position
            lines_before = content[:last_pos].count('\n') + 1
            col_after_prefix = len(prefix)
            
            # Delete from after "心医生: " to right before the final newline
            start_index = f"{lines_before}.{col_after_prefix}"
            
            # Find where the response ends (look for next newline after the response)
            # We delete to the end minus 1 char (the trailing newline from tk.END)
            self.chat_text.delete(start_index, "end-1c")
            
            # Start typewriter effect for the new text
            self._typewriter_effect(start_index, clean_text)
            
        self.chat_text.see(tk.END)
        self.chat_text.config(state=tk.DISABLED)

    def _typewriter_effect(self, index, text, current_pos=0):
        """Recursively insert text character by character."""
        if current_pos < len(text):
            self.chat_text.config(state=tk.NORMAL)
            
            # Insert next char
            char = text[current_pos]
            self.chat_text.insert(f"{index}+{current_pos}c", char, "ai")
            
            # Ensure final newline is present if done (though append_chat adds it, here we are replacing)
            if current_pos == len(text) - 1:
                self.chat_text.insert(f"{index}+{current_pos+1}c", "\n", "ai")
            
            self.chat_text.see(tk.END)
            self.chat_text.config(state=tk.DISABLED)
            
            # Schedule next char (randomized slightly for natural feel)
            import random
            delay = random.randint(30, 50)
            self.root.after(delay, lambda: self._typewriter_effect(index, text, current_pos + 1))
        else:
            # Ensure disabled state at end
             self.chat_text.config(state=tk.DISABLED)

    def append_to_chat(self, role, text):
        self.chat_text.config(state=tk.NORMAL)
        
        if role == "user":
            self.chat_text.insert(tk.END, f"\n我: {text}\n", "user")
        elif role == "ai":
            # Only used for non-streaming calls now (if any)
            self.chat_text.insert(tk.END, f"心医生: {text}\n", "ai")
        elif role == "ai_start":
            # Start the line for streaming
            self.chat_text.insert(tk.END, f"心医生: ", "ai")
        elif role == "system":
            self.chat_text.insert(tk.END, f"[系统]: {text}\n", "system")
            
        self.chat_text.see(tk.END)
        self.chat_text.config(state=tk.DISABLED)

    def _play_video_with_animation(self, button, original_color, filename):
        """Play video with button press animation effect."""
        # Stop any button highlight animation
        self._stop_highlight_animation()
        
        def press_effect():
            # Darken button
            button.config(relief=tk.SUNKEN, bg="#a0a0a0")
            self.root.after(100, release_effect)
        
        def release_effect():
            # Restore color with slight glow effect
            button.config(relief=tk.RAISED, bg=original_color)
            self.root.after(80, play_video)
        
        def play_video():
            self.play_relaxation_video(filename)
        
        press_effect()

    def play_relaxation_video(self, filename):
        app_dir = os.path.dirname(os.path.abspath(__file__))
        video_path = os.path.join(app_dir, filename)
        
        if os.path.exists(video_path):
            self.status_var.set(f"🎬 正在播放 {filename} (全屏模式)")
            
            # Initialize background generation state
            self._relaxation_transition_audio = None
            self._relaxation_suggestions_text = None
            self._relaxation_suggestions_audio = None
            self._background_generation_complete = False
            self._current_relaxation_type = filename.replace(".mp4", "")
            
            # Start background generation thread immediately
            def background_generation():
                try:
                    print("[INFO] Background generation starting...")
                    
                    # 1. Generate transition TTS
                    print("[INFO] Generating transition TTS...")
                    self._relaxation_transition_audio = self.tts_service.generate(POST_RELAXATION_MESSAGE)
                    
                    # 2. Generate personalized suggestions via LLM
                    print("[INFO] Generating suggestions via LLM...")
                    conversation_history = self.llm_service.conversation_history
                    suggestions = self.report_service.generate_suggestions(conversation_history)
                    self._relaxation_suggestions_text = suggestions
                    print(f"[INFO] Suggestions generated: {suggestions[:50]}...")
                    
                    # 3. Synthesize suggestions TTS
                    print("[INFO] Generating suggestions TTS...")
                    self._relaxation_suggestions_audio = self.tts_service.generate(suggestions)
                    
                    # 4. Generate interim report (fallback in case user force-exits)
                    print("[INFO] Generating interim report...")
                    user_id = self.user_id_entry.get().strip() or "default_user"
                    current_user_info = getattr(self, "user_info", {})
                    self._interim_report = self.report_service.generate_researcher_report(
                        conversation_history, user_id, EndType.GOAL_ACHIEVED, user_info=current_user_info
                    )
                    
                    self._background_generation_complete = True
                    print("[INFO] Background generation complete!")
                    
                except Exception as e:
                    print(f"[ERROR] Background generation failed: {e}")
                    import traceback
                    traceback.print_exc()
                    self._background_generation_complete = True  # Mark as done to avoid blocking
            
            threading.Thread(target=background_generation, daemon=True).start()
            
            # Video runner thread
            def video_runner():
                try:
                    player = get_video_player()
                    player.play_video(video_path)
                except Exception as e:
                    print(f"Video runner exception: {e}")
                finally:
                    print("[INFO] Video finished.")
                    # Trigger post-relaxation flow
                    self.processing_queue.put(("post_relaxation_greeting", None))
            
            threading.Thread(target=video_runner, daemon=True).start()

        else:
            messagebox.showwarning("警告", f"文件不存在: {filename}")

    def stop_relaxation_video(self):
        # With the new centralized player, we could signal it to stop if we kept a reference.
        # But per requirements, it "cannot be exited" except by backdoor.
        self.status_var.set("⚠️ 只能等待播放结束或使用开发者后门退出")

    def clearHistory(self):
        self.chat_text.config(state=tk.NORMAL)
        self.chat_text.delete(1.0, tk.END)
        self.chat_text.config(state=tk.DISABLED)
        if self.llm_service:
            self.llm_service.reset_conversation()
        if self.data_manager:
            self.data_manager.start_new_session()
        # Reset report service session
        if self.report_service:
            self.report_service.start_session()
        self.session_ended = False
        
        # Play greeting for new session
        self._play_opening_greeting()

    def exitApp(self):
        """Handle manual application exit."""
        if self.session_ended:
            self.root.quit()
            self.root.destroy()
            sys.exit(0)
            
        # Show generating report message
        popup = tk.Toplevel(self.root)
        popup.title("正在退出")
        popup.geometry("300x100")
        # Center popup
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 150
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 50
        popup.geometry(f"+{x}+{y}")
        
        label = tk.Label(popup, text="正在生成报告并退出...", font=("Microsoft YaHei", 12))
        label.pack(expand=True, pady=20)
        popup.update()
        
        # Trigger session end logic with QUIT type
        # We need to run this in a thread but wait for it or just run it and let the thread close the app
        # Since _handle_session_end is largely threaded, we can modify it to callback or we can just run the sync part here
        
        # Ideally we should use _handle_session_end but it puts results in queue to update UI
        # For exit, we just want to generate and save.
        
        def exit_task():
            try:
                if self.stt_service:
                    try:
                        self.stt_service.stop_recording()
                    except:
                        pass
                
                # Manually trigger the report generation part
                user_id = self.user_id_entry.get().strip() or "default_user"
                current_user_info = getattr(self, "user_info", {})
                conversation_history = self.llm_service.conversation_history
                
                if conversation_history: # Only generate if there is chat
                    print("[INFO] Exiting: Generating final report...")
                    researcher_report = self.report_service.generate_researcher_report(
                        conversation_history, user_id, EndType.QUIT, user_info=current_user_info
                    )
                    
                    self.data_manager.save_session_report(
                        researcher_report, "User Exited", EndType.QUIT.value
                    )
                    
                    # PDF
                    try:
                         # Prepare PDF data
                        pdf_data = researcher_report.copy() if isinstance(researcher_report, dict) else {}
                        if "subject_id" not in pdf_data:
                            pdf_data["subject_id"] = user_id
                        
                        pdf_data.update({
                            "report_date": self.report_service.get_session_start_time().strftime("%Y年%m月%d日") if self.report_service.session_start_time else "未知",
                            "session_duration_minutes": self.report_service.get_session_duration_minutes(),
                            "conversation_rounds": self.report_service.round_count,
                            "end_type": EndType.QUIT.value,
                        })
                        
                        save_result = self.data_manager.save_session_report(researcher_report, "User Exited", EndType.QUIT.value)
                        session_folder = os.path.dirname(save_result.get("report_path", "")) if save_result else None
                        
                        if session_folder and os.path.isdir(session_folder):
                            pdf_generator = get_pdf_generator()
                            pdf_generator.generate_report(pdf_data, session_folder)
                            print("[INFO] Exit PDF generated.")
                    except Exception as e:
                        print(f"[ERROR] Exit PDF generation failed: {e}")
                        
            except Exception as e:
                print(f"[ERROR] Exit cleanup failed: {e}")
            finally:
                self.root.quit()
                self.root.destroy()
                sys.exit(0)
        
        threading.Thread(target=exit_task, daemon=True).start()

    def _confirm_user_info(self):
        """Validate and confirm user info, then start session."""
        # Validate required fields
        user_id = self.user_id_entry.get().strip()
        gender = self.gender_var.get()
        age = self.age_entry.get().strip()
        education = self.education_var.get()
        marital = self.marital_var.get()
        drug_type = self.drug_type_var.get()
        
        # Check all fields are filled
        missing = []
        if not user_id:
            missing.append("编号")
        if not gender:
            missing.append("性别")
        if not age:
            missing.append("年龄")
        if not education:
            missing.append("文化程度")
        if not marital:
            missing.append("婚姻状况")
        if not drug_type:
            missing.append("毒品类型")
        
        if missing:
            messagebox.showwarning("信息不完整", f"请填写以下必填项：{', '.join(missing)}")
            return
        
        # Validate age is a number
        try:
            age_int = int(age)
            if age_int < 1 or age_int > 120:
                raise ValueError
        except ValueError:
            messagebox.showwarning("年龄格式错误", "请输入有效的年龄（1-120）")
            return
        
        # Store user info
        self.user_info = {
            "user_id": user_id,
            "gender": gender,
            "age": age_int,
            "education": education,
            "marital_status": marital,
            "drug_type": drug_type
        }
        self.info_confirmed = True
        self.confirmed_user_id = user_id
        self.current_user_id = user_id
        
        # Update status
        self.current_user_var.set(f"✅ 编号 {user_id} 已确认")
        
        # Disable form fields after confirmation
        self.user_id_entry.config(state="disabled")
        self.gender_combo.config(state="disabled")
        self.age_entry.config(state="disabled")
        self.education_combo.config(state="disabled")
        self.marital_combo.config(state="disabled")
        self.drug_type_combo.config(state="disabled")
        self.btn_confirm_user.config(text="已确认", bg="#888", state="disabled")
        self.btn_modify_user.config(state="normal", bg="#FF9800")
        
        # Reset LLM conversation history
        if self.llm_service:
            self.llm_service.reset_conversation()
        
        # Clear chat display
        self.chat_text.config(state=tk.NORMAL)
        self.chat_text.delete(1.0, tk.END)
        self.chat_text.config(state=tk.DISABLED)
        
        # Start new data session
        if self.data_manager:
            self.data_manager.set_user_id(user_id)
            self.data_manager.start_new_session()
        
        # Reset report service session
        if self.report_service:
            self.report_service.start_session()
        self.session_ended = False
        
        # Play greeting for new session
        self._play_opening_greeting()
    
    def _confirm_user_id(self):
        """Legacy method - redirects to new method."""
        self._confirm_user_info()
        
    def _on_modify_user_click(self):
        """Handle click on 'Modify Info' button."""
        # 1. Check if there is an active session
        has_active_session = False
        if self.report_service and self.report_service.round_count > 0 and not self.session_ended:
            has_active_session = True
            
        if has_active_session:
            # Ask for confirmation if session is active
            if not messagebox.askyesno("结束当前会话", 
                                      "正在进行会话，切换用户将结束当前会话并生成报告。\n确认要继续吗？"):
                return

            # Trigger session end logic (QUIT type)
            # Use threading to prevent UI block
            def end_and_reset():
                try:
                    # Manually generate report for current user
                    self.processing_queue.put(("status", "📊 正在保存当前会话报告..."))
                    
                    user_id = self.user_id_entry.get().strip() or "default_user"
                    current_user_info = getattr(self, "user_info", {})
                    conversation_history = self.llm_service.conversation_history
                    
                    # Generate report
                    researcher_report = self.report_service.generate_researcher_report(
                        conversation_history, user_id, EndType.QUIT, user_info=current_user_info
                    )
                    self.data_manager.save_session_report(
                        researcher_report, "User Switched", EndType.QUIT.value
                    )
                    
                    # Generate PDF
                    try:
                        pdf_generator = get_pdf_generator()
                        pdf_data = researcher_report.copy() if isinstance(researcher_report, dict) else {}
                        if "subject_id" not in pdf_data:
                            pdf_data["subject_id"] = user_id
                            
                        pdf_data.update({
                            "report_date": self.report_service.get_session_start_time().strftime("%Y年%m月%d日") if self.report_service.session_start_time else "未知",
                            "session_duration_minutes": self.report_service.get_session_duration_minutes(),
                            "conversation_rounds": self.report_service.round_count,
                            "end_type": EndType.QUIT.value,
                        })
                        
                        save_result = self.data_manager.save_session_report(researcher_report, "User Switched", EndType.QUIT.value)
                        session_folder = os.path.dirname(save_result.get("report_path", "")) if save_result else None
                        
                        if session_folder and os.path.isdir(session_folder):
                            pdf_generator.generate_report(pdf_data, session_folder)
                            print("[INFO] Switch User PDF generated.")
                            
                    except Exception as e:
                        print(f"[ERROR] Switch User PDF failed: {e}")
                    
                    # Reset after generation
                    self.root.after(0, self._reset_ui_for_new_session)
                    
                except Exception as e:
                    print(f"[ERROR] End session failed: {e}")
                    self.root.after(0, self._reset_ui_for_new_session)
            
            threading.Thread(target=end_and_reset, daemon=True).start()
            
        else:
            # Just reset directly if no active session
            self._reset_ui_for_new_session()

    def _reset_ui_for_new_session(self, clear_chat=True):
        """Reset UI to initial state for new user."""
        # 1. Unlock form fields
        self.user_id_entry.config(state="normal")
        self.user_id_entry.delete(0, tk.END)
        
        self.gender_combo.config(state="readonly")
        self.gender_combo.set("")
        
        self.age_entry.config(state="normal")
        self.age_entry.delete(0, tk.END)
        
        self.education_combo.config(state="readonly")
        self.education_combo.set("")
        
        self.marital_combo.config(state="readonly")
        self.marital_combo.set("")
        
        self.drug_type_combo.config(state="readonly")
        self.drug_type_combo.set("")
        
        # 2. Reset buttons
        self.btn_confirm_user.config(text="确认信息并开始", bg="#4CAF50", state="normal")
        self.btn_modify_user.config(state="disabled", bg="#ddd")
        self.setButtonsState("disabled")
        
        # 3. Reset logic state
        self.info_confirmed = False
        self.confirmed_user_id = ""
        self.current_user_var.set("请填写基本信息后开始对话")
        self.status_var.set("等待用户输入信息")
        
        # 4. Clear chat (only if requested)
        if clear_chat:
            self.chat_text.config(state=tk.NORMAL)
            self.chat_text.delete(1.0, tk.END)
            self.chat_text.config(state=tk.DISABLED)
        
        # 5. Stop any playing audio/TTS
        if self.tts_service:
            self.tts_service.stop_playing()
            
    def setButtonsState(self, state):
        cfg = {"state": state}
        if state == "normal":
             cfg["bg"] = "#81C784" # Restore green for start button
        elif state == "disabled":
             pass # Keep existing color (usually gray or red if recording)

        self.btn_start.config(state=state)
        # Stop button is managed by startRec
        self.btn_breathing.config(state=state)
        self.btn_muscle.config(state=state)
        self.btn_meditation.config(state=state)

    def start_audio_monitor(self):
        # Optional: Implement VAD visualization if needed
        pass

if __name__ == "__main__":
    app = VoiceChatApp()
