"""Anonymous, attributable learning events for FlowCity."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any


SENSITIVE_KEYS = {"rawInput", "fullConversation", "conversation", "messages", "prompt"}


def _sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize_payload(item)
            for key, item in value.items()
            if str(key) not in SENSITIVE_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_payload(item) for item in value[:30]]
    if isinstance(value, str):
        return value[:500]
    return value


def default_db_path() -> Path:
    configured = os.getenv("FLOWCITY_LEARNING_DB")
    if configured:
        return Path(configured)
    return Path.home() / ".flowcity" / "learning_events.sqlite3"


def anonymous_session_id(session_id: str | None) -> str:
    text = str(session_id or "anonymous")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


class LearningEventStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS learning_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    session_hash TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    hypothesis_id TEXT,
                    cluster_key TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_learning_hypothesis
                    ON learning_events(hypothesis_id, event_type);
                CREATE INDEX IF NOT EXISTS idx_learning_cluster
                    ON learning_events(cluster_key, event_type);
                CREATE TABLE IF NOT EXISTS ontology_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    proposal_type TEXT NOT NULL,
                    cluster_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )
            connection.commit()

    def record(
        self,
        event_type: str,
        *,
        session_id: str | None = None,
        hypothesis_id: str | None = None,
        cluster_key: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        safe_payload = _sanitize_payload(dict(payload or {}))
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO learning_events(
                    created_at, session_hash, event_type, hypothesis_id, cluster_key, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    time.time(),
                    anonymous_session_id(session_id),
                    event_type,
                    hypothesis_id,
                    cluster_key,
                    json.dumps(safe_payload, ensure_ascii=False, default=str),
                ),
            )
            connection.commit()

    def events(self, event_type: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM learning_events"
        params: tuple[Any, ...] = ()
        if event_type:
            query += " WHERE event_type = ?"
            params = (event_type,)
        query += " ORDER BY event_id"
        with closing(self._connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            {
                **dict(row),
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def upsert_proposal(self, proposal: dict[str, Any]) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO ontology_proposals(
                    proposal_id, created_at, proposal_type, cluster_key, status, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(proposal_id) DO UPDATE SET
                    status=excluded.status,
                    payload_json=excluded.payload_json
                """,
                (
                    proposal["proposalId"],
                    time.time(),
                    proposal["proposalType"],
                    proposal["clusterKey"],
                    proposal.get("status", "pending_review"),
                    json.dumps(proposal, ensure_ascii=False, default=str),
                ),
            )
            connection.commit()

    def proposals(self, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM ontology_proposals"
        params: tuple[Any, ...] = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY created_at DESC"
        with closing(self._connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            {
                **dict(row),
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def review_proposal(self, proposal_id: str, status: str) -> bool:
        if status not in {"approved", "rejected", "pending_review"}:
            raise ValueError(f"Unsupported proposal status: {status}")
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                "UPDATE ontology_proposals SET status = ? WHERE proposal_id = ?",
                (status, proposal_id),
            )
            connection.commit()
            return cursor.rowcount > 0


_STORE: LearningEventStore | None = None


def get_store() -> LearningEventStore:
    global _STORE
    if _STORE is None:
        _STORE = LearningEventStore()
    return _STORE
