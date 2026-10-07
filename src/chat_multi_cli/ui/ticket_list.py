"""Панель заявок слева: входящие, открытые, закрытые."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from chat_multi_cli.models import TicketBrief
from chat_multi_cli.ui import theme
from chat_multi_cli.ui.viewmodels import INCOMING_TITLE, client_name, format_time

INCOMING_ROLE = Qt.ItemDataRole.UserRole + 1
TICKET_ROLE = Qt.ItemDataRole.UserRole + 2


IndexT = QModelIndex | QPersistentModelIndex


class TicketDelegate(QStyledItemDelegate):
    """Рисует строки заявок вручную: активные ярко, закрытые приглушено."""

    def __init__(self, incoming_count: int = 0) -> None:
        super().__init__()
        self.incoming_count = incoming_count

    def set_incoming_count(self, count: int) -> None:
        self.incoming_count = count

    def sizeHint(self, option: QStyleOptionViewItem, index: IndexT) -> QSize:
        item = index.data(INCOMING_ROLE)
        height = theme.INCOMING_ROW_HEIGHT if item is not None else theme.ROW_HEIGHT
        return QSize(theme.PANEL_WIDTH - 32, height)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: IndexT) -> None:
        incoming = index.data(INCOMING_ROLE)
        if incoming is not None:
            self._paint_incoming(painter, option, index, incoming)
            return

        ticket: TicketBrief | None = index.data(TICKET_ROLE)
        if ticket is None:
            return
        self._paint_ticket(painter, option, index, ticket)

    def _paint_incoming(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: IndexT,
        count: int,
    ) -> None:
        rect = option.rect.adjusted(6, 4, -6, -4)
        selected = _is_selected(option)

        if selected:
            painter.fillRect(rect, theme.ACCENT)
            title_color = theme.WHITE
            badge_bg = QColor(255, 255, 255, 60)
            badge_text = theme.WHITE
        else:
            painter.fillRect(rect, theme.ACCENT_SOFT)
            title_color = theme.ACCENT
            badge_bg = theme.ACCENT
            badge_text = theme.WHITE

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 8, 8)

        painter.setFont(_font(11, bold=True))
        painter.setPen(title_color)
        painter.drawText(
            rect.adjusted(16, 14, -16, 0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            INCOMING_TITLE,
        )

        if count:
            badge = _badge_rect(rect, count)
            painter.setBrush(badge_bg)
            painter.drawRoundedRect(badge, badge.height() / 2, badge.height() / 2)
            painter.setFont(_font(10, bold=True))
            painter.setPen(badge_text)
            painter.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), str(count))
        else:
            painter.setFont(_font(9))
            painter.setPen(theme.WHITE if selected else theme.TEXT_MUTED)
            painter.drawText(
                rect.adjusted(16, 0, -16, -12),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                "Новых заявок нет",
            )

    def _paint_ticket(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: IndexT,
        ticket: TicketBrief,
    ) -> None:
        rect = option.rect.adjusted(6, 2, -6, -2)
        selected = _is_selected(option)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if selected:
            painter.fillRect(rect, theme.ACCENT_SOFT)
        elif ticket.is_closed:
            painter.fillRect(rect, QColor(255, 255, 255, 0))
        else:
            painter.fillRect(rect, theme.TICKET_NEW)
            painter.setPen(QPen(theme.TICKET_NEW_BORDER, 1))
            painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 8, 8)

        muted = ticket.is_closed
        title_color = theme.TEXT_DISABLED if muted else theme.TEXT
        meta_color = theme.TEXT_DISABLED if muted else theme.TEXT_MUTED

        if ticket.unread_count and not muted:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.ACCENT)
            painter.drawEllipse(rect.left() + 10, rect.top() + 18, 6, 6)

        painter.setFont(_font(10, bold=not muted))
        painter.setPen(title_color)
        subject_rect = rect.adjusted(24, 10, -70, 0)
        painter.drawText(
            subject_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
            _elide(painter, subject_rect, ticket.subject),
        )

        time = format_time(ticket.updated_at)
        painter.setFont(_font(9))
        painter.setPen(meta_color)
        meta_rect = rect.adjusted(24, 0, -70, -28)
        painter.drawText(
            meta_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom),
            _elide(painter, meta_rect, f"{client_name(ticket)} · {time}"),
        )

        painter.setFont(_font(9, bold=True))
        painter.setPen(theme.TEXT_DISABLED if muted else theme.TEXT_MUTED)
        painter.drawText(
            rect.adjusted(-64, 10, -12, 0),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop),
            time,
        )

        if muted:
            painter.setFont(_font(8))
            painter.setPen(theme.TEXT_DISABLED)
            painter.drawText(
                rect.adjusted(-64, 0, -12, -30),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom),
                "закрыта",
            )


def _badge_rect(rect: QRect, count: int) -> QRect:
    size = max(22, 14 + 8 * len(str(count)))
    return QRect(rect.right() - size - 14, rect.top() + 12, size, 22)


def _is_selected(option: QStyleOptionViewItem) -> bool:
    return bool(option.state & QStyle.StateFlag.State_Selected)


def _font(point_size: int, bold: bool = False) -> QFont:
    font = QFont()
    font.setPointSize(point_size)
    font.setBold(bold)
    return font


def _elide(painter: QPainter, rect: QRect, text: str) -> str:
    return painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, rect.width())


class TicketListPanel(QWidget):
    """Левая панель: входящие + список заявок."""

    ticket_selected = Signal(str)
    incoming_selected = Signal()
    navigation_changed = Signal(object, bool)

    def __init__(
        self,
        tickets: list[TicketBrief],
        incoming: list[TicketBrief],
    ) -> None:
        super().__init__()
        self.tickets = tickets
        self.incoming = incoming
        self._suppress_navigation = False
        self.setObjectName("ticketPanel")
        self.setFixedWidth(theme.PANEL_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("panelHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 16, 14, 12)

        title = QLabel("Заявки")
        title.setObjectName("panelTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        self.search = QLineEdit()
        self.search.setObjectName("searchInput")
        self.search.setPlaceholderText("Поиск")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.rebuild)
        header_layout.addWidget(self.search)
        layout.addWidget(header)

        self.list = QListWidget()
        self.list.setObjectName("ticketList")
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.delegate = TicketDelegate(incoming_count=len(incoming))
        self.list.setItemDelegate(self.delegate)
        self.list.currentItemChanged.connect(self._on_current_changed)
        layout.addWidget(self.list, 1)

        self.footer = QLabel()
        self.footer.setObjectName("panelFooter")
        footer_layout = QVBoxLayout()
        footer_layout.setContentsMargins(18, 10, 18, 14)
        footer_layout.addWidget(self.footer)
        footer_host = QWidget()
        footer_host.setLayout(footer_layout)
        layout.addWidget(footer_host)

        self.rebuild()

    def rebuild(self) -> None:
        selected_id = self.current_ticket_id()
        self.list.blockSignals(True)
        self.list.clear()

        incoming_item = QListWidgetItem()
        incoming_item.setData(INCOMING_ROLE, len(self.incoming))
        incoming_item.setToolTip("Заявки, которые ещё никто не принял")
        self.list.addItem(incoming_item)

        ordered = self._sorted()
        for ticket in ordered:
            item = QListWidgetItem()
            item.setData(TICKET_ROLE, ticket)
            item.setToolTip(f"{ticket.subject}\n{client_name(ticket)}")
            self.list.addItem(item)

        self.list.blockSignals(False)
        self.delegate.set_incoming_count(len(self.incoming))
        self.list.viewport().update()

        active = len([t for t in ordered if not t.is_closed])
        self.footer.setText(f"Активных: {active}   Закрытых: {len(ordered) - active}")

        self._restore_selection(selected_id)
        if self._suppress_navigation:
            return
        self.navigation_changed.emit(self.current_ticket_id(), self.is_incoming_selected())

    def set_data(self, tickets: list[TicketBrief], incoming: list[TicketBrief]) -> None:
        """Обновить оба списка и перерисовать панель."""
        self.tickets = tickets
        self.incoming = incoming
        self.rebuild()

    def _sorted(self) -> list[TicketBrief]:
        query = self.search.text().strip().casefold()
        pool = self.tickets
        if query:
            pool = [
                t
                for t in pool
                if query in t.subject.casefold()
                or query in client_name(t).casefold()
                or query in t.id.casefold()
            ]
        return [t for t in pool if not t.is_closed] + [t for t in pool if t.is_closed]

    def _restore_selection(self, ticket_id: str | None) -> None:
        """Восстанавливаем выделение молча: программная установка не должна
        приводить к «прыжку» правой панели."""
        if ticket_id:
            for row in range(self.list.count()):
                ticket = self.list.item(row).data(TICKET_ROLE)
                if ticket is not None and ticket.id == ticket_id:
                    self._set_current_row(row)
                    return
        if self.list.currentRow() < 0:
            self._set_current_row(0)

    def _set_current_row(self, row: int) -> None:
        self._suppress_navigation = True
        self.list.setCurrentRow(row)
        self._suppress_navigation = False

    def current_ticket_id(self) -> str | None:
        item = self.list.currentItem()
        if item is None:
            return None
        ticket = item.data(TICKET_ROLE)
        return ticket.id if ticket else None

    def select_incoming(self) -> None:
        self.list.setCurrentRow(0)

    def select_ticket(self, ticket_id: str) -> None:
        """Выделяет заявку в списке (программная навигация, не «прыжок» панели)."""
        for row in range(self.list.count()):
            ticket = self.list.item(row).data(TICKET_ROLE)
            if ticket is not None and ticket.id == ticket_id:
                self._set_current_row(row)
                return

    def is_incoming_selected(self) -> bool:
        item = self.list.currentItem()
        return item is not None and item.data(INCOMING_ROLE) is not None

    def _on_current_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is None or self._suppress_navigation:
            return
        if current.data(INCOMING_ROLE) is not None:
            self.incoming_selected.emit()
            self.navigation_changed.emit(None, True)
            return
        ticket = current.data(TICKET_ROLE)
        if ticket is not None:
            self.ticket_selected.emit(ticket.id)
            self.navigation_changed.emit(ticket.id, False)

    def remove_ticket(self, ticket_id: str) -> None:
        self.tickets = [t for t in self.tickets if t.id != ticket_id]
        self.rebuild()
