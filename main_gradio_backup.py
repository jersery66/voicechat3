# Voice Chat Application - Gradio Web UI Version

import os
import sys

# Disable proxy and SSL verification to avoid network issues
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""
os.environ["http_proxy"] = ""
os.environ["https_proxy"] = ""

import time
import queue
import threading
import numpy as np
import gradio as gr

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.stt_service import STTService
from services.llm_service import LLMService
from services.tts_service import TTSService
from data.data_manager import DataManager
from config import APP_NAME, OLLAMA_MODEL


class VoiceChatApp:
    """Voice Chat Application with Gradio UI."""
    
    def __init__(self):
        self.stt_service = None
        self.llm_service = None
        self.tts_service = None
        self.data_manager = None
        
        self.is_recording = False
        self.models_loaded = False
        self.conversation_history = []
        
    def load_models(self, progress=gr.Progress()):
        """Load all AI models."""
        import traceback
        
        progress(0, desc="初始化服务...")
        
        # Initialize services
        self.stt_service = STTService()
        self.llm_service = LLMService()
        self.tts_service = TTSService()
        self.data_manager = DataManager()
        
        progress(0.1, desc="加载语音识别模型...")
        try:
            self.stt_service.load_model()
        except Exception as e:
            error_detail = traceback.format_exc()
            print(f"STT Error: {error_detail}")
            return f"❌ STT模型加载失败: {e}\n{error_detail}"
        
        progress(0.5, desc="加载语音合成模型...")
        try:
            self.tts_service.load_model(use_streaming=False)
        except Exception as e:
            error_detail = traceback.format_exc()
            print(f"TTS Error: {error_detail}")
            return f"❌ TTS模型加载失败: {e}\n详情请查看终端"
        
        progress(0.9, desc="连接语言模型...")
        if not self.llm_service.test_connection():
            return "❌ 无法连接到Ollama，请确保Ollama服务正在运行"
        
        progress(1.0, desc="完成!")
        self.models_loaded = True
        self.data_manager.start_new_session()
        
        return "✅ 模型加载完成！点击麦克风开始对话"
    
    def process_audio(self, audio, chat_history, user_id):
        """Process recorded audio and generate response."""
        if not self.models_loaded:
            return chat_history, None, "❌ 模型未加载"
        
        if audio is None:
            return chat_history, None, "请先录制语音"
        
        sample_rate, audio_data = audio
        
        # Convert to float32 if needed
        if audio_data.dtype != np.float32:
            audio_data = audio_data.astype(np.float32) / 32768.0
        
        # Flatten if stereo
        if len(audio_data.shape) > 1:
            audio_data = audio_data.mean(axis=1)
        
        # Step 1: Transcribe
        yield chat_history, None, "📝 正在识别语音..."
        
        try:
            text = self.stt_service.transcribe(audio_data)
        except Exception as e:
            yield chat_history, None, f"❌ 识别失败: {e}"
            return
        
        if not text.strip():
            yield chat_history, None, "未检测到语音内容，请重试"
            return
        
        # Add user message to history
        chat_history = chat_history + [(text, None)]
        yield chat_history, None, "🤔 正在思考..."
        
        # Save user audio and text
        if user_id:
            self.data_manager.set_user_id(user_id)
        self.data_manager.save_user_message(audio_data, text)
        
        # Step 2: Get LLM response (streaming)
        full_response = ""
        try:
            for chunk in self.llm_service.chat(text):
                full_response += chunk
                # Update the last assistant message
                chat_history[-1] = (text, full_response)
                yield chat_history, None, "🤔 正在生成回复..."
        except Exception as e:
            yield chat_history, None, f"❌ LLM响应失败: {e}"
            return
        
        # Step 3: Generate TTS
        yield chat_history, None, "🔊 正在合成语音..."
        
        try:
            audio_output = self.tts_service.generate(full_response)
            
            # Save assistant response
            self.data_manager.save_assistant_message(audio_output, full_response, sample_rate=24000)
            
            # Return audio as (sample_rate, data) tuple
            output_audio = (24000, audio_output)
            yield chat_history, output_audio, "✅ 完成"
            
        except Exception as e:
            yield chat_history, None, f"❌ TTS合成失败: {e}"
    
    def clear_chat(self):
        """Clear chat history."""
        self.conversation_history = []
        if self.llm_service:
            self.llm_service.reset_conversation()
        if self.data_manager:
            self.data_manager.start_new_session()
        return [], None, "聊天已清空"
    
    def update_settings(self, user_id, model_name):
        """Update user settings."""
        if self.data_manager and user_id:
            self.data_manager.set_user_id(user_id)
        if self.llm_service and model_name:
            self.llm_service.model = model_name
        return f"设置已更新: 用户={user_id}, 模型={model_name}"


