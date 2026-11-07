# Copyright (c) 2026 FarmCheck contributors.
"""Transactional workspace state and immutable document storage.

Workspace IDs are public ownership keys, never v2 credentials. On the first schema
upgrade only existing workspaces become eligible for one legacy bootstrap claim.
Until that claim, a leaked old ID can still claim the workspace: bootstrap promptly
after deployment. Claiming atomically disables the old ID without rewriting any
imports or immutable snapshots. New workspaces are never legacy-claimable.
"""

import json
import secrets
import sqlite3
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from farmcheck.contracts import SourceRecord

MAX_IMPORT_PAIRS = 32
MAX_WORKSPACE_IMPORT_BYTES = 128 * 1024 * 1024


class DomainError(Exception):
    """An expected conflict or invalid workflow action."""

    def __init__(self, code: str, message: str, status: int = 422) -> None:
        """Carry a safe HTTP error without internal database details."""
        super().__init__(message)
        self.code: str = code
        self.message: str = message
        self.status: int = status


class FrozenModel(BaseModel):
    """Reject additional boundary fields and accidental assignment."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class Event(FrozenModel):
    """An append-only action receipt."""

    action: str
    actor: str
    created_at: str
    revision: int
    data_revision: int
    details: dict[str, JsonValue] = Field(default_factory=dict)


class RecordState(FrozenModel):
    """Mutable workflow represented as an immutable versioned value."""

    record_id: str
    revision: int = 0
    data_revision: int = 0
    review_state: Literal["unreviewed", "reviewed", "needs_review"] = "unreviewed"
    resolution_state: Literal["open", "held", "resolved"] = "open"
    overlays: dict[str, str] = Field(default_factory=dict)
    assessment: dict[str, JsonValue] | None = None
    assessed_data_revision: int | None = None
    model: dict[str, JsonValue] | None = None
    model_attempt: dict[str, JsonValue] | None = None
    pending_model_request: str | None = None
    note: str = ""
    hold_reason: str = ""
    next_action: str = ""
    evidence_note: str = ""
    evidence_refs: tuple[str, ...] = ()
    history: tuple[Event, ...] = ()


class WorkspaceState(FrozenModel):
    """A consistent workspace revision and all touched records."""

    workspace_revision: int
    workspace_id: str = ""
    records: tuple[RecordState, ...]


class Snapshot(FrozenModel):
    """Stored bytes, never regenerated from current state."""

    id: str
    workspace_revision: int
    created_at: str
    sha256: str
    payload: str


class ModelReceipt(FrozenModel):
    """An immutable external reply, including replies later rejected as stale."""

    request_id: str
    record_id: str
    based_on_revision: int
    based_on_data_revision: int
    created_at: str
    result: dict[str, JsonValue]


def timestamp() -> str:
    """Return an aware audit timestamp."""
    return datetime.now(UTC).isoformat()


class Store:
    """Own short SQLite transactions; every write compares its expected version."""

    def __init__(self, path: Path) -> None:
        """Initialize only this application's database."""
        self.path: Path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            _ = connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workspaces(
                  id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS records(
                  workspace TEXT NOT NULL REFERENCES workspaces(id),
                  record_id TEXT NOT NULL, payload TEXT NOT NULL,
                  PRIMARY KEY(workspace,record_id));
                CREATE TABLE IF NOT EXISTS snapshots(
                  workspace TEXT NOT NULL REFERENCES workspaces(id),
                  id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
                  PRIMARY KEY(workspace,id));
                CREATE TABLE IF NOT EXISTS imports(
                  workspace TEXT NOT NULL REFERENCES workspaces(id),
                  record_id TEXT NOT NULL, source TEXT NOT NULL, image BLOB NOT NULL,
                  image_sha256 TEXT NOT NULL, json_sha256 TEXT NOT NULL,
                  warnings TEXT NOT NULL, source_bytes INTEGER NOT NULL,
                  PRIMARY KEY(workspace,record_id),
                  UNIQUE(workspace,image_sha256), UNIQUE(workspace,json_sha256));
                CREATE TABLE IF NOT EXISTS model_receipts(
                  workspace TEXT NOT NULL REFERENCES workspaces(id),
                  request_id TEXT NOT NULL, record_id TEXT NOT NULL, payload TEXT NOT NULL,
                  PRIMARY KEY(workspace,request_id));
                """,
            )

        # Serialize the one-time migration with bootstrap and other app instances.
        with self.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            first_upgrade = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workspace_sessions'",
            ).fetchone() is None
            _ = connection.execute(
                """CREATE TABLE IF NOT EXISTS workspace_sessions(
                token_hash TEXT PRIMARY KEY,
                workspace TEXT NOT NULL UNIQUE REFERENCES workspaces(id))""",
            )
            _ = connection.execute(
                """CREATE TABLE IF NOT EXISTS legacy_workspace_claims(
                workspace TEXT PRIMARY KEY REFERENCES workspaces(id))""",
            )
            if first_upgrade:
                _ = connection.execute(
                    "INSERT INTO legacy_workspace_claims SELECT id FROM workspaces",
                )

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection]:
        """Close connections even when a transaction fails."""
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            _ = connection.execute("PRAGMA foreign_keys=ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def workspace(self, supplied: str | None) -> str:
        """Return an internal stable ownership key, never an HTTP credential."""
        with self.connection() as connection:
            if (
                supplied
                and connection.execute(
                    "SELECT 1 FROM workspaces WHERE id=?",
                    (supplied,),
                ).fetchone()
            ):
                return supplied
            identifier = secrets.token_urlsafe(32)
            _ = connection.execute("INSERT INTO workspaces(id) VALUES(?)", (identifier,))
            return identifier

    def bootstrap_session(self, supplied: str | None) -> tuple[str, str]:
        """Resolve v2 or claim an eligible legacy ID once, returning owner and cookie.

        Only bootstrap may migrate a legacy credential. Invalid or already claimed
        IDs get a separate empty workspace, never access to the original owner.
        """
        with self.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            if supplied:
                existing = self._session_workspace(connection, supplied)
                if existing is not None:
                    return existing, supplied
            legacy: Callable[[], tuple[str] | None] = connection.execute(
                """SELECT workspace FROM legacy_workspace_claims WHERE workspace=?
                AND NOT EXISTS(SELECT 1 FROM workspace_sessions WHERE workspace=?)""",
                (supplied, supplied),
            ).fetchone
            claim = legacy()
            if claim is None:
                identifier = secrets.token_urlsafe(32)
                _ = connection.execute("INSERT INTO workspaces(id) VALUES(?)", (identifier,))
            else:
                identifier = claim[0]
                _ = connection.execute(
                    "DELETE FROM legacy_workspace_claims WHERE workspace=?", (identifier,),
                )
            credential = "v2_" + secrets.token_urlsafe(32)
            _ = connection.execute(
                "INSERT INTO workspace_sessions(token_hash,workspace) VALUES(?,?)",
                (sha256(credential.encode()).hexdigest(), identifier),
            )
            return identifier, credential

    def resolve_session(self, supplied: str) -> str:
        """Authenticate only a hashed v2 credential and return its stable owner ID."""
        with self.connection() as connection:
            identifier = self._session_workspace(connection, supplied)
            if identifier is None:
                code = "workspace_required"
                raise DomainError(code, "먼저 작업 공간을 열어 주세요.", 401)
            return identifier

    @staticmethod
    def _session_workspace(connection: sqlite3.Connection, supplied: str) -> str | None:
        if not supplied.startswith("v2_"):
            return None
        fetch: Callable[[], tuple[str] | None] = connection.execute(
            "SELECT workspace FROM workspace_sessions WHERE token_hash=?",
            (sha256(supplied.encode()).hexdigest(),),
        ).fetchone
        row = fetch()
        return None if row is None else row[0]

    def read(self, workspace: str) -> WorkspaceState:
        """Read all records and their global version in one transaction."""
        with self.connection() as connection:
            _ = connection.execute("BEGIN")
            revision = self._revision(connection, workspace)
            rows = connection.execute(
                "SELECT payload FROM records WHERE workspace=? ORDER BY record_id",
                (workspace,),
            )
            fetch: Callable[[], list[tuple[str]]] = rows.fetchall
            records = tuple(RecordState.model_validate_json(row[0]) for row in fetch())
            return WorkspaceState(
                workspace_revision=revision, workspace_id=workspace, records=records
            )

    def record(self, workspace: str, record_id: str) -> RecordState:
        """Get workflow state; source existence is checked by the caller."""
        with self.connection() as connection:
            _ = self._revision(connection, workspace)
            return self._record(connection, workspace, record_id)

    def mutate(
        self,
        workspace: str,
        record_id: str,
        expected_revision: int,
        transform: Callable[[RecordState], RecordState],
    ) -> RecordState:
        """Compare, transform and persist atomically, or roll back entirely."""
        with self.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            _ = self._revision(connection, workspace)
            previous = self._record(connection, workspace, record_id)
            if previous.revision != expected_revision:
                code = "revision_conflict"
                raise DomainError(code, "기록이 변경되었습니다. 다시 열어 주세요.", 409)
            updated = transform(previous)
            if updated.revision != previous.revision + 1 or updated.record_id != record_id:
                code = "invalid_transition"
                raise DomainError(code, "잘못된 버전 전이입니다.", 500)
            _ = connection.execute(
                """INSERT INTO records(workspace,record_id,payload) VALUES(?,?,?)
                ON CONFLICT(workspace,record_id) DO UPDATE SET payload=excluded.payload""",
                (workspace, record_id, updated.model_dump_json()),
            )
            _ = connection.execute(
                "UPDATE workspaces SET revision=revision+1 WHERE id=?",
                (workspace,),
            )
            return updated

    def capture(
        self,
        workspace: str,
        expected_revision: int,
        kind: Literal["handover", "agent"],
        materialize: Callable[[WorkspaceState], str],
    ) -> Snapshot:
        """Materialize a frozen document while holding the version lock."""
        with self.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            revision = self._revision(connection, workspace)
            if revision != expected_revision:
                code = "workspace_conflict"
                raise DomainError(code, "작업 공간이 변경되었습니다. 다시 확인하세요.", 409)
            rows = connection.execute(
                "SELECT payload FROM records WHERE workspace=? ORDER BY record_id",
                (workspace,),
            )
            fetch: Callable[[], list[tuple[str]]] = rows.fetchall
            state = WorkspaceState(
                workspace_revision=revision,
                workspace_id=workspace,
                records=tuple(RecordState.model_validate_json(row[0]) for row in fetch()),
            )
            payload = materialize(state)
            snapshot = Snapshot(
                id=secrets.token_urlsafe(18),
                workspace_revision=revision,
                created_at=timestamp(),
                sha256=sha256(payload.encode()).hexdigest(),
                payload=payload,
            )
            _ = connection.execute(
                "INSERT INTO snapshots(workspace,id,kind,payload) VALUES(?,?,?,?)",
                (workspace, snapshot.id, kind, snapshot.model_dump_json()),
            )
            return snapshot

    def snapshot(
        self,
        workspace: str,
        snapshot_id: str,
        kind: Literal["handover", "agent"],
    ) -> Snapshot:
        """Only the owning workspace can retrieve an immutable document."""
        with self.connection() as connection:
            fetch: Callable[[], tuple[str] | None] = connection.execute(
                "SELECT payload FROM snapshots WHERE workspace=? AND id=? AND kind=?",
                (workspace, snapshot_id, kind),
            ).fetchone
            row = fetch()
            if row is None:
                code = "not_found"
                raise DomainError(code, "문서를 찾을 수 없습니다.", 404)
            return Snapshot.model_validate_json(row[0])

    def save_model_receipt(self, workspace: str, receipt: ModelReceipt) -> None:
        """Preserve a reply before attempting version-sensitive adoption."""
        with self.connection() as connection:
            _ = connection.execute(
                """INSERT INTO model_receipts(workspace,request_id,record_id,payload)
                VALUES(?,?,?,?)""",
                (workspace, receipt.request_id, receipt.record_id, receipt.model_dump_json()),
            )

    def model_receipts(self, workspace: str, record_id: str) -> tuple[ModelReceipt, ...]:
        """Read replies only for the owning workspace and requested record."""
        with self.connection() as connection:
            fetch: Callable[[], list[tuple[str]]] = connection.execute(
                """SELECT payload FROM model_receipts
                WHERE workspace=? AND record_id=? ORDER BY rowid""",
                (workspace, record_id),
            ).fetchall
            return tuple(ModelReceipt.model_validate_json(row[0]) for row in fetch())

    def imported_sources(self, workspace: str) -> dict[str, SourceRecord]:
        """Never merge private sources into the shared catalog."""
        if not workspace:
            return {}
        with self.connection() as connection:
            _ = self._revision(connection, workspace)
            fetch: Callable[[], list[tuple[str, str]]] = connection.execute(
                "SELECT record_id,source FROM imports WHERE workspace=? ORDER BY rowid",
                (workspace,),
            ).fetchall
            return {row[0]: SourceRecord.model_validate_json(row[1]) for row in fetch()}

    def imported_image(self, workspace: str, record_id: str) -> bytes:
        """Resolve only a database key, never a user-controlled filesystem path."""
        with self.connection() as connection:
            fetch: Callable[[], tuple[bytes] | None] = connection.execute(
                "SELECT image FROM imports WHERE workspace=? AND record_id=?",
                (workspace, record_id),
            ).fetchone
            row = fetch()
            if row is None:
                code = "not_found"
                raise DomainError(code, "사진 기록을 찾을 수 없습니다.", 404)
            return bytes(row[0])

    def import_pair(
        self,
        workspace: str,
        expected_revision: int,
        source: SourceRecord,
        image: bytes,
        warnings: tuple[str, ...],
    ) -> WorkspaceState:
        """Commit immutable pair and workspace revision together, including quota checks."""
        image_hash = sha256(image).hexdigest()
        size = len(image) + len(source.raw_bytes)
        with self.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            revision = self._revision(connection, workspace)
            if revision != expected_revision:
                code = "workspace_conflict"
                raise DomainError(code, "작업 공간이 변경되었습니다. 다시 확인하세요.", 409)
            if connection.execute(
                "SELECT 1 FROM imports WHERE workspace=? AND (image_sha256=? OR json_sha256=?)",
                (workspace, image_hash, source.raw_hash),
            ).fetchone():
                code = "duplicate_import"
                raise DomainError(code, "이미 가져온 사진 또는 JSON입니다.", 409)
            fetch_totals: Callable[[], tuple[int, int]] = connection.execute(
                "SELECT COUNT(*),COALESCE(SUM(source_bytes),0) FROM imports WHERE workspace=?",
                (workspace,),
            ).fetchone
            count, total = fetch_totals()
            if count >= MAX_IMPORT_PAIRS or total + size > MAX_WORKSPACE_IMPORT_BYTES:
                code = "import_quota"
                raise DomainError(
                    code, "작업 공간 가져오기 한도(32쌍 / 128 MiB)에 도달했습니다.", 413
                )
            _ = connection.execute(
                "INSERT INTO imports VALUES(?,?,?,?,?,?,?,?)",
                (
                    workspace,
                    source.id,
                    source.model_dump_json(exclude={"raw", "values"}),
                    image,
                    image_hash,
                    source.raw_hash,
                    json.dumps(warnings, ensure_ascii=False),
                    size,
                ),
            )
            _ = connection.execute(
                "UPDATE workspaces SET revision=revision+1 WHERE id=?", (workspace,)
            )
        return WorkspaceState(workspace_revision=revision + 1, workspace_id=workspace, records=())

    def import_metadata(self, workspace: str, record_id: str) -> dict[str, JsonValue]:
        """Only the owner can retrieve source hashes and validation warnings."""
        with self.connection() as connection:
            fetch: Callable[[], tuple[str, str, str] | None] = connection.execute(
                """SELECT image_sha256,json_sha256,warnings FROM imports
                WHERE workspace=? AND record_id=?""",
                (workspace, record_id),
            ).fetchone
            row = fetch()
            if row is None:
                code = "not_found"
                raise DomainError(code, "사진 기록을 찾을 수 없습니다.", 404)
            return {
                "origin": "uploaded",
                "image_sha256": row[0],
                "json_sha256": row[1],
                "warnings": TypeAdapter(list[JsonValue]).validate_json(row[2]),
            }

    def snapshots(self, workspace: str) -> tuple[Snapshot, ...]:
        """List only the owning workspace's immutable handovers, newest first."""
        with self.connection() as connection:
            _ = self._revision(connection, workspace)
            fetch: Callable[[], list[tuple[str]]] = connection.execute(
                """SELECT payload FROM snapshots
                WHERE workspace=? AND kind='handover' ORDER BY rowid DESC""",
                (workspace,),
            ).fetchall
            return tuple(Snapshot.model_validate_json(row[0]) for row in fetch())

    @staticmethod
    def _revision(connection: sqlite3.Connection, workspace: str) -> int:
        fetch: Callable[[], tuple[int] | None] = connection.execute(
            "SELECT revision FROM workspaces WHERE id=?",
            (workspace,),
        ).fetchone
        row = fetch()
        if row is None:
            code = "workspace_not_found"
            raise DomainError(code, "작업 공간을 다시 열어 주세요.", 404)
        return int(row[0])

    @staticmethod
    def _record(
        connection: sqlite3.Connection,
        workspace: str,
        record_id: str,
    ) -> RecordState:
        fetch: Callable[[], tuple[str] | None] = connection.execute(
            "SELECT payload FROM records WHERE workspace=? AND record_id=?",
            (workspace, record_id),
        ).fetchone
        row = fetch()
        if row is None:
            return RecordState(record_id=record_id)
        return RecordState.model_validate_json(row[0])
