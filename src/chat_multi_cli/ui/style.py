"""Таблица стилей Qt Style Sheets."""

from __future__ import annotations

QSS = """
* { font-family: "Segoe UI", "Inter", sans-serif; font-size: 13px; }

QWidget#ticketPanel, QWidget#incomingBackground { background: #f4f5f7; }
QWidget#chatBackground, QWidget#incomingBackground { background: #f4f5f7; }
QWidget#membersPanel { background: #ffffff; border-left: 1px solid #e3e6ea; }

QWidget#panelHeader, QWidget#ticketHeader {
    background: #ffffff;
    border-bottom: 1px solid #e3e6ea;
}
QLabel#panelTitle { font-size: 15px; font-weight: 600; color: #1c2128; }
QLabel#chatSubject { font-size: 15px; font-weight: 600; color: #1c2128; }
QLabel#chatMeta { color: #7a838f; font-size: 12px; }
QLabel#statusDot { color: #2e9e5b; font-size: 12px; }

QLabel#placeholderTitle { font-size: 15px; font-weight: 600; color: #7a838f; }
QLabel#placeholderHint { color: #9aa1ab; font-size: 12px; }

QLineEdit#searchInput {
    background: #f4f5f7;
    border: 1px solid #e3e6ea;
    border-radius: 8px;
    padding: 6px 10px;
    min-width: 120px;
}
QLineEdit#searchInput:focus { border-color: #2f6fed; background: #ffffff; }

QListWidget#ticketList {
    background: #f4f5f7;
    border: none;
    padding: 8px 6px;
    outline: none;
}

QLabel#bubbleText { color: #1c2128; font-size: 13px; }
QLabel#bubbleAuthor { color: #2f6fed; font-size: 11px; font-weight: 600; }
QLabel#bubbleMeta { color: #9aa1ab; font-size: 10px; }
QLabel#bubbleMetaRead { color: #2f6fed; font-size: 10px; }

QFrame#bubbleMaster {
    background: #dbe8ff;
    border: none;
    border-radius: 12px;
}
QFrame#bubbleClient {
    background: #ffffff;
    border: 1px solid #e3e6ea;
    border-radius: 12px;
}
QLabel#systemBubble {
    color: #9aa1ab;
    font-size: 11px;
    background: #eceff3;
    border-radius: 10px;
    padding: 4px 12px;
}

QFrame#attachmentTile {
    background: rgba(255, 255, 255, 0.65);
    border: 1px solid #dfe3e8;
    border-radius: 8px;
}
QLabel#attachmentIcon { font-size: 20px; }
QLabel#attachmentName { color: #1c2128; font-size: 11px; }
QLabel#attachmentSize { color: #9aa1ab; font-size: 10px; }

QFrame#composer, QFrame#actionBar {
    background: #ffffff;
    border-top: 1px solid #e3e6ea;
}
QFrame#actionBar { border-top: none; padding-top: 2px; }

QTextEdit#composerInput {
    background: #f4f5f7;
    border: 1px solid #e3e6ea;
    border-radius: 10px;
    padding: 4px 8px;
    selection-background-color: #2f6fed;
    selection-color: #ffffff;
}
QTextEdit#composerInput:focus { border-color: #2f6fed; background: #ffffff; }
QTextEdit#composerInput:disabled { color: #9aa1ab; background: #fafbfc; }

QPushButton {
    background: #ffffff;
    border: 1px solid #dfe3e8;
    border-radius: 8px;
    padding: 7px 14px;
    color: #1c2128;
}
QPushButton:hover { background: #f0f2f5; }
QPushButton:disabled { color: #b3b9c2; background: #fafbfc; }

QPushButton#primaryButton {
    background: #2f6fed;
    border: none;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#primaryButton:hover { background: #2560d8; }

QPushButton#dangerButton {
    background: #fdeeee;
    border: 1px solid #f3c9c9;
    color: #d64545;
    font-weight: 600;
}
QPushButton#dangerButton:hover { background: #fadcdc; }

QPushButton#secondaryButton { color: #2f6fed; border-color: #c7d9f8; }
QPushButton#secondaryButton:hover { background: #eaf1fe; }

QPushButton#linkButton {
    background: transparent;
    border: none;
    color: #2f6fed;
    padding: 7px 6px;
}
QPushButton#linkButton:hover { background: transparent; color: #2560d8; }

QPushButton#iconButton {
    font-size: 16px;
    padding: 0px;
    min-width: 40px;
}
QPushButton#iconButtonSmall {
    font-size: 10px;
    padding: 0px;
    border: none;
    background: transparent;
    color: #9aa1ab;
}
QPushButton#iconButtonSmall:hover { color: #d64545; background: #fdeeee; }

QPushButton#attachmentChip {
    background: #eaf1fe;
    border: none;
    color: #2f6fed;
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
}

QFrame#incomingCard {
    background: #ffffff;
    border: 1px solid #e3e6ea;
    border-radius: 12px;
}
QFrame#incomingCard:hover { border-color: #c7d9f8; }
QLabel#cardSubject { font-size: 14px; font-weight: 600; color: #1c2128; }
QLabel#cardMeta { color: #7a838f; font-size: 12px; }
QLabel#cardPreview { color: #4a515b; font-size: 12px; }
QLabel#cardTime { color: #9aa1ab; font-size: 11px; }
QLabel#cardUnread {
    color: #2f6fed;
    font-size: 11px;
    font-weight: 600;
    background: #eaf1fe;
    border-radius: 9px;
    padding: 2px 10px;
}

QLabel#membersTitle { font-size: 13px; font-weight: 600; color: #1c2128; }
QLabel#memberRow { color: #4a515b; font-size: 12px; }

QScrollArea#chatScroll, QScrollArea#incomingScroll { background: #f4f5f7; border: none; }

QStatusBar#appStatusBar { color: #7a838f; font-size: 11px; background: #ffffff; }
QStatusBar#appStatusBar::item { border: none; }

QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #cbd1d9;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: #aeb6c0; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

QSplitter::handle { background: #e3e6ea; }
QDialog { background: #ffffff; }
QLabel#dialogTitle { font-size: 14px; font-weight: 600; color: #1c2128; }
QListWidget { border: 1px solid #e3e6ea; border-radius: 8px; outline: none; }
QListWidget::item { padding: 8px 10px; border-radius: 6px; }
QListWidget::item:selected { background: #eaf1fe; color: #1c2128; }
QListWidget::item:hover { background: #f4f5f7; }
"""
