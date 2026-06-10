"""Shared UI colors and helpers."""
import platform

from PyQt6.QtGui import QColor


class C:
    # Core surfaces (aligned with Siri overlay)
    BG        = "#0e0e10"
    SURFACE   = "#161618"
    SURFACE2  = "#1e1e22"
    PANEL     = "#161618"
    PANEL2    = "#222226"
    BORDER    = "rgba(255, 255, 255, 0.08)"
    BORDER_B  = "rgba(255, 255, 255, 0.14)"
    BORDER_A  = "rgba(255, 255, 255, 0.06)"
    # Text
    PRI       = "#f0f0f2"
    PRI_DIM   = "#a8a8b0"
    PRI_GHO   = "#6a6a72"
    ACC       = "#e8e8ec"
    ACC2      = "#b8b8c0"
    GREEN     = "#6ee7a0"
    GREEN_D   = "#3d9a6a"
    RED       = "#f87171"
    MUTED_C   = "#888890"
    TEXT      = "#f0f0f2"
    TEXT_DIM  = "#a8a8b0"
    TEXT_MED  = "#7a7a84"
    WHITE     = "#ffffff"
    DARK      = "#161618"
    BAR_BG    = "#1e1e22"
    # Accents
    USER      = "#ffffff"
    AI        = "#d4d4dc"
    LINK      = "#3fd0c0"
    # Legacy aliases
    ACCENT    = LINK


RADIUS_L = 24
RADIUS_M = 14
RADIUS_S = 10

# Siri orb disc + expanded panel (same glass look)
PANEL_GLASS_BG = "rgba(28, 28, 32, 0.60)"
PANEL_GLASS_BORDER = "rgba(255, 255, 255, 0.30)"
PANEL_GLASS_INNER = "rgba(22, 22, 26, 0.55)"

_UI_FONT = (
    ".AppleSystemUIFont"
    if platform.system() == "Darwin"
    else "Segoe UI"
)
_MONO_FONT = "Menlo" if platform.system() == "Darwin" else "Consolas"


def ui_font(size: int = 13, bold: bool = False) -> str:
    w = "bold" if bold else "normal"
    return f'font-family: "{_UI_FONT}"; font-size: {size}pt; font-weight: {w};'


def mono_font(size: int = 12) -> str:
    return f'font-family: "{_MONO_FONT}"; font-size: {size}pt;'


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h)
    c.setAlpha(a)
    return c


def expanded_shell_stylesheet() -> str:
    """Outer window — transparent so the glass card shows through."""
    return """
        QMainWindow#ariaExpandedShell,
        QWidget#ariaExpandedShell {
            background: transparent;
        }
        QLabel {
            background: transparent;
        }
    """


def panel_card_stylesheet() -> str:
    """Matches the orb disc: dark glass + soft white rim + round corners."""
    return f"""
        QFrame#ariaPanelCard {{
            background: {PANEL_GLASS_BG};
            border: 1px solid {PANEL_GLASS_BORDER};
            border-radius: {RADIUS_L}px;
        }}
    """


def panel_card_compact_stylesheet() -> str:
    return """
        QFrame#ariaPanelCard {
            background: transparent;
            border: none;
            border-radius: 0px;
        }
    """


def log_widget_stylesheet() -> str:
    return f"""
        QTextEdit {{
            background: {PANEL_GLASS_INNER};
            color: {C.TEXT};
            border: 1px solid {C.BORDER};
            border-radius: {RADIUS_M}px;
            padding: 12px 14px;
            selection-background-color: #3a3a44;
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 6px;
            margin: 4px 2px;
        }}
        QScrollBar::handle:vertical {{
            background: #3a3a44;
            border-radius: 3px;
            min-height: 24px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
    """


def command_input_stylesheet() -> str:
    return f"""
        QLineEdit {{
            background: {PANEL_GLASS_INNER};
            color: {C.TEXT};
            border: 1px solid {C.BORDER};
            border-radius: {RADIUS_M}px;
            padding: 8px 14px;
        }}
        QLineEdit:focus {{
            border: 1px solid #22a89c;
            background: rgba(30, 30, 36, 0.75);
        }}
    """


def command_input_drag_stylesheet() -> str:
    return f"""
        QLineEdit {{
            background: #222228;
            color: {C.TEXT};
            border: 1px solid {C.LINK};
            border-radius: {RADIUS_M}px;
            padding: 8px 14px;
        }}
    """


def icon_button_stylesheet() -> str:
    return f"""
        QPushButton {{
            background: {C.SURFACE2};
            color: {C.TEXT_DIM};
            border: 1px solid {C.BORDER};
            border-radius: {RADIUS_M}px;
            padding: 6px 12px;
            {ui_font(11)}
        }}
        QPushButton:hover {{
            background: #2a2a30;
            color: {C.TEXT};
            border-color: {C.BORDER_B};
        }}
    """


def primary_button_stylesheet() -> str:
    return f"""
        QPushButton {{
            background: #22a89c;
            color: #07201c;
            border: none;
            border-radius: {RADIUS_M}px;
            {ui_font(12, bold=True)}
        }}
        QPushButton:hover {{ background: #6fe3d6; }}
        QPushButton:pressed {{ background: #178a80; color: #07201c; }}
    """


def progress_bar_stylesheet() -> str:
    return f"""
        QProgressBar {{
            background: {C.SURFACE};
            border: none;
            border-radius: 2px;
        }}
        QProgressBar::chunk {{
            background: {C.LINK};
            border-radius: 2px;
        }}
    """


def embed_panel_stylesheet() -> str:
    return f"""
        QLabel {{
            background: {C.SURFACE2};
            border: 1px solid {C.BORDER};
            border-radius: {RADIUS_M}px;
            color: {C.TEXT_DIM};
        }}
    """
