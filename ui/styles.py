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
    background-color: #f5f5f5;
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
    font-size: 14px;
    font-weight: bold;
    color: #2d5a27;
    padding: 4px 0;
}

/* Form labels */
QLabel {
    color: #2c3e50;
    font-size: 12px;
}

/* Input fields */
QLineEdit, QComboBox, QSpinBox {
    background-color: rgba(255, 255, 255, 0.9);
    border: 1px solid #d0d0d0;
    border-radius: 6px;
    padding: 5px 8px;
    font-size: 12px;
    color: #2c3e50;
}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1px solid #2d5a27;
}

QComboBox::drop-down {
    border: none;
    width: 20px;
}

/* Buttons */
QPushButton {
    background-color: #f0f0f0;
    border: 1px solid #d0d0d0;
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 12px;
    color: #2c3e50;
}

QPushButton:hover {
    background-color: #e0e0e0;
    border: 1px solid #b0b0b0;
}

QPushButton:pressed {
    background-color: #d0d0d0;
}

/* Primary button */
#primaryButton {
    background-color: #4CAF50;
    color: white;
    border: 1px solid #388E3C;
    font-weight: bold;
}

#primaryButton:hover {
    background-color: #388E3C;
}

/* Danger button */
#dangerButton {
    background-color: #EF5350;
    color: white;
    border: 1px solid #C62828;
}

#dangerButton:hover {
    background-color: #C62828;
}

/* Record button special styling handled in widget */

/* Relaxation buttons */
#relaxButton {
    border-radius: 10px;
    padding: 8px 12px;
    font-size: 12px;
    font-weight: bold;
    color: white;
    border: none;
}

#relaxButton:hover {
    opacity: 0.85;
}

/* Status label */
#statusLabel {
    color: #1565C0;
    font-size: 11px;
    padding: 4px;
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
    background: rgba(0, 0, 0, 0.15);
    border-radius: 4px;
    min-height: 30px;
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
    font-size: 16px;
    font-weight: bold;
    color: #2d5a27;
    padding: 8px;
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
    background-color: #2d5a27;
    border-radius: 3px;
}
"""


def get_style():
    return GLOBAL_STYLE
