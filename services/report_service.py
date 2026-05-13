# Report Service - Session Report Generation System
# Supports composite ending judgment and dual-audience reports

import os
import sys
import json
import re
from datetime import datetime
from typing import Optional, Dict, Any, List
from enum import Enum

# Add parent directory to path for config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    OLLAMA_HOST, OLLAMA_MODEL,
    MAX_CONVERSATION_ROUNDS, MAX_CONVERSATION_MINUTES, TIME_WARNING_MINUTES,
    CRISIS_HOTLINES, RESEARCHER_REPORT_PROMPT, VISITOR_FEEDBACK_PROMPT,
    SESSION_SUMMARY_PROMPT, AGENT_REPORT_TIMEOUT
)


class EndType(Enum):
    """Session end types"""
    NONE = "NONE"                      # 未结束
    GOAL_ACHIEVED = "GOAL_ACHIEVED"    # 目标达成
    TIME_LIMIT = "TIME_LIMIT"          # 时间/轮次限制
    SAFETY = "SAFETY"                  # 安全边界（危机干预）
    INVALID = "INVALID"                # 无效对话
    QUIT = "QUIT"                      # 用户主动退出


class ReportService:
    """
    Report generation service for voice chat sessions.
    
    Supports:
    - Four end type detection (goal/time/safety/invalid)
    - Round/time tracking with warnings
    - Researcher report generation (JSON)
    - Visitor feedback generation (oral style for TTS)
    - Safety resources output
    """
    
    def __init__(self, llm_service=None, agent_service=None):
        """
        Initialize report service.

        Args:
            llm_service: Optional LLM service for report generation.
                        If not provided, will import when needed.
            agent_service: Optional 3B agent service for report generation.
                          Falls back to llm_service (72B) on failure.
        """
        self.llm_service = llm_service
        self.agent_service = agent_service
        self.session_start_time: Optional[datetime] = None
        self.round_count: int = 0
        self.time_warning_shown: bool = False
        self.completed_relaxation: Optional[str] = None # Track completed relaxation type
        
    def _get_llm_service(self):
        """Lazy load LLM service if not provided."""
        if self.llm_service is None:
            from services.llm_service import get_llm_service
            self.llm_service = get_llm_service()
        return self.llm_service

    def _get_agent_service(self):
        """Lazy load agent service if not provided."""
        if self.agent_service is None:
            from services.agent_service import get_agent_service
            self.agent_service = get_agent_service()
        return self.agent_service

    def _format_emotion_summary(self, session_emotions: list) -> str:
        """Format accumulated emotion data into a prompt section."""
        if not session_emotions:
            return ""
        _EMOTION_CN = {
            "neutral": "平静", "anxious": "焦虑", "depressed": "低落",
            "angry": "愤怒", "fearful": "恐惧", "hopeful": "有希望",
            "grateful": "感激", "lonely": "孤独", "confused": "困惑",
            "stressed": "压力大",
        }
        counts = {}
        for e in session_emotions:
            emo = e.get("emotion", "neutral")
            counts[emo] = counts.get(emo, 0) + 1
        dominant = sorted(counts.items(), key=lambda x: -x[1])[:3]
        parts = []
        for emo, _ in dominant:
            cn = _EMOTION_CN.get(emo, emo)
            parts.append(cn)
        trajectory = "、".join(parts)
        last = session_emotions[-1].get("emotion", "neutral")
        last_cn = _EMOTION_CN.get(last, last)
        return (
            f"- 情绪追踪轨迹: {trajectory}\n"
            f"- 最终情绪状态: {last_cn}"
        )

    # ==================== Session Tracking ====================
    
    def start_session(self):
        """Start tracking a new session."""
        self.session_start_time = datetime.now()
        self.round_count = 0
        self.time_warning_shown = False
        self.completed_relaxation = None
        self.game_results = None
        
    def record_relaxation(self, relaxation_name: str):
        """Record that a relaxation session was completed."""
        self.completed_relaxation = relaxation_name
        print(f"[INFO] ReportService recorded relaxation: {relaxation_name}")

    def record_game_session(self, game_results: dict):
        """Record therapeutic game session results."""
        self.game_results = game_results
        print(f"[INFO] ReportService recorded game session: {game_results}")

    def increment_round(self):
        """Increment conversation round count."""
        self.round_count += 1
        
    def get_session_duration_minutes(self) -> float:
        """Get session duration in minutes."""
        if self.session_start_time is None:
            return 0.0
        delta = datetime.now() - self.session_start_time
        return delta.total_seconds() / 60.0
    
    def get_round_count(self) -> int:
        """Get current round count."""
        return self.round_count
    
    def get_session_start_time(self) -> Optional[datetime]:
        """Get session start time."""
        return self.session_start_time
    
    # ==================== End Type Detection ====================

    def should_warn_time_limit(self) -> tuple[bool, str]:
        """
        Check if time/round warning should be shown.
        
        Returns:
            (should_warn, warning_message)
        """
        if self.time_warning_shown:
            return False, ""
            
        duration = self.get_session_duration_minutes()
        
        # Time warning (approaching limit)
        if duration >= TIME_WARNING_MINUTES:
            self.time_warning_shown = True
            remaining = MAX_CONVERSATION_MINUTES - duration
            return True, f"我们的对话已进行约{int(duration)}分钟，还剩约{int(remaining)}分钟。如有需要，可以延长或约定下次讨论。"
        
        # Round warning (approaching limit)
        # Round warning (approaching limit)
        # DISABLE round warning as per user request
        # if self.round_count >= MAX_CONVERSATION_ROUNDS - 2:
        #     self.time_warning_shown = True
        #     remaining = MAX_CONVERSATION_ROUNDS - self.round_count
        #     return False, ""
            
        return False, ""
    
    def is_over_limit(self) -> bool:
        """Check if session has exceeded time or round limits."""
        duration = self.get_session_duration_minutes()
        return (duration >= MAX_CONVERSATION_MINUTES or 
                self.round_count >= MAX_CONVERSATION_ROUNDS)
    
    # ==================== Report Generation ====================
    
    def generate_researcher_report(self,
                                   conversation_history: List[Dict],
                                   subject_id: str,
                                   end_type: EndType,
                                   user_info: Optional[Dict[str, Any]] = None,
                                   relaxation_info: str = "未进行",
                                   session_emotions: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """
        Generate researcher report (JSON format).
        
        Args:
            conversation_history: List of message dicts with 'role' and 'content'
            subject_id: Subject/participant ID
            end_type: How the session ended
            user_info: Optional dictionary containing user demographics
            relaxation_info: Description of completed relaxation training
            session_emotions: Accumulated emotion detection results from 3B agent

        Returns:
            Structured report dict
        """
        llm = self._get_llm_service()
        
        # Format conversation for analysis
        formatted_history = self._format_conversation(conversation_history)
        
        # Inject relaxation info explicitly into conversation history for LLM
        # This ensures the LLM 'sees' the event in the context
        actual_relaxation = relaxation_info if relaxation_info != "未进行" else (self.completed_relaxation or "未进行")
        if actual_relaxation != "未进行":
            formatted_history += f"\n【系统记录】来访者刚刚完成了{actual_relaxation}，目前状态应该有所缓解。"
        
        emotion_summary = self._format_emotion_summary(session_emotions)
        prompt = RESEARCHER_REPORT_PROMPT.format(
            conversation=formatted_history,
            subject_id=subject_id,
            duration_minutes=int(self.get_session_duration_minutes()),
            total_rounds=self.round_count,
            end_type=end_type.value,
            relaxation_info=relaxation_info if relaxation_info != "未进行" else (self.completed_relaxation or "未进行"),
            emotion_summary=emotion_summary
        )
        
        # Get analysis from LLM (use separate context to not pollute main conversation)
        try:
            # Try 3B agent first, fallback to 72B
            try:
                agent = self._get_agent_service()
                analysis_text = agent.generate_report(prompt, timeout=AGENT_REPORT_TIMEOUT)
                print("[AGENT] Report generated by 3B model")
            except Exception as agent_err:
                print(f"[WARNING] 3B report failed, falling back to 72B: {agent_err}")
                import ollama
                client = ollama.Client(host=OLLAMA_HOST)
                response = client.chat(
                    model=OLLAMA_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    stream=False
                )
                analysis_text = response["message"]["content"]
            
            # Attempt to parse as JSON, or wrap in structure
            report = self._parse_report_json(analysis_text, subject_id, end_type)
            
            # FORCE FIX: Check if we have explicit relaxation completion info
            # This overrides LLM potential "Not performed" hallucination
            actual_relaxation = relaxation_info if relaxation_info != "未进行" else (self.completed_relaxation or "未进行")
            
            if actual_relaxation != "未进行":
                # Infer code
                code = "NONE"
                if "呼吸" in actual_relaxation: code = "BREATHING"
                elif "肌肉" in actual_relaxation: code = "MUSCLE"
                elif "冥想" in actual_relaxation: code = "MEDITATION"
                
                # Update report fields
                report["relaxation_recommendation"] = code
                report["relaxation_completed_type"] = actual_relaxation # Helper field
                
                # Verify summary mentions it, if not, append it? 
                # (Risky to modify text, but ensures PDF shows it if PDF uses this)
            
            # Add user info if provided
            if user_info:
                report["user_info"] = user_info
                
            return report
            
        except Exception as e:
            # Return basic structure on error
            report = {
                "session_info": {
                    "subject_id": subject_id,
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "duration_minutes": int(self.get_session_duration_minutes()),
                    "total_rounds": self.round_count,
                    "end_type": end_type.value
                },
                "error": f"Report generation failed: {str(e)}",
                "raw_conversation": formatted_history
            }
            if user_info:
                report["user_info"] = user_info
            return report
    
    def generate_visitor_feedback(self,
                                  conversation_history: List[Dict],
                                  end_type: EndType,
                                  relaxation_rec: Optional[str] = None,
                                  stream: bool = False,
                                  session_emotions: Optional[List[Dict]] = None) -> Any:
        """
        Generate oral-style feedback for the visitor (TTS).
        
        Args:
            conversation_history: List of messages
            end_type: Session end reason
            relaxation_rec: Recommended relaxation type (BREATHING/MUSCLE/MEDITATION) or None
            stream: Whether to stream the response
            
        Returns:
            Feedback text string or generator (if stream=True)
        """
        llm = self._get_llm_service()
        formatted_history = self._format_conversation(conversation_history)
        
        # Map relaxation_rec code to Chinese for prompt
        rec_map = {
            "BREATHING": "呼吸放松训练",
            "MUSCLE": "肌肉放松训练", 
            "MEDITATION": "冥想放松训练"
        }
        rec_str = rec_map.get(relaxation_rec, "无") if relaxation_rec else "无"
        
        emotion_summary = self._format_emotion_summary(session_emotions)
        prompt = VISITOR_FEEDBACK_PROMPT.format(
            conversation=formatted_history,
            end_type=end_type.value,
            relaxation_recommendation=rec_str,
            emotion_summary=emotion_summary
        )

        try:
            # Try 3B agent first, fallback to 72B
            try:
                agent = self._get_agent_service()
                if stream:
                    def clean_stream(gen):
                        for chunk in gen:
                            yield self._clean_for_tts(chunk)
                    return clean_stream(agent.generate_report_stream(prompt, timeout=AGENT_REPORT_TIMEOUT))
                else:
                    feedback = agent.generate_report(prompt, timeout=AGENT_REPORT_TIMEOUT)
                    print("[AGENT] Visitor feedback generated by 3B model")
                    return self._clean_for_tts(feedback)
            except Exception as agent_err:
                print(f"[WARNING] 3B feedback failed, falling back to 72B: {agent_err}")
                import ollama
                client = ollama.Client(host=OLLAMA_HOST)
                if stream:
                    def stream_generator():
                        stream_response = client.chat(
                            model=OLLAMA_MODEL,
                            messages=[{"role": "user", "content": prompt}],
                            stream=True
                        )
                        for chunk in stream_response:
                            if "message" in chunk and "content" in chunk["message"]:
                                yield self._clean_for_tts(chunk["message"]["content"])
                    return stream_generator()
                else:
                    response = client.chat(
                        model=OLLAMA_MODEL,
                        messages=[{"role": "user", "content": prompt}],
                        stream=False
                    )
                    feedback = response["message"]["content"]
                    return self._clean_for_tts(feedback)

        except Exception as e:
            fallback = "今天聊得不错，辛苦你了。如果觉得不舒服，随时可以进行放松训练。再见。"
            if stream:
                def fallback_gen(): yield fallback
                return fallback_gen()
            return fallback

    def generate_session_summary(self, conversation_history: List[Dict], suggestions: str = None, stream: bool = False) -> Any:
        """
        Generate a warm closing summary with suggestions.
        
        Args:
            conversation_history: List of messages
            suggestions: Provided relaxation suggestions
            stream: Whether to stream the response (yield chunks)
            
        Returns:
            Summary text string or generator (if stream=True)
        """
        llm = self._get_llm_service()
        
        formatted_history = self._format_conversation(conversation_history)
        
        prompt = SESSION_SUMMARY_PROMPT.format(
            conversation=formatted_history,
            suggestions=suggestions or "无特定推荐"
        )

        try:
            # Try 3B agent first, fallback to 72B
            try:
                agent = self._get_agent_service()
                if stream:
                    def clean_stream(gen):
                        for chunk in gen:
                            yield self._clean_for_tts(chunk)
                    return clean_stream(agent.generate_report_stream(prompt, timeout=AGENT_REPORT_TIMEOUT))
                else:
                    summary = agent.generate_report(prompt, timeout=AGENT_REPORT_TIMEOUT)
                    print("[AGENT] Session summary generated by 3B model")
                    return self._clean_for_tts(summary)
            except Exception as agent_err:
                print(f"[WARNING] 3B summary failed, falling back to 72B: {agent_err}")
                import ollama
                client = ollama.Client(host=OLLAMA_HOST)
                if stream:
                    def stream_generator():
                        stream_response = client.chat(
                            model=OLLAMA_MODEL,
                            messages=[{"role": "user", "content": prompt}],
                            stream=True
                        )
                        for chunk in stream_response:
                            if "message" in chunk and "content" in chunk["message"]:
                                yield self._clean_for_tts(chunk["message"]["content"])
                    return stream_generator()
                else:
                    response = client.chat(
                        model=OLLAMA_MODEL,
                        messages=[{"role": "user", "content": prompt}],
                        stream=False
                    )
                    feedback = response["message"]["content"]
                    return self._clean_for_tts(feedback)

        except Exception as e:
            fallback = "今天聊了不少，辛苦你了。回去记得试试那几条建议，有事儿随时再来找我。"
            if stream:
                def fallback_gen(): yield fallback
                return fallback_gen()
            return fallback
    
    def generate_session_summary_async(self, conversation_history: List[Dict], callback=None) -> str:
        """
        Generate session summary in a non-blocking way.
        This method is designed to be called from a separate thread.
        
        Args:
            conversation_history: List of messages
            callback: Optional callback function(summary_text)
            
        Returns:
            Summary text string
        """
        formatted_history = self._format_conversation(conversation_history)
        
        prompt = SESSION_SUMMARY_PROMPT.format(
            conversation=formatted_history,
            suggestions="本次会话无特定建议"
        )

        try:
            # Try 3B agent first, fallback to 72B
            try:
                agent = self._get_agent_service()
                summary = agent.generate_report(prompt, timeout=AGENT_REPORT_TIMEOUT)
                print("[AGENT] Session summary (async) generated by 3B model")
            except Exception as agent_err:
                print(f"[WARNING] 3B summary_async failed, falling back to 72B: {agent_err}")
                import ollama
                client = ollama.Client(host=OLLAMA_HOST)
                response = client.chat(
                    model=OLLAMA_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    stream=False
                )
                summary = response["message"]["content"]
            summary = self._clean_for_tts(summary)
            
            if callback:
                callback(summary)
                
            return summary
            
        except Exception as e:
            print(f"[ERROR] generate_session_summary_async failed: {e}")
            fallback = "本次会话顺利完成。来访者表现出一定的开放态度，建议后续继续跟进。"
            if callback:
                callback(fallback)
            return fallback
    def generate_suggestions(self, conversation_history: List[Dict]) -> str:
        """
        Generate personalized suggestions based on conversation history.
        Used after relaxation training.
        
        Args:
            conversation_history: List of message dicts
            
        Returns:
            Short suggestions text (4 items, ~60 chars total)
        """
        from config import SUGGESTIONS_PROMPT
        
        llm = self._get_llm_service()
        formatted_history = self._format_conversation(conversation_history)
        
        prompt = SUGGESTIONS_PROMPT.format(conversation=formatted_history)

        try:
            # Try 3B agent first, fallback to 72B
            try:
                agent = self._get_agent_service()
                suggestions = agent.generate_report(prompt, timeout=AGENT_REPORT_TIMEOUT)
                print("[AGENT] Suggestions generated by 3B model")
            except Exception as agent_err:
                print(f"[WARNING] 3B suggestions failed, falling back to 72B: {agent_err}")
                import ollama
                client = ollama.Client(host=OLLAMA_HOST)
                response = client.chat(
                    model=OLLAMA_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    stream=False
                )
                suggestions = response["message"]["content"]

            # Clean up for TTS
            suggestions = self._clean_for_tts(suggestions)
            return suggestions

        except Exception as e:
            print(f"[ERROR] generate_suggestions failed: {e}")
            # Fallback suggestions
            return "睡不着时试试深呼吸。心里堵得慌就写两句。作息尽量规律。有空多走走晒太阳。"
    
    # ==================== Safety Resources ====================
    
    def get_crisis_resources(self) -> str:
        """Get formatted crisis hotline resources."""
        lines = ["紧急求助资源："]
        for name, number in CRISIS_HOTLINES.items():
            lines.append(f"• {name}: {number}")
        return "\n".join(lines)
    
    def get_crisis_resources_for_tts(self) -> str:
        """Get crisis resources in TTS-friendly format."""
        return "如果你感觉撑不住，可以拨打心理援助热线，电话是400-161-9995，24小时都有人接听。"
    
    # ==================== Relaxation Recommendation ====================
    
    def recommend_relaxation_strategy(self, conversation_history: List[Dict]) -> str:
        """
        Analyze conversation to recommend best relaxation strategy.
        
        Returns:
            One of: "呼吸", "肌肉", "冥想"
        """
        formatted_history = self._format_conversation(conversation_history)
        
        prompt = f"""你是一位专业的心理咨询师。请根据以下对话记录，判断来访者当前最适合哪种放松训练。

【对话记录】
{formatted_history}

【选项说明】
1. 呼吸 (BREATHING): 适合焦虑、紧张、惊恐发作、情绪激动
2. 肌肉 (MUSCLE): 适合身体紧绷、压力大、失眠、疲劳
3. 冥想 (MEDITATION): 适合思绪纷乱、担忧、强迫思维、注意力不集中

请分析用户的情绪和状态，只返回最合适的一个关键词（呼吸、肌肉、或 冥想）。
不要解释，只返回关键词。如果不确定，默认返回 呼吸。"""

        try:
            # Try 3B agent first, fallback to 72B
            try:
                agent = self._get_agent_service()
                content = agent.generate_report(prompt, timeout=AGENT_REPORT_TIMEOUT).strip()
                print("[AGENT] Relaxation recommendation by 3B model")
            except Exception as agent_err:
                print(f"[WARNING] 3B relaxation rec failed, falling back to 72B: {agent_err}")
                import ollama
                client = ollama.Client(host=OLLAMA_HOST)
                response = client.chat(
                    model=OLLAMA_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    stream=False
                )
                content = response["message"]["content"].strip()

            # Extract keyword
            if "肌肉" in content or "MUSCLE" in content.upper():
                return "肌肉"
            elif "冥想" in content or "MEDITATION" in content.upper():
                return "冥想"
            else:
                return "呼吸" # Default

        except Exception as e:
            print(f"[ERROR] Relaxation recommendation failed: {e}")
            return "呼吸" # Fallback
    
    def get_relaxation_recommendation(self, report: Dict[str, Any]) -> Optional[str]:
        """
        Extract relaxation recommendation from report.
        
        Returns:
            'BREATHING', 'MUSCLE', 'MEDITATION', or None
        """
        rec = report.get("relaxation_recommendation", "")
        if isinstance(rec, str):
            rec_upper = rec.upper()
            if "BREATHING" in rec_upper or "呼吸" in rec:
                return "BREATHING"
            elif "MUSCLE" in rec_upper or "肌肉" in rec:
                return "MUSCLE"
            elif "MEDITATION" in rec_upper or "冥想" in rec:
                return "MEDITATION"
        return None
    
    # ==================== Helper Methods ====================
    
    def _format_conversation(self, history: List[Dict]) -> str:
        """Format conversation history for LLM prompt."""
        lines = []
        for msg in history:
            role = "来访者" if msg.get("role") == "user" else "咨询师"
            content = msg.get("content", msg.get("text", ""))
            # Clean control tags from content
            content = re.sub(r'\[REC_\w+\]', '', content)
            content = re.sub(r'\[END_\w+\]', '', content)
            lines.append(f"【{role}】{content}")
        return "\n".join(lines)
    
    def _parse_report_json(self, text: str, subject_id: str, end_type: EndType) -> Dict:
        """Try to parse LLM response as JSON report."""
        # Try to find JSON block
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            try:
                report = json.loads(json_match.group())
                # Ensure required fields
                if "session_info" not in report:
                    report["session_info"] = {}
                report["session_info"].update({
                    "subject_id": subject_id,
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "duration_minutes": int(self.get_session_duration_minutes()),
                    "total_rounds": self.round_count,
                    "end_type": end_type.value
                })
                return report
            except json.JSONDecodeError:
                pass
        
        # Fallback: wrap text in structure
        return {
            "session_info": {
                "subject_id": subject_id,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "duration_minutes": int(self.get_session_duration_minutes()),
                "total_rounds": self.round_count,
                "end_type": end_type.value
            },
            "raw_analysis": text
        }
    
    def _get_safety_feedback_prompt(self, conversation: str) -> str:
        """Get special prompt for safety-end feedback."""
        return f"""你是一位经验丰富的心理咨询师。刚才的对话中，来访者表达了一些需要专业帮助的内容。

【对话记录】
{conversation}

请生成一段简短的、温暖的结束语（用于语音播放）：
1. 表达共情，让对方感到被理解
2. 温和地说明这需要更专业的帮助
3. 告知会提供求助电话
4. 保持连接感，告知可以再回来
5. 使用极度口语化的风格，就像面对面说话
6. 不超过50个字

只输出结束语本身，不要任何解释或标签。"""

    def _clean_for_tts(self, text: str) -> str:
        """Clean text for TTS playback."""
        # Remove markdown
        text = re.sub(r'\*+', '', text)
        text = re.sub(r'#+\s*', '', text)
        text = re.sub(r'\[.*?\]\(.*?\)', '', text)
        # Remove special brackets
        text = re.sub(r'【.*?】', '', text)
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text)
        return text.strip()


# Singleton instance
_report_service = None

def get_report_service() -> ReportService:
    global _report_service
    if _report_service is None:
        _report_service = ReportService()
    return _report_service
