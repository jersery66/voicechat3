# Voice Chat Application - Tkinter Version
import os
import sys
import time
import threading
import queue
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from PIL import Image, ImageTk

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.stt_service import STTService
from services.llm_service import LLMService
from services.tts_service import TTSService
from services.video_service import get_video_player
from services.report_service import ReportService, EndType
from data.data_manager import DataManager
from config import APP_NAME, CRISIS_HOTLINES, GREETING_MESSAGE, POST_RELAXATION_MESSAGE

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

    def createUI(self):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("1366x768")

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

        # Left Control Panel (hidden during loading)
        self.control_frame = tk.Frame(self.root, bg="#f0f0f0", relief=tk.RAISED, bd=2)
        # self.control_frame.place(relx=0.05, rely=0.5, anchor="w", width=300, height=600)

        # Right Chat Area (hidden during loading)
        self.chat_frame = tk.Frame(self.root, bg="#ffffff", relief=tk.SUNKEN, bd=2)
        # self.chat_frame.place(relx=0.95, rely=0.5, anchor="e", width=600, height=600)

        # User ID Section
        user_id_frame = tk.Frame(self.control_frame, bg="#f0f0f0")
        user_id_frame.pack(pady=10, fill=tk.X)

        tk.Label(user_id_frame, text="用户编号:", bg="#f0f0f0",
                 font=("Arial", 10, "bold")).pack(anchor="w", padx=5)

        # Entry and confirm button in a row
        entry_row = tk.Frame(user_id_frame, bg="#f0f0f0")
        entry_row.pack(fill=tk.X, padx=5)
        
        self.user_id_entry = tk.Entry(entry_row, width=12, font=("Arial", 10))
        self.user_id_entry.pack(side=tk.LEFT, pady=5)
        self.user_id_entry.insert(0, "default_user")
        
        # Store confirmed user ID
        self.confirmed_user_id = "default_user"
        
        self.btn_confirm_user = tk.Button(entry_row, text="确认", width=6, 
                                          command=self._confirm_user_id,
                                          bg="#4CAF50", fg="white", font=("Arial", 9))
        self.btn_confirm_user.pack(side=tk.LEFT, padx=5, pady=5)

        self.current_user_var = tk.StringVar(value="当前用户: default_user")
        tk.Label(user_id_frame, textvariable=self.current_user_var,
                 bg="#f0f0f0", font=("Arial", 9), fg="blue").pack(anchor="w", padx=5)

        # Buttons
        self.btn_start = tk.Button(self.control_frame, text="开始录音", width=20, height=2, 
                                   command=self.startRec, bg="#81C784", fg="white", font=("Arial", 10, "bold"))
        
        self.btn_stop = tk.Button(self.control_frame, text="停止录音并发送", width=20, height=2, 
                                  command=self.stopRec, bg="#E0E0E0", state="disabled") # Gray initially

        self.relaxation_label = tk.Label(self.control_frame, text="放松训练", font=("Arial", 12, "bold"),
                                         bg="#f0f0f0", fg="#333333")
        
        self.btn_breathing = tk.Button(self.control_frame, text="呼吸放松训练", width=20, height=1,
                                       command=lambda: self._play_video_with_animation(self.btn_breathing, "#64B5F6", "呼吸训练.mp4"),
                                       bg="#64B5F6", fg="white", font=("Arial", 10))
        self.btn_muscle = tk.Button(self.control_frame, text="肌肉放松训练", width=20, height=1,
                                    command=lambda: self._play_video_with_animation(self.btn_muscle, "#4DD0E1", "肌肉放松.mp4"),
                                    bg="#4DD0E1", fg="white", font=("Arial", 10))
        self.btn_meditation = tk.Button(self.control_frame, text="冥想放松训练", width=20, height=1,
                                        command=lambda: self._play_video_with_animation(self.btn_meditation, "#9575CD", "冥想训练.mp4"),
                                        bg="#9575CD", fg="white", font=("Arial", 10))
        self.btn_stop_video = tk.Button(self.control_frame, text="停止视频播放", width=20, height=1,
                                        command=self.stop_relaxation_video,
                                        bg="#FFD54F", fg="white", font=("Arial", 10)) # Amber

        self.btn_clear = tk.Button(self.control_frame, text="清除对话历史", width=20, height=2,
                                   command=self.clearHistory, bg="#E0E0E0")
        self.btn_exit = tk.Button(self.control_frame, text="退出程序", width=20, height=2, 
                                  command=self.exitApp, bg="#EF5350", fg="white") # Red 400

        self.status_label = tk.Label(self.control_frame, textvariable=self.status_var, fg="#1565C0", bg="#f0f0f0",
                                     wraplength=280, justify=tk.LEFT, font=("Arial", 9))

        # Packing
        self.btn_start.pack(pady=5)
        self.btn_stop.pack(pady=5)
        
        self.relaxation_label.pack(pady=(15, 5))
        self.btn_breathing.pack(pady=2)
        self.btn_muscle.pack(pady=2)
        self.btn_meditation.pack(pady=2)
        self.btn_stop_video.pack(pady=5)

        self.btn_clear.pack(pady=10)
        self.btn_exit.pack(pady=5)
        self.status_label.pack(pady=10, fill=tk.X)

        # Chat Area
        self.chat_title = tk.Label(self.chat_frame, text="对话记录", font=("Arial", 14, "bold"), bg="#ffffff")
        self.chat_title.pack(pady=5)

        self.scrollbar = tk.Scrollbar(self.chat_frame)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.chat_text = tk.Text(self.chat_frame, yscrollcommand=self.scrollbar.set,
                                 wrap=tk.WORD, state=tk.DISABLED, bg="#FAFAFA",
                                 font=("Microsoft YaHei", 12)) # Better font for reading
        self.chat_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
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
                    end_type, visitor_feedback, relaxation_rec = content
                    self._show_session_end_dialog(end_type, visitor_feedback, relaxation_rec)
                elif msg_type == "show_crisis_resources":
                    # Show crisis hotlines for safety endings
                    self._show_crisis_resources_dialog()
                elif msg_type == "play_greeting":
                    # Play opening greeting after UI is ready
                    self.root.after(1000, self._play_opening_greeting)  # Delay 1s after UI shows
                elif msg_type == "post_relaxation_greeting":
                    # Play greeting after relaxation video finishes
                    self.root.after(500, self._play_post_relaxation_greeting)
                elif msg_type == "start_keepalive":
                    # Start Ollama keep-alive timer
                    self._start_ollama_keepalive()
                elif msg_type == "error":
                    messagebox.showerror("错误", content)
                    self.status_var.set("发生错误")
                    self.setButtonsState("normal")
                
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
            
            # After UI is ready, play the greeting
            self.processing_queue.put(("play_greeting", None))
            
            # Start Ollama keep-alive timer (every 3 minutes)
            self.processing_queue.put(("start_keepalive", None))
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.processing_queue.put(("error", f"模型加载失败: {str(e)}"))

    def startRec(self):
        if not self.models_loaded:
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
        self.btn_start.config(state="normal", bg="#81C784") # Restore Green
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
            
            # Creating a generator for LLM
            llm_gen = self.llm_service.chat(text)
            
            # Buffer all chunks (don't display raw output to avoid showing analysis/tags)
            for chunk in llm_gen:
                full_response += chunk
            
            # DEBUG: Print full response
            print(f"[DEBUG] LLM完整响应长度: {len(full_response)}")
            print(f"[DEBUG] LLM响应前200字符: {full_response[:200] if full_response else '空响应'}")
            
            # 4. Parse structured output (心理分析|||口语回复)
            analysis_text = ""
            spoken_text = full_response
            
            if "|||" in full_response:
                parts = full_response.split("|||", 1)
                part1 = parts[0].strip()
                part2 = parts[1].strip() if len(parts) > 1 else ""
                
                # Handle edge case: if ||| is at the end (part2 empty), use part1 as spoken text
                if part2:
                    # Normal case: 心理分析|||口语回复
                    analysis_text = part1
                    spoken_text = part2
                else:
                    # Edge case: LLM output ends with ||| or only has ||| separator
                    # Use part1 as the spoken text (assume no analysis provided)
                    analysis_text = ""
                    spoken_text = part1
                    print(f"[DEBUG] 警告: LLM输出以|||结尾，使用前半部分作为口语文本")
                
                # Save analysis to log file for research (only if we have real analysis)
                if analysis_text:
                    self._save_analysis_log(user_id, text, analysis_text, spoken_text)
                    print(f"[DEBUG] 心理分析: {analysis_text[:100]}...")
            
            # 5. Check for session end tags
            end_type = self.report_service.check_session_end(spoken_text)
            spoken_text = self.report_service.strip_end_tags(spoken_text)
            
            # 6. Extract relaxation control tags from spoken text
            control_tags = {
                "[REC_BREATHING]": "呼吸",
                "[REC_MUSCLE]": "肌肉",
                "[REC_MEDITATION]": "冥想"
            }
            
            detected_tag = None
            for tag, keyword in control_tags.items():
                if tag in spoken_text:
                    detected_tag = keyword
                    spoken_text = spoken_text.replace(tag, "").strip()
                    break
            
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
            
            # 8. Display cleaned response in UI with typewriter effect
            self.processing_queue.put(("append_chat", ("ai_start", "")))
            
            # Stream the cleaned spoken text character by character for typewriter effect
            for char in spoken_text:
                self.processing_queue.put(("stream_update", char))
                # Small delay handled by queue processing
            
            self.processing_queue.put(("stream_end", ""))
            
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
                self.control_frame.place(relx=-0.3, rely=0.5, anchor="w", width=300, height=600)
                self.chat_frame.place(relx=1.3, rely=0.5, anchor="e", width=600, height=600)
            
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
                self.control_frame.place(relx=0.05, rely=0.5, anchor="w", width=300, height=600)
                self.chat_frame.place(relx=0.95, rely=0.5, anchor="e", width=600, height=600)
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
        def greeting_task():
            try:
                # Display greeting in chat
                self.processing_queue.put(("append_chat", ("ai", GREETING_MESSAGE)))
                
                # Update status
                self.processing_queue.put(("status", "🔊 正在播放问候..."))
                
                # Play greeting via TTS
                tts_audio_data = self.tts_service.generate_and_play(GREETING_MESSAGE)
                
                # Save to conversation history so LLM knows what was said
                self.llm_service.conversation_history.append({
                    "role": "assistant",
                    "content": GREETING_MESSAGE
                })
                
                # Optionally save the greeting message
                if tts_audio_data is not None and len(tts_audio_data) > 0:
                    self.data_manager.save_assistant_message(tts_audio_data, GREETING_MESSAGE)
                
                self.processing_queue.put(("status", "✅ 就绪 - 点击录音开始对话"))
                
            except Exception as e:
                print(f"[WARNING] 问候播放失败: {e}")
                self.processing_queue.put(("status", "✅ 就绪 - 点击录音开始对话"))
        
        threading.Thread(target=greeting_task, daemon=True).start()
    
    def _play_post_relaxation_greeting(self):
        """Play greeting after relaxation training completes."""
        def greeting_task():
            try:
                # Display greeting in chat
                self.processing_queue.put(("append_chat", ("ai", POST_RELAXATION_MESSAGE)))
                
                # Update status
                self.processing_queue.put(("status", "🔊 正在询问感受..."))
                
                # Play greeting via TTS
                tts_audio_data = self.tts_service.generate_and_play(POST_RELAXATION_MESSAGE)
                
                # Save to conversation history so LLM knows what was said
                self.llm_service.conversation_history.append({
                    "role": "assistant",
                    "content": POST_RELAXATION_MESSAGE
                })
                
                # Optionally save the message
                if tts_audio_data is not None and len(tts_audio_data) > 0:
                    self.data_manager.save_assistant_message(tts_audio_data, POST_RELAXATION_MESSAGE)
                
                self.processing_queue.put(("status", "✅ 就绪 - 点击录音回答"))
                
            except Exception as e:
                print(f"[WARNING] 放松后问候播放失败: {e}")
                self.processing_queue.put(("status", "✅ 就绪"))
        
        threading.Thread(target=greeting_task, daemon=True).start()
    
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
    
    def _handle_session_end(self, end_type: EndType, relaxation_tag: str = None):
        """
        Handle session end: generate reports and trigger UI updates.
        
        Args:
            end_type: The type of session ending
            relaxation_tag: Optional detected relaxation recommendation
        """
        self.session_ended = True
        self.processing_queue.put(("status", "📊 正在生成反馈..."))
        
        # Run report generation in thread to not block
        def generate_reports():
            try:
                user_id = self.user_id_entry.get().strip() or "default_user"
                
                # Get conversation history from LLM service
                conversation_history = self.llm_service.conversation_history
                
                # Get relaxation recommendation from detected tag (if any)
                relaxation_rec = None
                if relaxation_tag:
                    tag_map = {"呼吸": "BREATHING", "肌肉": "MUSCLE", "冥想": "MEDITATION"}
                    relaxation_rec = tag_map.get(relaxation_tag)
                
                # ===== STEP 1: Generate visitor feedback FIRST (fast) =====
                print("[INFO] 生成来访者反馈...")
                visitor_feedback = self.report_service.generate_visitor_feedback(
                    conversation_history, end_type, relaxation_rec
                )
                
                # ===== STEP 2: Immediately trigger UI and TTS playback =====
                self.processing_queue.put(("session_end", (end_type, visitor_feedback, relaxation_rec)))
                
                # Show crisis resources for safety endings
                if end_type == EndType.SAFETY:
                    self.processing_queue.put(("show_crisis_resources", None))
                
                # ===== STEP 3: Generate researcher report in background (slow) =====
                print("[INFO] 后台生成研究人员报告...")
                researcher_report = self.report_service.generate_researcher_report(
                    conversation_history, user_id, end_type
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
                
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.processing_queue.put(("error", f"报告生成失败: {str(e)}"))
        
        threading.Thread(target=generate_reports, daemon=True).start()
    
    def _show_session_end_dialog(self, end_type: EndType, visitor_feedback: str, relaxation_rec: str = None):
        """
        Show session end dialog with visitor feedback.
        
        Args:
            end_type: The type of session ending
            visitor_feedback: The oral-style feedback text
            relaxation_rec: Optional relaxation recommendation (BREATHING/MUSCLE/MEDITATION)
        """
        # Update status
        self.status_var.set("📋 会话已结束 - 正在播放反馈")
        
        # Add feedback to chat as system message
        end_type_names = {
            EndType.GOAL_ACHIEVED: "目标达成",
            EndType.TIME_LIMIT: "时间结束",
            EndType.SAFETY: "安全转介",
            EndType.INVALID: "对话结束"
        }
        end_name = end_type_names.get(end_type, "会话结束")
        self.append_to_chat("system", f"[{end_name}] 正在为您播放反馈...")
        
        # Play feedback via TTS in separate thread
        def play_feedback():
            try:
                self.tts_service.generate_and_play(visitor_feedback)
                
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
        
        threading.Thread(target=play_feedback, daemon=True).start()
        
        # Display feedback in chat
        self.append_to_chat("ai", f"[反馈] {visitor_feedback}")
        
        # Show simple dialog for important endings
        if end_type in [EndType.GOAL_ACHIEVED, EndType.SAFETY]:
            title = "会话总结" if end_type == EndType.GOAL_ACHIEVED else "重要提示"
            icon = "info" if end_type == EndType.GOAL_ACHIEVED else "warning"
            
            # Use non-blocking approach - just update the UI
            self.root.after(500, lambda: self._show_feedback_popup(title, visitor_feedback, end_type))
    
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
            
            # Insert clean text
            self.chat_text.insert(start_index, clean_text + "\n", "ai")
        
        self.chat_text.see(tk.END)
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
             
             # Run video player in a separate thread to not block UI/keep logic clean
             # Note: PyGame must run on the main thread often, OR carefully managed.
             # In standard Tkinter + PyGame integration, it's tricky.
             # BUT here we open a separate PyGame window from the Tkinter App.
             # Safest is to spawning a thread that initializes PyGame logic.
             # HOWEVER, PyGame on mac need main thread. Windows is more forgiving but still.
             # Let's try threaded invocation. If it fails, we might need a subprocess.
             
             # VideoService is imported inside method or top level?
             # VideoService is imported at top level

             
             def video_runner():
                 try:
                     player = get_video_player()
                     player.play_video(video_path)
                 except Exception as e:
                     print(f"Video runner exception: {e}")
                 finally:
                     print("[INFO] Video finished.")
                     # Ask about relaxation experience after video
                     self.processing_queue.put(("post_relaxation_greeting", None))
             
             threading.Thread(target=video_runner, daemon=True).start()
             
             # messagebox.showinfo("提示", "视频正在播放。按 Ctrl + Alt + Q (或 Win + Esc) 可强制退出。")

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
        if self.stt_service:
            try:
                self.stt_service.stop_recording()
            except:
                pass
        self.root.quit()
        self.root.destroy()
        sys.exit(0)

    def _confirm_user_id(self):
        """Confirm user ID change and start new conversation if different."""
        new_user_id = self.user_id_entry.get().strip() or "default_user"
        
        if new_user_id != self.confirmed_user_id:
            # User ID changed - start new conversation
            self.confirmed_user_id = new_user_id
            self.current_user_id = new_user_id  # Also update tracking variable
            self.current_user_var.set(f"当前用户: {new_user_id}")
            
            # Reset LLM conversation history
            if self.llm_service:
                self.llm_service.reset_conversation()
            
            # Clear chat display
            self.chat_text.config(state=tk.NORMAL)
            self.chat_text.delete(1.0, tk.END)
            self.chat_text.config(state=tk.DISABLED)
            
            # Start new data session
            if self.data_manager:
                self.data_manager.set_user_id(new_user_id)
                self.data_manager.start_new_session()
            
            # Reset report service session
            if self.report_service:
                self.report_service.start_session()
            self.session_ended = False
            
            # Visual feedback
            self.btn_confirm_user.config(bg="#8BC34A")  # Light green
            self.root.after(500, lambda: self.btn_confirm_user.config(bg="#4CAF50"))
            
            # Play greeting for new session
            self._play_opening_greeting()
        else:
            # Same user - just show feedback
            self.btn_confirm_user.config(bg="#FFC107")  # Yellow
            self.root.after(500, lambda: self.btn_confirm_user.config(bg="#4CAF50"))
            self.status_var.set(f"用户编号未改变: {new_user_id}")
        
    def setButtonsState(self, state):
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
