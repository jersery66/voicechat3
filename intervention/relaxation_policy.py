"""Session-level constraints for relaxation recommendations."""


class RelaxationPolicy:
    """The assistant may recommend only between, never inside, scale items."""

    @staticmethod
    def may_recommend(*, relaxation_used: bool, waiting_scale_answer: bool) -> bool:
        return not relaxation_used and not waiting_scale_answer
