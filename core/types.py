# core.types — shared domain enums.
#
# EndType used to live in services/report_service.py; it is now defined here
# so that pure-logic modules (core.session_fsm, core.tags helpers) can use it
# without importing the service layer. services.report_service re-exports it
# for backward compatibility.

from enum import Enum


class EndType(Enum):
    """Session end types"""
    NONE = "NONE"                      # 未结束
    GOAL_ACHIEVED = "GOAL_ACHIEVED"    # 目标达成
    TIME_LIMIT = "TIME_LIMIT"          # 时间/轮次限制
    SAFETY = "SAFETY"                  # 安全边界（危机干预）
    INVALID = "INVALID"                # 无效对话
    QUIT = "QUIT"                      # 用户主动退出
