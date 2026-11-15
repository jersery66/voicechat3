# QSS Styles - Chinese traditional color scheme

# Color palette (Chinese ink wash painting inspired)
COLORS = {
    "primary": "#2d5a27",       # Green (mountain green)
    "secondary": "#8b7355",     # Brown (wood grain)
    "accent": "#c0392b",        # Red (vermillion)
    "text": "#2c3e50",          # Dark ink
    "text_light": "#7f8c8d",    # Light gray text
    "bg_frost": "rgba(255, 255, 255, 0.85)",
    "bg_frost_chat": "rgba(255, 255, 255, 0.78)",
    "user_bubble": "#DCF8C6",
    "ai_bubble": "rgba(255, 255, 255, 0.95)",
}

GLOBAL_STYLE = """
* {
    font-family: "Microsoft YaHei", "SimHei", sans-serif;
}

QMainWindow {
    background-color: #0B1220;
}

/* Left control panel */
#controlPanel {
    background-color: rgba(255, 255, 255, 0.85);
    border-radius: 16px;
    border: 1px solid rgba(255, 255, 255, 0.3);
}

/* Right chat panel */
#chatPanel {
    background-color: rgba(255, 255, 255, 0.78);
    border-radius: 16px;
    border: 1px solid rgba(255, 255, 255, 0.3);
}

/* Section titles */
#sectionTitle {
    font-size: 13px;
    font-weight: 700;
    color: #2F6B4F;
    padding: 6px 0 2px 0;
}

/* Form labels */
QLabel {
    color: #3D4A52;
    font-size: 12px;
}

/* Input fields */
QLineEdit, QComboBox, QSpinBox {
    background-color: rgba(255, 255, 255, 0.96);
    border: 1px solid rgba(30, 42, 50, 0.14);
    border-radius: 8px;
    padding: 7px 10px;
    font-size: 12px;
    color: #1E2A32;
    selection-background-color: rgba(47, 107, 79, 0.25);
}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1px solid #2F6B4F;
    background-color: #FFFFFF;
}

QComboBox::drop-down {
    border: none;
    width: 20px;
}

/* Buttons */
QPushButton {
    background-color: rgba(255, 255, 255, 0.78);
    border: 1px solid rgba(30, 42, 50, 0.12);
    border-radius: 9px;
    padding: 7px 14px;
    font-size: 12px;
    color: #1E2A32;
}

QPushButton:hover {
    background-color: #FFFFFF;
    border: 1px solid rgba(47, 107, 79, 0.35);
}

QPushButton:pressed {
    background-color: rgba(47, 107, 79, 0.12);
}

QPushButton:disabled {
    background-color: rgba(255, 255, 255, 0.45);
    color: rgba(30, 42, 50, 0.38);
}

/* Primary button */
#primaryButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2F6B4F, stop:1 #3C8163);
    color: #FFFFFF;
    border: none;
    border-radius: 10px;
    padding: 10px 16px;
    font-size: 13px;
    font-weight: 700;
}

#primaryButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #275C43, stop:1 #337257);
}

#primaryButton:pressed {
    background: #23503B;
}

/* Danger button */
#dangerButton {
    background-color: #C0503C;
    color: #FFFFFF;
    border: none;
    border-radius: 10px;
    padding: 8px 14px;
}

#dangerButton:hover {
    background-color: #A94432;
}

/* Record button special styling handled in widget */

/* Relaxation buttons */
#relaxButton {
    background-color: rgba(47, 107, 79, 0.10);
    border: 1px solid rgba(47, 107, 79, 0.18);
    border-radius: 10px;
    padding: 9px 12px;
    font-size: 12px;
    font-weight: 600;
    color: #2F6B4F;
}

#relaxButton:hover {
    background-color: rgba(47, 107, 79, 0.18);
    border: 1px solid rgba(47, 107, 79, 0.32);
}

#relaxButton:pressed {
    background-color: rgba(47, 107, 79, 0.26);
}

/* Status label */
#statusLabel {
    color: #2F6B4F;
    font-size: 11px;
    padding: 6px 10px;
    background-color: rgba(47, 107, 79, 0.08);
    border-radius: 8px;
}

/* Chat area */
#chatScrollArea {
    background: transparent;
    border: none;
}

#chatScrollArea QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 0;
}

#chatScrollArea QScrollBar::handle:vertical {
    background: rgba(30, 42, 50, 0.18);
    border-radius: 4px;
    min-height: 30px;
}

#chatScrollArea QScrollBar::handle:vertical:hover {
    background: rgba(30, 42, 50, 0.30);
}

#chatScrollArea QScrollBar::add-line:vertical,
#chatScrollArea QScrollBar::sub-line:vertical {
    height: 0;
}

#chatScrollArea QScrollBar::add-page:vertical,
#chatScrollArea QScrollBar::sub-page:vertical {
    background: transparent;
}

/* Chat title */
#chatTitle {
    font-size: 17px;
    font-weight: 700;
    color: #1E2A32;
    padding: 6px 8px;
}

/* Loading screen */
#loadingCard {
    background-color: rgba(245, 245, 247, 0.95);
    border-radius: 20px;
    border: 1px solid rgba(208, 208, 213, 0.5);
}

#loadingTitle {
    font-size: 22px;
    font-weight: bold;
    color: #2c3e50;
}

#loadingSubtitle {
    font-size: 11px;
    color: #7f8c8d;
}

#loadingStep {
    font-size: 12px;
    color: #34495e;
}

#loadingStatus {
    font-size: 10px;
    color: #95a5a6;
}

QProgressBar {
    border: none;
    border-radius: 3px;
    background-color: #e0e0e5;
    max-height: 6px;
}

QProgressBar::chunk {
    background-color: #2F6B4F;
    border-radius: 3px;
}
"""


