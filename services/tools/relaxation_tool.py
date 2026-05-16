# Relaxation Recommendation Tool - Uses 3B Agent for relaxation type inference

from services.logger import get_logger

logger = get_logger(__name__)


class RelaxationRecommendationTool:
    """Use 3B agent to recommend relaxation type based on conversation context."""

    name = "recommend_relaxation"
    description = "Recommend relaxation training type (breathing/muscle/meditation)"

    TAG_MAP = {
        "[REC_BREATHING]": "呼吸",
        "[REC_MUSCLE]": "肌肉",
        "[REC_MEDITATION]": "冥想",
    }

    def __init__(self, agent_service, report_service):
        self.agent = agent_service
        self.report = report_service

    def execute(self, conversation_history=None, spoken_text=None) -> str:
        """
        Recommend relaxation type. Tries 3B agent first, falls back to report_service.

        Args:
            conversation_history: List of conversation messages
            spoken_text: AI spoken text to analyze for relaxation cues

        Returns:
            "呼吸", "肌肉", or "冥想" (defaults to "呼吸")
        """
        # Try 3B agent's infer_relaxation_tag on spoken text
        if spoken_text and self.agent:
            try:
                tag = self.agent.infer_relaxation_tag(spoken_text)
                if tag:
                    result = self.TAG_MAP.get(tag)
                    if result:
                        return result
            except Exception as e:
                logger.warning(f"Agent relaxation inference failed: {e}")

        # Fallback: use report_service's conversation-based recommendation
        if conversation_history and self.report:
            try:
                return self.report.recommend_relaxation_strategy(conversation_history)
            except Exception as e:
                logger.warning(f"Report service relaxation rec failed: {e}")

        return "呼吸"  # Default
