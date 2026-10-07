"""Фейковый сервер для тестов: httpx.MockTransport с живым состоянием.

Повторяет контракт сервера: тесты должны ломаться, если клиент начнёт ожидать
то, чего реальный сервер не вернёт. Состояние — обычные атрибуты FakeServer;
тесты правят их напрямую: добавили клиенту сообщение — окно обязано увидеть
его при следующей перерисовке.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any

import httpx

from tests.conftest import master_payload, message_payload, ticket_payload

PREFIX = "/v1"

#: Имя файла внутри multipart-тела POST /attachments.
_re_filename = re.compile(rb'filename="([^"]*)"')


def _error(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


class FakeServer:
    """Мини-копия API сервера. Потокобезопасен (тесты и клиент могут звать
    его из разных потоков после ctx.start_sync())."""

    def __init__(self) -> None:
        self.master_id = "m1"
        self.full_name = "Иванов Иван"
        self.workshop_id = "body"
        self.workshop_name = "Кузовной"
        self.cursor = 0
        self.instances: set[str] = set()
        self.tickets: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, list[dict[str, Any]]] = {}
        self.masters: list[dict[str, Any]] = []
        self.workshops: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.uploaded: dict[str, dict[str, Any]] = {}
        self.seq_by_ticket: dict[str, int] = {}
        self._msg_id = 0
        self._att_id = 0
        self._event_seq = 0
        self._lock = threading.RLock()

    # ---------- Состояние: тесты правят отсюда ----------

    def add_ticket(self, payload: dict[str, Any]) -> None:
        self.tickets[str(payload["id"])] = payload

    def add_message(self, ticket_id: str, payload: dict[str, Any]) -> None:
        """Добавляет сообщение от клиента/мастера, как прислал бы сервер."""
        entry = dict(payload)
        entry["ticket_id"] = ticket_id
        self.messages.setdefault(ticket_id, []).append(entry)
        seq = int(entry["seq"])
        self.seq_by_ticket[ticket_id] = max(self.seq_by_ticket.get(ticket_id, 0), seq)
        ticket = self.tickets.get(ticket_id)
        if ticket is not None:
            ticket["last_message"] = {
                "seq": int(entry["seq"]),
                "sender": str(entry["sender"]),
                "preview": str(entry["text"]),
            }
            ticket["updated_at"] = str(entry["created_at"])

    def post_event(self, event: dict[str, Any]) -> None:
        """Событие ленты для GET /sync (используется тестами с start_sync())."""
        with self._lock:
            self._event_seq += 1
            self.cursor = self._event_seq
            self.events.append({**event, "seq": self._event_seq})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    # ---------- Роутинг ----------

    def _handle(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            path = request.url.path
            if path.startswith(PREFIX):
                path = path[len(PREFIX) :]
            method = request.method

            if method == "GET" and path == "/session":
                return self._get_session(request)
            if method == "POST" and path == "/session":
                return self._create_session(request)
            if method == "PATCH" and path == "/session/profile":
                return self._patch_profile(request)
            if method == "GET" and path == "/sync":
                return self._sync(request)
            if method == "GET" and path == "/masters":
                return self._json(200, {"masters": self.masters})
            if method == "GET" and path == "/workshops":
                return self._json(200, {"workshops": self.workshops})
            if method == "GET" and path == "/tickets":
                return self._list_tickets(request)
            if method == "POST" and path == "/messages":
                return self._send_message(request)
            if method == "POST" and path == "/attachments":
                return self._upload_attachment(request)

            parts = path.split("/")
            if len(parts) >= 3 and parts[1] == "tickets" and parts[2]:
                ticket_id = parts[2]
                rest = parts[3:]
                if method == "GET" and not rest:
                    return self._ticket(ticket_id)
                if method == "GET" and rest == ["messages"]:
                    return self._messages(ticket_id, request)
                if method == "POST" and rest == ["accept"]:
                    return self._set_status(ticket_id, "in_progress", keep_owner=True)
                if method == "POST" and rest == ["release"]:
                    return self._set_status(ticket_id, "new", keep_owner=False)
                if method == "POST" and rest == ["close"]:
                    return self._set_status(ticket_id, "closed", keep_owner=True)
                if method == "POST" and rest == ["reopen"]:
                    return self._set_status(ticket_id, "in_progress", keep_owner=True)
                if method == "POST" and rest == ["decline"]:
                    return self._json(200, {"ok": True})
                if method == "POST" and rest == ["read"]:
                    return self._json(200, {"ok": True})
                if method == "POST" and rest == ["members"]:
                    return self._add_member(ticket_id, request)
                if method == "DELETE" and len(rest) == 2 and rest[0] == "members":
                    return self._remove_member(ticket_id, rest[1])

            return self._json(404, _error("not_found", f"{method} {path} не поддерживается"))

    # ---------- Сессия ----------

    def _get_session(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("X-Instance-Id") not in self.instances:
            return self._json(404, _error("master_not_found", "Мастер не найден"))
        return self._json(200, self._session_payload())

    def _create_session(self, request: httpx.Request) -> httpx.Response:
        body = _json_body(request)
        instance_id = str(body.get("instance_id") or "")
        self.instances.add(instance_id)
        if body.get("full_name"):
            self.full_name = str(body["full_name"])
        if body.get("workshop_id"):
            self.workshop_id = str(body["workshop_id"])
        return self._json(200, self._session_payload())

    def _patch_profile(self, request: httpx.Request) -> httpx.Response:
        body = _json_body(request)
        if body.get("full_name"):
            self.full_name = str(body["full_name"])
        if body.get("workshop_id"):
            self.workshop_id = str(body["workshop_id"])
        return self._json(200, self._session_payload())

    def _session_payload(self) -> dict[str, Any]:
        return {
            "master_id": self.master_id,
            "profile": {
                "id": self.master_id,
                "full_name": self.full_name,
                "workshop_id": self.workshop_id,
                "workshop_name": self.workshop_name,
                "is_active": True,
                "online": True,
            },
            "cursor": self.cursor,
            "server_time": "2026-02-01T10:10:00+00:00",
            "token": None,
        }

    # ---------- Лента событий ----------

    def _sync(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        cursor = int(params.get("cursor", 0))
        limit = int(params.get("limit", 200))
        pending = [event for event in self.events if int(event["seq"]) > cursor]
        if not pending:
            return httpx.Response(204)
        batch = pending[:limit]
        return self._json(
            200,
            {
                "events": batch,
                "cursor": int(batch[-1]["seq"]),
                "has_more": len(pending) > limit,
            },
        )

    # ---------- Заявки ----------

    def _list_tickets(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        scope = str(params.get("scope", "feed"))
        status_filter = params.get("status")
        query = str(params.get("query") or "").casefold()
        limit = int(params.get("limit", 50))

        results: list[dict[str, Any]] = []
        for payload in self.tickets.values():
            if status_filter and payload.get("status") != status_filter:
                continue
            if scope == "feed" and payload.get("status") != "new":
                continue
            if scope == "mine" and not self._is_mine(payload):
                continue
            if query:
                haystack = " ".join(
                    str(payload.get(key) or "") for key in ("subject", "text")
                ).casefold()
                if query not in haystack:
                    continue
            results.append(payload)

        results.sort(key=lambda payload: str(payload.get("updated_at") or ""), reverse=True)
        return self._json(
            200,
            {"tickets": results[:limit], "next_before": None},
        )

    def _is_mine(self, payload: dict[str, Any]) -> bool:
        owner = payload.get("owner")
        if isinstance(owner, dict) and owner.get("id") == self.master_id:
            return True
        return any(
            str(item.get("master_id") or "") == self.master_id
            for item in payload.get("members") or []
        )

    def _ticket(self, ticket_id: str) -> httpx.Response:
        if ticket_id not in self.tickets:
            return self._json(404, _error("ticket_not_found", "Заявка не найдена"))
        return self._json(200, {"ticket": self.tickets[ticket_id]})

    def _set_status(
        self,
        ticket_id: str,
        status: str,
        *,
        keep_owner: bool,
    ) -> httpx.Response:
        if ticket_id not in self.tickets:
            return self._json(404, _error("ticket_not_found", "Заявка не найдена"))
        payload = self.tickets[ticket_id]
        payload["status"] = status
        payload["updated_at"] = "2026-02-01T12:00:00+00:00"
        if keep_owner:
            payload["owner"] = master_payload(
                self.master_id, full_name=self.full_name, workshop_id=self.workshop_id
            )
        else:
            payload["owner"] = None
        if status == "closed":
            payload["closed_at"] = "2026-02-01T12:00:00+00:00"
        else:
            payload["closed_at"] = None
        return self._json(200, {"ticket": payload})

    # ---------- Сообщения ----------

    def _messages(self, ticket_id: str, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        after = params.get("after_seq")
        before = params.get("before_seq")
        limit = int(params.get("limit", 100))
        rows = sorted(
            self.messages.get(ticket_id, []), key=lambda payload: int(payload["seq"])
        )
        if after is not None:
            rows = [row for row in rows if int(row["seq"]) > int(after)]
        if before is not None:
            rows = [row for row in rows if int(row["seq"]) < int(before)]
        return self._json(
            200,
            {
                "messages": rows[:limit],
                "has_more_before": False,
                "has_more_after": False,
            },
        )

    def _send_message(self, request: httpx.Request) -> httpx.Response:
        body = _json_body(request)
        ticket_id = str(body.get("ticket_id") or "")
        if ticket_id not in self.tickets:
            return self._json(404, _error("ticket_not_found", "Заявка не найдена"))
        self._msg_id += 1
        seq = self.seq_by_ticket.get(ticket_id, 0) + 1
        self.seq_by_ticket[ticket_id] = seq
        entry = message_payload(
            f"ms-{self._msg_id}",
            seq=seq,
            ticket_id=ticket_id,
            sender="master",
            text=str(body.get("text") or ""),
            delivery="queued",
        )
        ids = body.get("attachments")
        if ids:
            entry["attachments"] = [
                attachment
                for item in ids
                if (attachment := self.uploaded.get(str(item.get("attachment_id") or "")))
            ]
        self.add_message(ticket_id, entry)
        return self._json(200, {"message": entry})

    def _upload_attachment(self, request: httpx.Request) -> httpx.Response:
        self._att_id += 1
        content = request.content
        match = _re_filename.search(content)
        filename = match.group(1).decode("utf-8", "replace") if match else "file.bin"
        mime = "application/octet-stream"
        attachment_id = f"att-{self._att_id}"
        attachment = {
            "attachment_id": attachment_id,
            "kind": "file",
            "filename": filename,
            "mime_type": mime,
            "size": len(content),
            "source": "master",
            "created_at": "2026-02-01T12:00:00+00:00",
            "width": None,
            "height": None,
        }
        self.uploaded[attachment_id] = attachment
        return self._json(200, {"attachment": attachment})

    # ---------- Участники ----------

    def _add_member(self, ticket_id: str, request: httpx.Request) -> httpx.Response:
        if ticket_id not in self.tickets:
            return self._json(404, _error("ticket_not_found", "Заявка не найдена"))
        body = _json_body(request)
        master_id = str(body.get("master_id") or "")
        name = next(
            (
                str(master["full_name"])
                for master in self.masters
                if str(master["id"]) == master_id
            ),
            "Мастер",
        )
        member = {
            "master_id": master_id,
            "full_name": name,
            "role": "collaborator",
            "joined_at": "2026-02-01T12:00:00+00:00",
        }
        payload = self.tickets[ticket_id]
        members = [item for item in payload.get("members") or [] if item["master_id"] != master_id]
        members.append(member)
        payload["members"] = members
        return self._json(200, {"member": member})

    def _remove_member(self, ticket_id: str, master_id: str) -> httpx.Response:
        if ticket_id not in self.tickets:
            return self._json(404, _error("ticket_not_found", "Заявка не найдена"))
        payload = self.tickets[ticket_id]
        payload["members"] = [
            item for item in payload.get("members") or [] if item["master_id"] != master_id
        ]
        return self._json(200, {"ok": True})

    # ---------- Служебное ----------

    @staticmethod
    def _json(status: int, payload: dict[str, Any]) -> httpx.Response:
        return httpx.Response(status, json=payload)


def _json_body(request: httpx.Request) -> dict[str, Any]:
    try:
        value = json.loads(request.content.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def seeded_server() -> FakeServer:
    """Сервер с примером данных: одна моя, одна в ленте, одна закрытая."""
    server = FakeServer()
    server.masters.append(
        {
            **master_payload("m2", full_name="Пётр Кузнецов", workshop_id="body"),
            "role": "",
        }
    )

    server.add_ticket(
        ticket_payload(
            "t1",
            status="in_progress",
            subject="Стук при повороте",
            owner=master_payload("m1", full_name="Иванов Иван", workshop_id="body"),
        )
    )
    server.add_message(
        "t1",
        message_payload("m-1", seq=1, ticket_id="t1", sender="client", text="Стук появился вчера"),
    )
    server.add_message(
        "t1",
        message_payload(
            "m-2", seq=2, ticket_id="t1", sender="master", text="Принял, подъеду вечером"
        ),
    )
    server.add_message(
        "t1",
        message_payload(
            "m-3",
            seq=3,
            ticket_id="t1",
            sender="system",
            text="Заявка переведена в работу",
        ),
    )

    new_ticket = ticket_payload(
        "t2",
        status="new",
        subject="Не закрывается багажник",
    )
    new_ticket["owner"] = None
    server.add_ticket(new_ticket)
    server.add_message(
        "t2",
        message_payload(
            "m-4",
            seq=1,
            ticket_id="t2",
            sender="client",
            text="После аварии багажник не закрывается",
        ),
    )

    server.add_ticket(
        ticket_payload(
            "t3",
            status="closed",
            subject="Гарантийный ремонт",
            owner=master_payload("m1", full_name="Иванов Иван", workshop_id="body"),
        )
    )
    server.add_message(
        "t3",
        message_payload(
            "m-5", seq=1, ticket_id="t3", sender="client", text="Как продвигается ремонт?"
        ),
    )
    return server