GLOBAL_STYLE_DARK = """
* {
    font-family: "Microsoft YaHei", "SimHei", sans-serif;
}

QMainWindow {
    background-color: #1a1a2e;
}

#controlPanel {
    background-color: rgba(30, 30, 60, 0.85);
    border-radius: 16px;
    border: 1px solid rgba(255, 255, 255, 0.08);
}

#chatPanel {
    background-color: rgba(30, 30, 60, 0.78);
    border-radius: 16px;
    border: 1px solid rgba(255, 255, 255, 0.08);
}

#sectionTitle {
    font-size: 14px;
    font-weight: bold;
    color: #81c784;
    padding: 4px 0;
}

QLabel {
    color: #e0e0e0;
    font-size: 12px;
}

QLineEdit, QComboBox, QSpinBox {
    background-color: rgba(40, 40, 70, 0.9);
    border: 1px solid #3a3a5c;
    border-radius: 6px;
    padding: 5px 8px;
    font-size: 12px;
    color: #e0e0e0;
}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1px solid #81c784;
}

QComboBox::drop-down { border: none; width: 20px; }

QPushButton {
    background-color: #2a2a3e;
    border: 1px solid #3a3a5c;
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 12px;
    color: #e0e0e0;
}

QPushButton:hover {
    background-color: #3a3a5c;
    border: 1px solid #5a5a7c;
}

QPushButton:pressed { background-color: #2a2a4e; }

#primaryButton {
    background-color: #2d5a27;
    color: white;
    border: 1px solid #388E3C;
    font-weight: bold;
}
#primaryButton:hover { background-color: #388E3C; }

#dangerButton {
    background-color: #B71C1C;
    color: white;
    border: 1px solid #D32F2F;
}
#dangerButton:hover { background-color: #D32F2F; }

#relaxButton {
    border-radius: 10px;
    padding: 8px 12px;
    font-size: 12px;
    font-weight: bold;
    color: white;
    border: none;
}
#relaxButton:hover { opacity: 0.85; }

#statusLabel { color: #64B5F6; font-size: 11px; padding: 4px; }

#chatScrollArea { background: transparent; border: none; }
#chatScrollArea QScrollBar:vertical {
    background: transparent; width: 8px; margin: 0;
}
#chatScrollArea QScrollBar::handle:vertical {
    background: rgba(255, 255, 255, 0.1);
    border-radius: 4px;
    min-height: 30px;
}
#chatScrollArea QScrollBar::add-line:vertical,
#chatScrollArea QScrollBar::sub-line:vertical { height: 0; }
#chatScrollArea QScrollBar::add-page:vertical,
#chatScrollArea QScrollBar::sub-page:vertical { background: transparent; }

#chatTitle { font-size: 16px; font-weight: bold; color: #81c784; padding: 8px; }

#loadingCard {
    background-color: rgba(20, 20, 40, 0.95);
    border-radius: 20px;
    border: 1px solid rgba(100, 100, 120, 0.3);
}
#loadingTitle { font-size: 22px; font-weight: bold; color: #e0e0e0; }
#loadingSubtitle { font-size: 11px; color: #90a4ae; }
#loadingStep { font-size: 12px; color: #b0bec5; }
#loadingStatus { font-size: 10px; color: #78909c; }

QProgressBar {
    border: none;
    border-radius: 3px;
    background-color: #2a2a3e;
    max-height: 6px;
}
QProgressBar::chunk {
    background-color: #81c784;
    border-radius: 3px;
}
"""


def get_style(dark: bool = False):
    return GLOBAL_STYLE_DARK if dark else GLOBAL_STYLE