def create_ui():
    """Create the Gradio UI with floating panel layout."""
    app = VoiceChatApp()
    
    # Auto-detect available Ollama models
    try:
        import ollama
        client = ollama.Client()
        models_list = client.list()
        available_models = [m["name"] for m in models_list.get("models", [])]
    except:
        available_models = ["qwen2.5:7b", "deepseek-r1:latest"]
    
    default_model = available_models[0] if available_models else "qwen2.5:7b"
    
    # Get app directory for paths
    app_dir = os.path.dirname(os.path.abspath(__file__))
    bg_image_path = os.path.join(app_dir, "ui", "background.jpg").replace("\\", "/")
    
    # Custom CSS with floating panels and full-screen background
    custom_css = f"""
    /* 1. 全局背景设置 */
    body, gradio-app {{
        background-image: url('file={bg_image_path}') !important;
        background-size: cover !important;
        background-position: center center !important;
        background-attachment: fixed !important;
        background-repeat: no-repeat !important;
        margin: 0 !important;
        padding: 0 !important;
        min-height: 100vh !important;
    }}

    /* 2. 让 Gradio 默认容器透明 */
    .gradio-container {{
        background: transparent !important;
        max-width: 100% !important;
        padding: 10px !important;
    }}
    
    .contain {{
        background: transparent !important;
    }}

    /* 隐藏页脚 */
    footer {{ display: none !important; }}

    /* 3. 左侧控制面板样式 */
    .left-panel {{
        background: rgba(245, 245, 245, 0.95) !important;
        padding: 20px !important;
        border: 2px solid #333 !important;
        border-radius: 12px !important;
        box-shadow: 5px 5px 20px rgba(0,0,0,0.4) !important;
    }}

    /* 4. 右侧对话记录样式 */
    .right-panel {{
        background: rgba(255, 255, 255, 0.95) !important;
        border: 2px solid #333 !important;
        border-radius: 12px !important;
        box-shadow: 5px 5px 20px rgba(0,0,0,0.4) !important;
        padding: 15px !important;
    }}

    /* 5. 按钮样式 */
    .custom-btn {{
        margin-bottom: 8px !important;
        border-radius: 8px !important;
        border: 1px solid #666 !important;
        box-shadow: 2px 2px 5px rgba(0,0,0,0.2) !important;
        font-size: 14px !important;
        padding: 12px !important;
    }}

    /* 绿色按钮 (呼吸训练) */
    .green-btn {{
        background: linear-gradient(135deg, #22C55E, #16A34A) !important;
        color: white !important;
    }}
    .green-btn:hover {{
        background: linear-gradient(135deg, #16A34A, #22C55E) !important;
    }}

    /* 蓝色按钮 (肌肉训练) */
    .blue-btn {{
        background: linear-gradient(135deg, #3B82F6, #2563EB) !important;
        color: white !important;
    }}
    .blue-btn:hover {{
        background: linear-gradient(135deg, #2563EB, #3B82F6) !important;
    }}

    /* 紫色按钮 (冥想训练) */
    .purple-btn {{
        background: linear-gradient(135deg, #8B5CF6, #7C3AED) !important;
        color: white !important;
    }}
    .purple-btn:hover {{
        background: linear-gradient(135deg, #7C3AED, #8B5CF6) !important;
    }}

    /* 红色按钮 (停止/退出) */
    .red-btn {{
        background: linear-gradient(135deg, #EF4444, #DC2626) !important;
        color: white !important;
    }}
    .red-btn:hover {{
        background: linear-gradient(135deg, #DC2626, #EF4444) !important;
    }}
    
    /* 设置面板标题 */
    .panel-title {{
        color: #333 !important;
        font-weight: bold !important;
        margin-bottom: 10px !important;
    }}
    
    /* 状态文本 */
    .status-text {{
        color: #0066cc !important;
        font-weight: bold !important;
    }}
    
    /* 底部Logo */
    .bottom-logo {{
        background: #fdf6ca !important;
        padding: 10px 20px !important;
        border: 2px solid #333 !important;
        border-radius: 8px !important;
        font-size: 16px !important;
        font-weight: bold !important;
        text-align: center !important;
        color: #333 !important;
    }}
    
    /* 折叠面板样式 */
    .settings-accordion {{
        background: rgba(245, 245, 245, 0.9) !important;
        border-radius: 8px !important;
        margin-top: 10px !important;
    }}
    """
    
    with gr.Blocks(title=APP_NAME, css=custom_css) as demo:
        
        with gr.Row():
            # --- 左侧控制面板 ---
            with gr.Column(scale=1, elem_classes=["left-panel"]):
                gr.Markdown("### 🎙️ 控制面板", elem_classes=["panel-title"])
                
                # 用户编号
                gr.Markdown("**用户编号:**")
                user_id = gr.Textbox(value="default_user", label="", container=False, show_label=False)
                status_display = gr.Markdown("<span style='color:blue'>当前用户: default_user</span>")
                
                gr.Markdown("---")
                
                # 录音控制
                audio_input = gr.Audio(
                    sources=["microphone"],
                    type="numpy",
                    label="🎤 录音"
                )
                
                with gr.Row():
                    submit_btn = gr.Button("📤 发送录音", elem_classes=["custom-btn"], variant="primary")
                
                gr.Markdown("---")
                gr.Markdown("**🎬 放松训练:**")
                
                # 放松训练按钮
                breath_btn = gr.Button("🌬️ 呼吸放松训练", elem_classes=["custom-btn", "green-btn"])
                muscle_btn = gr.Button("💪 肌肉放松训练", elem_classes=["custom-btn", "blue-btn"])
                meditation_btn = gr.Button("🧘 冥想放松训练", elem_classes=["custom-btn", "purple-btn"])
                
                gr.Markdown("---")
                
                clear_btn = gr.Button("🗑️ 清除对话历史", elem_classes=["custom-btn"])
                exit_btn = gr.Button("🚪 退出程序", elem_classes=["custom-btn", "red-btn"])
                
                # 设置折叠面板
                with gr.Accordion("⚙️ 高级设置", open=False, elem_classes=["settings-accordion"]):
                    model_choice = gr.Dropdown(
                        label="Ollama模型",
                        choices=available_models if available_models else ["qwen2.5:7b"],
                        value=default_model,
                        interactive=True
                    )
                    update_btn = gr.Button("💾 保存设置", variant="secondary", size="sm")
                    load_btn = gr.Button("📦 加载模型", variant="primary", size="sm")
                    status_text = gr.Textbox(
                        label="状态",
                        value="点击加载模型按钮初始化",
                        interactive=False,
                        lines=2
                    )
            
            # --- 右侧对话面板 ---
            with gr.Column(scale=2, elem_classes=["right-panel"]):
                gr.Markdown("### 💬 对话记录", elem_classes=["panel-title"])
                
                chatbot = gr.Chatbot(
                    label="",
                    show_label=False,
                    height=500
                )
                
                audio_output = gr.Audio(
                    label="� 语音回复",
                    autoplay=True
                )
        
        # 底部 Logo
        with gr.Row():
            gr.HTML(
                """
                <div style="background: #fdf6ca; padding: 10px 20px; border: 2px solid #333; 
                            border-radius: 8px; text-align: center; margin: 10px auto; 
                            display: inline-block; font-weight: bold; color: #333;">
                    南昌市强制隔离戒毒所<br>
                    心理矫治中心
                </div>
                """
            )
        
        # --- 功能函数 ---
        def update_user_display(user):
            return f"<span style='color:blue'>当前用户: {user}</span>"
        
        def play_muscle_video():
            video_path = os.path.join(app_dir, "肌肉放松.mp4")
            if os.path.exists(video_path):
                os.startfile(video_path)
                return "正在播放肌肉放松视频..."
            return "视频文件不存在"
        
        def play_breath_video():
            video_path = os.path.join(app_dir, "呼吸训练.mp4")
            if os.path.exists(video_path):
                os.startfile(video_path)
                return "正在播放呼吸训练视频..."
            return "视频文件不存在"
        
        def play_meditation_video():
            video_path = os.path.join(app_dir, "冥想训练.mp4")
            if os.path.exists(video_path):
                os.startfile(video_path)
                return "正在播放冥想训练视频..."
            return "视频文件不存在"
        
        def exit_app():
            def delayed_exit():
                time.sleep(0.5)
                os._exit(0)
            threading.Thread(target=delayed_exit, daemon=True).start()
        
        # --- 事件绑定 ---
        user_id.change(fn=update_user_display, inputs=[user_id], outputs=[status_display])
        
        muscle_btn.click(fn=play_muscle_video, outputs=[status_text])
        breath_btn.click(fn=play_breath_video, outputs=[status_text])
        meditation_btn.click(fn=play_meditation_video, outputs=[status_text])
        
        exit_btn.click(
            fn=exit_app, 
            inputs=[], 
            outputs=[],
            js="() => { setTimeout(() => { window.close(); }, 100); }"
        )
        
        load_btn.click(
            fn=app.load_models,
            inputs=[],
            outputs=[status_text]
        )
        
        submit_btn.click(
            fn=app.process_audio,
            inputs=[audio_input, chatbot, user_id],
            outputs=[chatbot, audio_output, status_text]
        )
        
        clear_btn.click(
            fn=app.clear_chat,
            inputs=[],
            outputs=[chatbot, audio_output, status_text]
        )
        
        update_btn.click(
            fn=app.update_settings,
            inputs=[user_id, model_choice],
            outputs=[status_text]
        )
        
        audio_input.stop_recording(
            fn=app.process_audio,
            inputs=[audio_input, chatbot, user_id],
            outputs=[chatbot, audio_output, status_text]
        )
    
    return demo


if __name__ == "__main__":
    # Disable proxy for localhost
    os.environ["NO_PROXY"] = "localhost,127.0.0.1"
    os.environ["no_proxy"] = "localhost,127.0.0.1"
    
    # Get app directory for allowed paths
    app_dir = os.path.dirname(os.path.abspath(__file__))
    
    demo = create_ui()
    demo.launch(
        share=False,
        server_name="0.0.0.0",
        server_port=7870,
        show_error=True,
        inbrowser=True,
        allowed_paths=[app_dir, os.path.join(app_dir, "ui")]  # Allow serving background image
    )
