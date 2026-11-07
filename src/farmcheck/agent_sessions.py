# Copyright (c) 2026 FarmCheck contributors.
"""Workspace and record scoped conversation ownership with expiring turn leases."""

import json
import secrets
import time
from typing import TYPE_CHECKING

from pydantic import JsonValue

from farmcheck.service import JSON_OBJECT
from farmcheck.store import DomainError, Store

if TYPE_CHECKING:
    from collections.abc import Callable


class AgentSessions:
    """Store remote IDs only on the server; serialize turns across app workers."""

    def __init__(self, store: Store) -> None:
        """Create an additive table without modifying existing records or reports."""
        self.store: Store = store
        with store.connection() as db:
            _ = db.execute("""CREATE TABLE IF NOT EXISTS agent_conversations(
                workspace TEXT NOT NULL REFERENCES workspaces(id),
                record_id TEXT NOT NULL, provider TEXT NOT NULL,
                session_id TEXT, history TEXT NOT NULL DEFAULT '{"turns":[]}',
                lease TEXT, expires REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(workspace, record_id, provider))""")

    def acquire(self, ws: str, record: str, provider: str) -> tuple[str, str | None]:
        """Atomically reserve a turn; discard sessions from abandoned turns."""
        token = secrets.token_urlsafe(24)
        with self.store.connection() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            _ = db.execute(
                """INSERT OR IGNORE INTO agent_conversations(workspace,record_id,provider)
                VALUES(?,?,?)""",
                (ws, record, provider),
            )
            fetch: Callable[[], tuple[str | None, str | None, float]] = db.execute(
                """SELECT session_id,lease,expires FROM agent_conversations
                WHERE workspace=? AND record_id=? AND provider=?""",
                (ws, record, provider),
            ).fetchone
            row = fetch()
            if row[2] > time.time():
                code = "agent_busy"
                raise DomainError(code, "이 기록의 답변을 기다린 뒤 질문해 주세요.", 409)
            session = row[0] if row[1] is None else None
            _ = db.execute(
                """UPDATE agent_conversations SET lease=?,expires=?,session_id=?
                WHERE workspace=? AND record_id=? AND provider=?""",
                (token, time.time() + 120, session, ws, record, provider),
            )
        return token, session

    def bind(self, token: str, session: str) -> None:
        """Attach the upstream ID only to the reserved server-side conversation."""
        with self.store.connection() as db:
            _ = db.execute(
                "UPDATE agent_conversations SET session_id=? WHERE lease=?",
                (session, token),
            )

    def finish(self, token: str, turn: dict[str, JsonValue] | None) -> None:
        """Save a completed turn, or invalidate an uncertain upstream session."""
        with self.store.connection() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            fetch: Callable[[], tuple[str] | None] = db.execute(
                "SELECT history FROM agent_conversations WHERE lease=?",
                (token,),
            ).fetchone
            row = fetch()
            if row is None:
                return
            if turn is None:
                _ = db.execute(
                    """UPDATE agent_conversations SET session_id=NULL,lease=NULL,expires=0
                    WHERE lease=?""",
                    (token,),
                )
                return
            history = JSON_OBJECT.validate_json(row[0]).get("turns", [])
            turns = history if isinstance(history, list) else []
            turns.append(turn)
            _ = db.execute(
                "UPDATE agent_conversations SET history=?,lease=NULL,expires=0 WHERE lease=?",
                (json.dumps({"turns": turns[-20:]}, ensure_ascii=False), token),
            )

    def history(self, ws: str, record: str, provider: str) -> dict[str, JsonValue]:
        """Return completed turns only, excluding keys and upstream IDs."""
        with self.store.connection() as db:
            fetch: Callable[[], tuple[str] | None] = db.execute(
                """SELECT history FROM agent_conversations
                WHERE workspace=? AND record_id=? AND provider=?""",
                (ws, record, provider),
            ).fetchone
            row = fetch()
        return {"turns": []} if row is None else JSON_OBJECT.validate_json(row[0])

    def reset(self, ws: str, record: str, provider: str) -> None:
        """Forget the local conversation without disrupting an active turn."""
        with self.store.connection() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            fetch: Callable[[], tuple[float] | None] = db.execute(
                """SELECT expires FROM agent_conversations
                WHERE workspace=? AND record_id=? AND provider=?""",
                (ws, record, provider),
            ).fetchone
            row = fetch()
            if row is not None and row[0] > time.time():
                code = "agent_busy"
                raise DomainError(code, "답변이 끝난 뒤 새 대화를 시작해 주세요.", 409)
            _ = db.execute(
                "DELETE FROM agent_conversations WHERE workspace=? AND record_id=? AND provider=?",
                (ws, record, provider),
            )
