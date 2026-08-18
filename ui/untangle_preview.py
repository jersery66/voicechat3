"""Standalone human-playtest preview for the V2-A.2 Untangle campaign."""

from __future__ import annotations

import argparse
import sys

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QMainWindow,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from relaxation.puzzles.untangle.campaign import CampaignMode, UntangleCampaign, campaign_levels
from relaxation.puzzles.untangle.generator import Difficulty
from ui.untangle_progression_dialogs import UntangleCompletionDialog, UntangleSkipDialog
from ui.untangle_widget import UntangleWidget


class UntanglePreviewWindow(QMainWindow):
    def __init__(self, *, seed: int | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("解开线团")
        self.setMinimumSize(700, 700)
        self.campaign = UntangleCampaign(all_levels_unlocked=True, seed=seed)
        self._completion_pending = False
        self._completion_key = None
        self._completion_dialog = None
        self._skip_dialog = None
        self.title_label = QLabel("解开线团")
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #4f625f;")
        subtitle = QLabel("拖动圆点，让所有连线不再交叉。")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #766a5d; padding-bottom: 4px;")

        self.mode_label = QLabel("闯关模式")
        self.mode_label.setAlignment(Qt.AlignCenter)
        self.mode_label.setStyleSheet("color: #766a5d; font-weight: bold;")
        self.chapter_label = QLabel()
        self.chapter_label.setAlignment(Qt.AlignCenter)
        self.level_label = QLabel()
        self.level_label.setAlignment(Qt.AlignCenter)
        self.hint_label = QLabel()
        self.hint_label.setAlignment(Qt.AlignCenter)
        self.hint_label.setStyleSheet("color: #8a6d93; padding: 3px;")
        self.completion_label = QLabel()
        self.completion_label.setAlignment(Qt.AlignCenter)
        self.completion_label.setStyleSheet("color: #4f625f; font-weight: bold; padding: 5px;")
        self.completion_label.hide()

        self.level_combo = QComboBox()
        for level in campaign_levels():
            self.level_combo.addItem(f"{level.number:02d} · {level.title}", level.number)
        self.level_combo.activated.connect(self._level_selected)

        self.difficulty_combo = QComboBox()
        self.difficulty_combo.addItem("轻松 · 6 个点", Difficulty.EASY)
        self.difficulty_combo.addItem("标准 · 10 个点", Difficulty.NORMAL)
        self.difficulty_combo.addItem("挑战 · 15 个点", Difficulty.CHALLENGE)
        self.difficulty_combo.currentIndexChanged.connect(self._difficulty_changed)

        self.puzzle_widget = UntangleWidget(
            model=self.campaign.model
        )
        self.puzzle_widget.completed_signal.connect(self._on_completed)
        self.puzzle_widget.state_changed.connect(self._sync_controls)

        self.undo_button = QPushButton("撤销")
        self.reset_button = QPushButton("重新开始")
        self.new_button = QPushButton("换一局")
        self.hint_button = QPushButton("提示")
        self.skip_button = QPushButton("跳过")
        self.next_button = QPushButton("下一关")
        self.replay_button = QPushButton("重玩本关")
        self.endless_button = QPushButton("自由挑战")
        self.continue_button = QPushButton("继续挑战")
        self.campaign_button = QPushButton("开始闯关")
        self.return_button = QPushButton("返回")
        self.close_button = QPushButton("关闭")
        self.undo_button.clicked.connect(self._undo)
        self.reset_button.clicked.connect(self._reset)
        self.new_button.clicked.connect(self._new_puzzle)
        self.hint_button.clicked.connect(self._hint)
        self.skip_button.clicked.connect(self._skip)
        self.next_button.clicked.connect(self._next_level)
        self.replay_button.clicked.connect(self._replay_level)
        self.endless_button.clicked.connect(self._enter_endless)
        self.continue_button.clicked.connect(self._enter_endless)
        self.campaign_button.clicked.connect(self._enter_campaign)
        self.return_button.clicked.connect(self.close)
        self.close_button.clicked.connect(self.close)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self._undo)

        controls = QHBoxLayout()
        controls.addWidget(self.level_combo)
        controls.addWidget(self.difficulty_combo)
        controls.addWidget(self.undo_button)
        controls.addWidget(self.reset_button)
        controls.addWidget(self.new_button)
        controls.addWidget(self.hint_button)
        controls.addWidget(self.skip_button)
        navigation = QHBoxLayout()
        navigation.addWidget(self.next_button)
        navigation.addWidget(self.replay_button)
        navigation.addWidget(self.campaign_button)
        navigation.addWidget(self.endless_button)
        navigation.addWidget(self.continue_button)
        navigation.addWidget(self.return_button)
        navigation.addWidget(self.close_button)
        root = QVBoxLayout()
        root.addWidget(self.title_label)
        root.addWidget(subtitle)
        root.addWidget(self.mode_label)
        root.addWidget(self.chapter_label)
        root.addWidget(self.level_label)
        root.addWidget(self.hint_label)
        root.addWidget(self.completion_label)
        root.addLayout(controls)
        root.addLayout(navigation)
        root.addWidget(self.puzzle_widget, 1)
        central = QWidget(self)
        central.setLayout(root)
        self.setCentralWidget(central)
        self._sync_controls()
        self._refresh_campaign_ui()

    def _difficulty_changed(self, index: int) -> None:
        if self.campaign.mode is CampaignMode.ENDLESS:
            self.campaign.next_endless(difficulty=self.difficulty_combo.itemData(index))
            self._install_current_model()
            self._refresh_campaign_ui()

    def _new_puzzle(self) -> None:
        if self.campaign.mode is CampaignMode.ENDLESS:
            self.campaign.next_endless()
            self._install_current_model()
            self._refresh_campaign_ui()
        else:
            self._replay_level()

    def _undo(self) -> None:
        self.puzzle_widget.undo()
        self._sync_controls()

    def _reset(self) -> None:
        self.puzzle_widget.reset()
        self._sync_controls()

    def _on_completed(self) -> None:
        level = self.campaign.current_level
        completion_key = (
            self.campaign.mode.value,
            level.number if level is not None else self.campaign.model.seed,
        )
        if (
            self._completion_pending
            or self._completion_dialog is not None
            or completion_key == self._completion_key
        ):
            return
        self._completion_key = completion_key
        self.campaign.complete_current()
        if self.campaign.mode is CampaignMode.CAMPAIGN and self.campaign.campaign_completed:
            self.completion_label.setText("全部解开了\n你已经完成全部 15 个关卡。")
            self.continue_button.show()
        elif self.campaign.mode is CampaignMode.CAMPAIGN:
            level = self.campaign.current_level
            self.completion_label.setText(f"第 {level.number} 关完成")
            self.continue_button.hide()
        else:
            self.completion_label.setText("解开了。")
            self.continue_button.show()
        self.completion_label.show()
        self.hint_label.clear()
        self._sync_controls()
        self._refresh_campaign_ui(clear_completion=False)
        self._completion_pending = True
        QTimer.singleShot(350, self._show_completion_dialog)

    def _show_completion_dialog(self) -> None:
        if not self._completion_pending or self._completion_dialog is not None:
            return
        self._completion_pending = False
        level = self.campaign.current_level
        is_final = (
            self.campaign.mode is CampaignMode.CAMPAIGN
            and level is not None
            and level.number == 15
            and self.campaign.campaign_completed
        )
        dialog = UntangleCompletionDialog(
            level_number=level.number if level is not None else 0,
            final=is_final,
            parent=self,
        )
        self._completion_dialog = dialog
        if is_final:
            dialog.continue_button.clicked.connect(self._completion_continue)
            dialog.restart_button.clicked.connect(self._completion_restart)
            dialog.select_button.clicked.connect(self._completion_select)
            dialog.return_button.clicked.connect(self._completion_return)
        else:
            dialog.next_button.clicked.connect(self._completion_next)
            dialog.replay_button.clicked.connect(self._completion_replay)
            dialog.select_button.clicked.connect(self._completion_select)
        dialog.show()

    def _dismiss_completion_dialog(self) -> None:
        dialog = self._completion_dialog
        self._completion_dialog = None
        if dialog is not None:
            dialog.close()

    def _completion_next(self) -> None:
        self._dismiss_completion_dialog()
        self._next_level()

    def _completion_replay(self) -> None:
        self._dismiss_completion_dialog()
        self._replay_level()

    def _completion_select(self) -> None:
        self._dismiss_completion_dialog()
        self._open_level_selector()

    def _completion_continue(self) -> None:
        self._dismiss_completion_dialog()
        self._enter_endless()

    def _completion_restart(self) -> None:
        self._dismiss_completion_dialog()
        self.campaign.load_level(1)
        self._install_current_model()
        self.hint_label.clear()
        self._refresh_campaign_ui()

    def _completion_return(self) -> None:
        self._dismiss_completion_dialog()

    def _sync_controls(self) -> None:
        self.undo_button.setEnabled(self.puzzle_widget.model.can_undo)
        self.hint_button.setEnabled(not self.puzzle_widget.model.completed)

    def _refresh_campaign_ui(self, *, clear_completion: bool = True) -> None:
        if clear_completion:
            self.completion_label.hide()
            self.continue_button.hide()
        if self.campaign.mode is CampaignMode.CAMPAIGN:
            level = self.campaign.current_level
            assert level is not None
            self.mode_label.setText("闯关模式")
            chapter_number = ("一", "二", "三", "四", "五")[level.chapter_number - 1]
            self.chapter_label.setText(f"第{chapter_number}章 · {level.chapter}")
            self.level_label.setText(f"第 {level.number} / 15 关")
            self.level_combo.blockSignals(True)
            self.level_combo.setCurrentIndex(level.number - 1)
            self.level_combo.blockSignals(False)
            self.level_combo.setEnabled(True)
            self.difficulty_combo.setEnabled(False)
            self.campaign_button.setEnabled(False)
            self.endless_button.setEnabled(True)
            self.skip_button.setEnabled(not self.campaign.campaign_completed)
            self.next_button.setEnabled(
                level.number < 15
                and level.number in self.campaign.progress.completed_levels | self.campaign.progress.skipped_levels
            )
            self.replay_button.setEnabled(True)
            self.return_button.setEnabled(True)
        else:
            self.mode_label.setText("自由挑战")
            self.chapter_label.setText("随机生成 · 通关后无限模式")
            self.level_label.setText("不计分 · 不计时")
            self.level_combo.setEnabled(False)
            self.difficulty_combo.setEnabled(True)
            self.campaign_button.setEnabled(True)
            self.endless_button.setEnabled(False)
            self.skip_button.setEnabled(False)
            self.next_button.setEnabled(False)
            self.replay_button.setEnabled(True)
            self.return_button.setEnabled(True)

    def _level_selected(self, index: int) -> None:
        number = self.level_combo.itemData(index)
        if self.campaign.load_level(number):
            self._install_current_model()
            self.hint_label.clear()
            self._refresh_campaign_ui()

    def _hint(self) -> bool:
        hint = self.puzzle_widget.request_hint()
        if hint.level == 0:
            return False
        if hint.level == 1:
            self.hint_label.setText("提示：可以先看看这个点。")
        elif hint.level == 2:
            self.hint_label.setText("提示：注意这个点参与的交叉线。")
        else:
            self.hint_label.setText(f"提示：{hint.direction}")
        return True

    def _skip(self) -> bool:
        if self.campaign.mode is not CampaignMode.CAMPAIGN:
            return False
        if self._skip_dialog is not None:
            return False
        dialog = UntangleSkipDialog(parent=self)
        self._skip_dialog = dialog
        dialog.confirm_button.clicked.connect(self._confirm_skip)
        dialog.continue_button.clicked.connect(self._cancel_skip)
        dialog.show()
        return True

    def _confirm_skip(self) -> None:
        dialog = self._skip_dialog
        self._skip_dialog = None
        if dialog is not None:
            dialog.close()
        if not self.campaign.skip_current():
            return
        self._install_current_model()
        if self.campaign.campaign_completed:
            self.completion_label.setText("全部解开了\n你已经完成全部 15 个关卡。")
            self.completion_label.show()
            self.continue_button.show()
            self.hint_label.clear()
            self._refresh_campaign_ui(clear_completion=False)
            self._completion_key = (CampaignMode.CAMPAIGN.value, 15)
            self._completion_pending = True
            QTimer.singleShot(350, self._show_completion_dialog)
        else:
            self.hint_label.setText("已跳过，可以之后再回来。")
            self._refresh_campaign_ui()

    def _cancel_skip(self) -> None:
        dialog = self._skip_dialog
        self._skip_dialog = None
        if dialog is not None:
            dialog.close()

    def _next_level(self) -> bool:
        if not self.campaign.next_level():
            return False
        self._install_current_model()
        self.hint_label.clear()
        self._refresh_campaign_ui()
        return True

    def _replay_level(self) -> bool:
        if not self.campaign.replay_current():
            return False
        self._install_current_model()
        self.hint_label.clear()
        self._refresh_campaign_ui()
        return True

    def _enter_endless(self) -> bool:
        if self.campaign.mode is CampaignMode.ENDLESS:
            return False
        self.campaign.start_endless(self.difficulty_combo.currentData())
        self._install_current_model()
        self.hint_label.clear()
        self._refresh_campaign_ui()
        return True

    def _enter_campaign(self) -> bool:
        if self.campaign.mode is CampaignMode.CAMPAIGN:
            return False
        self.campaign.start_campaign()
        self._install_current_model()
        self.hint_label.clear()
        self._refresh_campaign_ui()
        return True

    def _open_level_selector(self) -> None:
        self.level_combo.setFocus()
        self.level_combo.showPopup()

    def _install_current_model(self) -> None:
        self._completion_pending = False
        self._completion_key = None
        self.puzzle_widget.set_model(self.campaign.model)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv)
    window = UntanglePreviewWindow(seed=args.seed)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
