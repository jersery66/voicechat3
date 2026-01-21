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
    CRISIS_HOTLINES, RESEARCHER_REPORT_PROMPT, VISITOR_FEEDBACK_PROMPT
)


class EndType(Enum):
    """Session end types"""
    NONE = "NONE"                      # 未结束
    GOAL_ACHIEVED = "GOAL_ACHIEVED"    # 目标达成
    TIME_LIMIT = "TIME_LIMIT"          # 时间/轮次限制
    SAFETY = "SAFETY"                  # 安全边界（危机干预）
    INVALID = "INVALID"                # 无效对话


# End type detection patterns
END_PATTERNS = {
    EndType.GOAL_ACHIEVED: r'\[END_GOAL_ACHIEVED\]',
    EndType.TIME_LIMIT: r'\[END_TIME_LIMIT\]',
    EndType.SAFETY: r'\[END_SAFETY\]',
    EndType.INVALID: r'\[END_INVALID\]',
}


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
    
    def __init__(self, llm_service=None):
        """
        Initialize report service.
        
        Args:
            llm_service: Optional LLM service for report generation.
                        If not provided, will import when needed.
        """
        self.llm_service = llm_service
        self.session_start_time: Optional[datetime] = None
        self.round_count: int = 0
        self.time_warning_shown: bool = False
        
    def _get_llm_service(self):
        """Lazy load LLM service if not provided."""
        if self.llm_service is None:
            from services.llm_service import get_llm_service
            self.llm_service = get_llm_service()
        return self.llm_service
    
    # ==================== Session Tracking ====================
    
    def start_session(self):
        """Start tracking a new session."""
        self.session_start_time = datetime.now()
        self.round_count = 0
        self.time_warning_shown = False
        
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
    
    # ==================== End Type Detection ====================
    
    def check_session_end(self, response: str) -> EndType:
        """
        Check if AI response contains session end tag.
        
        Args:
            response: AI response text
            
        Returns:
            EndType indicating the type of session end, or NONE
        """
        for end_type, pattern in END_PATTERNS.items():
            if re.search(pattern, response):
                return end_type
        return EndType.NONE
    
    def strip_end_tags(self, response: str) -> str:
        """Remove end tags from response text."""
        result = response
        for pattern in END_PATTERNS.values():
            result = re.sub(pattern, '', result)
        return result.strip()
    
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
        if self.round_count >= MAX_CONVERSATION_ROUNDS - 2:
            self.time_warning_shown = True
            remaining = MAX_CONVERSATION_ROUNDS - self.round_count
            return True, f"我们已交流约{self.round_count}轮，建议稍作整理。还可以继续{remaining}轮左右。"
            
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
                                   end_type: EndType) -> Dict[str, Any]:
        """
        Generate researcher report (JSON format).
        
        Args:
            conversation_history: List of message dicts with 'role' and 'content'
            subject_id: Subject/participant ID
            end_type: How the session ended
            
        Returns:
            Structured report dict
        """
        llm = self._get_llm_service()
        
        # Format conversation for analysis
        formatted_history = self._format_conversation(conversation_history)
        
        prompt = RESEARCHER_REPORT_PROMPT.format(
            conversation=formatted_history,
            subject_id=subject_id,
            duration_minutes=int(self.get_session_duration_minutes()),
            total_rounds=self.round_count,
            end_type=end_type.value
        )
        
        # Get analysis from LLM (use separate context to not pollute main conversation)
        import ollama
        client = ollama.Client(host=OLLAMA_HOST)
        
        try:
            response = client.chat(
                model=OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                stream=False
            )
            analysis_text = response["message"]["content"]
            
            # Attempt to parse as JSON, or wrap in structure
            report = self._parse_report_json(analysis_text, subject_id, end_type)
            return report
            
        except Exception as e:
            # Return basic structure on error
            return {
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
    
    def generate_visitor_feedback(self,
                                  conversation_history: List[Dict],
                                  end_type: EndType,
                                  relaxation_recommendation: Optional[str] = None) -> str:
        """
        Generate visitor-friendly feedback (oral style for TTS).
        
        Args:
            conversation_history: List of message dicts
            end_type: How the session ended
            relaxation_recommendation: Optional recommended relaxation type
            
        Returns:
            Oral-style feedback text suitable for TTS
        """
        llm = self._get_llm_service()
        
        formatted_history = self._format_conversation(conversation_history)
        
        # Different prompts based on end type
        if end_type == EndType.SAFETY:
            prompt = self._get_safety_feedback_prompt(formatted_history)
        else:
            prompt = VISITOR_FEEDBACK_PROMPT.format(
                conversation=formatted_history,
                end_type=end_type.value,
                relaxation_recommendation=relaxation_recommendation or "无特定推荐"
            )
        
        import ollama
        client = ollama.Client(host=OLLAMA_HOST)
        
        try:
            response = client.chat(
                model=OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                stream=False
            )
            feedback = response["message"]["content"]
            
            # Clean up the feedback for TTS
            feedback = self._clean_for_tts(feedback)
            return feedback
            
        except Exception as e:
            # Fallback feedback
            if end_type == EndType.SAFETY:
                return "我听到你说的这些了。有些事情需要更专业的人来帮你。屏幕上会显示一些电话号码，如果需要，随时可以打。"
            else:
                return "今天聊了这么久，辛苦你了。有什么想说的，咱们下次再聊。"
    
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
