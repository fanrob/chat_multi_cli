"""Хардкодные данные для UI-эскиза. Заменяется вызовами ApiClient и БД."""

from __future__ import annotations

from dataclasses import dataclass, field

INCOMING_TITLE = "Входящие заявки"


@dataclass(slots=True)
class MockAttachment:
    name: str
    kind: str = "photo"
    size_kb: int = 0


@dataclass(slots=True)
class MockMessage:
    sender: str
    text: str
    time: str
    sender_name: str = ""
    delivery: str = ""
    attachments: list[MockAttachment] = field(default_factory=list)


@dataclass(slots=True)
class MockTicket:
    id: str
    subject: str
    client_name: str
    status: str
    workshop: str
    updated: str
    unread: int = 0
    messages: list[MockMessage] = field(default_factory=list)

    @property
    def is_closed(self) -> bool:
        return self.status == "closed"

    @property
    def status_label(self) -> str:
        return {"new": "Не принята", "in_progress": "В работе", "closed": "Закрыта"}.get(
            self.status, self.status
        )

    @property
    def time_label(self) -> str:
        return self.updated


def make_messages() -> list[MockMessage]:
    return [
        MockMessage(
            sender="client",
            text="Здравствуйте, левый передний баккер порвался, горит на асфальте.",
            time="12:31",
            sender_name="Иван Петров",
        ),
        MockMessage(
            sender="client",
            text="Машина на стоянке, подъезд к сервису свободный.",
            time="12:32",
            sender_name="Иван Петров",
        ),
        MockMessage(
            sender="master",
            text="Добрый день! Принял заявку, выезжаю через 15 минут.",
            time="12:33",
            delivery="read",
        ),
        MockMessage(
            sender="master",
            text="Возьмите с собой документы на автомобиль.",
            time="12:33",
            delivery="sent",
            attachments=[MockAttachment("bakk_01.jpg", size_kb=284)],
        ),
        MockMessage(
            sender="client",
            text="Документы взял, VIN кину на всякий случай.",
            time="12:40",
            sender_name="Иван Петров",
        ),
        MockMessage(
            sender="system",
            text="К заявке подключился мастер Пётр Кузнецов",
            time="12:41",
        ),
    ]


MOCK_INCOMING: list[MockTicket] = [
    MockTicket(
        id="t_in_1",
        subject="Замена масла + фильтры",
        client_name="Ольга С.",
        status="new",
        workshop="Слесарный цех",
        updated="12:44",
        unread=2,
        messages=[
            MockMessage(
                sender="client",
                text="Нужно масло 5w-40 и фильтр воздуха.",
                time="12:44",
                sender_name="Ольга С.",
            ),
            MockMessage(
                sender="client",
                text="Машина на территории, подъезд свободный.",
                time="12:44",
                sender_name="Ольга С.",
            ),
        ],
    ),
    MockTicket(
        id="t_in_2",
        subject="Скол на переднем бампере",
        client_name="Дмитрий К.",
        status="new",
        workshop="Кузовной цех",
        updated="12:41",
        unread=1,
        messages=[
            MockMessage(
                sender="client",
                text="Парковка рядом с ТЦ, бампер можно осмотреть при мне.",
                time="12:41",
                sender_name="Дмитрий К.",
                attachments=[MockAttachment("bumper.jpg", size_kb=412)],
            )
        ],
    ),
    MockTicket(
        id="t_in_3",
        subject="Не заводится, толкается",
        client_name="Алексей Р.",
        status="new",
        workshop="Все цеха",
        updated="12:12",
        unread=1,
        messages=[
            MockMessage(
                sender="client",
                text="Стою на заправке у трассы, горит чек.",
                time="12:12",
                sender_name="Алексей Р.",
            )
        ],
    ),
    MockTicket(
        id="t_in_4",
        subject="Замена тормозных колодок",
        client_name="Мария В.",
        status="new",
        workshop="Слесарный цех",
        updated="11:58",
        unread=0,
        messages=[
            MockMessage(
                sender="client",
                text="Передние, задние пока живые.",
                time="11:58",
                sender_name="Мария В.",
            )
        ],
    ),
]

MOCK_TICKETS: list[MockTicket] = [
    MockTicket(
        id="t_9f2c",
        subject="Левый баккер, горит на асфальте",
        client_name="Иван Петров",
        status="in_progress",
        workshop="Кузовной цех",
        updated="12:41",
        unread=2,
        messages=make_messages(),
    ),
    MockTicket(
        id="t_3a71",
        subject="Стук в передней подвеске",
        client_name="Сергей Л.",
        status="in_progress",
        workshop="Слесарный цех",
        updated="12:25",
        unread=1,
        messages=[
            MockMessage(
                sender="client",
                text="Стучит на неровностях, скорость 40.",
                time="12:20",
                sender_name="Сергей Л.",
            ),
            MockMessage(
                sender="master", text="Понял, посмотрю рычаги.", time="12:25", delivery="read"
            ),
        ],
    ),
    MockTicket(
        id="t_5b04",
        subject="Ржавый арки, обработка",
        client_name="Никита Ж.",
        status="in_progress",
        workshop="Кузовной цех",
        updated="11:47",
        unread=0,
        messages=[
            MockMessage(
                sender="client",
                text="Арки и пороги, машина 2014 года.",
                time="11:40",
                sender_name="Никита Ж.",
            ),
            MockMessage(
                sender="master",
                text="Согласовано, заберу в четверг.",
                time="11:47",
                delivery="read",
            ),
        ],
    ),
    MockTicket(
        id="t_1c88",
        subject="Компрессор не качает",
        client_name="Павел Т.",
        status="closed",
        workshop="Слесарный цех",
        updated="вчера",
        unread=0,
        messages=[
            MockMessage(
                sender="client",
                text="Компрессор не набирает давление.",
                time="16:02",
                sender_name="Павел Т.",
            ),
            MockMessage(
                sender="master",
                text="Заменил клапан, проверил — держит 8 бар.",
                time="16:40",
                delivery="read",
            ),
            MockMessage(
                sender="system", text="Заявка закрыта мастером Фёдор Семёнов", time="16:41"
            ),
        ],
    ),
    MockTicket(
        id="t_7d22",
        subject="Замена масла по регламенту",
        client_name="Анна Б.",
        status="closed",
        workshop="Слесарный цех",
        updated="вчера",
        unread=0,
        messages=[
            MockMessage(
                sender="client",
                text="Плановое ТО, 90 тыс. км.",
                time="11:15",
                sender_name="Анна Б.",
            ),
            MockMessage(
                sender="system", text="Заявка закрыта мастером Пётр Кузнецов", time="11:50"
            ),
        ],
    ),
]
