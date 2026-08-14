# Legacy relaxation helper.  Live intervention approval belongs to TurnPolicy.

from services.logger import get_logger

logger = get_logger(__name__)


class RelaxationRecommendationTool:
    """Compatibility helper for old callers that need a display label.

    It never inspects model output or emits a control tag.  The live path uses
    the intervention type already carried by TurnDecision.
    """

    name = "recommend_relaxation"
    description = "Recommend relaxation training type (breathing/muscle/meditation)"

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
        # Compatibility fallback for report generation only; this is not a
        # policy decision and is not used to start media in the live path.
        if conversation_history and self.report:
            try:
                return self.report.recommend_relaxation_strategy(conversation_history)
            except Exception as e:
                logger.warning(f"Report service relaxation rec failed: {e}")

        return "呼吸"  # Default
