from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

try:
    from sqlalchemy import Engine, create_engine, text  # type: ignore
except Exception:  # pragma: no cover
    Engine = object  # type: ignore[misc,assignment]
    create_engine = None  # type: ignore[assignment]
    text = None  # type: ignore[assignment]


@dataclass(frozen=True)
class SessionRow:
    session_id: str
    label: str
    saved_at: str
    meal_messages: list
    shop_messages: list
    location: str
    active_notes: str
    active_note_names: list


@lru_cache(maxsize=4)
def get_engine(database_url: str) -> Engine:
    if create_engine is None:
        raise ImportError("SQLAlchemy is not installed. Install requirements.txt to use DATABASE_URL persistence.")
    # pool_pre_ping helps long-lived Streamlit processes
    return create_engine(database_url, pool_pre_ping=True, future=True)


def ensure_schema(engine: Engine) -> None:
    if text is None:
        raise ImportError("SQLAlchemy is not installed. Install requirements.txt to use DATABASE_URL persistence.")
    # Simple schema: one row per saved chat session (per user_id).
    # Messages are stored as JSON so we can preserve mixed content blocks.
    ddl = """
    CREATE TABLE IF NOT EXISTS chat_sessions (
      user_id TEXT NOT NULL,
      session_id TEXT NOT NULL,
      label TEXT NOT NULL,
      saved_at TEXT NOT NULL,
      meal_messages JSON NOT NULL,
      shop_messages JSON NOT NULL,
      location TEXT NOT NULL,
      active_notes TEXT NOT NULL,
      active_note_names JSON NOT NULL,
      PRIMARY KEY (user_id, session_id)
    );
    CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_saved_at
      ON chat_sessions (user_id, saved_at DESC);
    """
    with engine.begin() as conn:
        for stmt in [s.strip() for s in ddl.split(";") if s.strip()]:
            conn.execute(text(stmt))


def load_sessions(engine: Engine, user_id: str) -> dict[str, dict[str, Any]]:
    if text is None:
        raise ImportError("SQLAlchemy is not installed. Install requirements.txt to use DATABASE_URL persistence.")
    ensure_schema(engine)
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT session_id, label, saved_at, meal_messages, shop_messages, location, active_notes, active_note_names
                FROM chat_sessions
                WHERE user_id = :user_id
                ORDER BY saved_at DESC
                """
            ),
            {"user_id": user_id},
        ).mappings()
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            out[str(r["session_id"])] = {
                "label": r["label"],
                "saved_at": r["saved_at"],
                "meal_messages": r["meal_messages"] if isinstance(r["meal_messages"], list) else json.loads(r["meal_messages"]),
                "shop_messages": r["shop_messages"] if isinstance(r["shop_messages"], list) else json.loads(r["shop_messages"]),
                "location": r["location"],
                "active_notes": r["active_notes"],
                "active_note_names": r["active_note_names"]
                if isinstance(r["active_note_names"], list)
                else json.loads(r["active_note_names"]),
            }
        return out


def save_session(engine: Engine, user_id: str, session_id: str, session: dict[str, Any]) -> None:
    if text is None:
        raise ImportError("SQLAlchemy is not installed. Install requirements.txt to use DATABASE_URL persistence.")
    ensure_schema(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO chat_sessions (
                  user_id, session_id, label, saved_at,
                  meal_messages, shop_messages, location, active_notes, active_note_names
                )
                VALUES (
                  :user_id, :session_id, :label, :saved_at,
                  :meal_messages, :shop_messages, :location, :active_notes, :active_note_names
                )
                ON CONFLICT (user_id, session_id) DO UPDATE SET
                  label = excluded.label,
                  saved_at = excluded.saved_at,
                  meal_messages = excluded.meal_messages,
                  shop_messages = excluded.shop_messages,
                  location = excluded.location,
                  active_notes = excluded.active_notes,
                  active_note_names = excluded.active_note_names
                """
            ),
            {
                "user_id": user_id,
                "session_id": session_id,
                "label": session.get("label", "Chat"),
                "saved_at": session.get("saved_at", ""),
                "meal_messages": json.dumps(session.get("meal_messages", []), ensure_ascii=False),
                "shop_messages": json.dumps(session.get("shop_messages", []), ensure_ascii=False),
                "location": session.get("location", ""),
                "active_notes": session.get("active_notes", "") or "",
                "active_note_names": json.dumps(session.get("active_note_names", []), ensure_ascii=False),
            },
        )


def delete_session(engine: Engine, user_id: str, session_id: str) -> None:
    if text is None:
        raise ImportError("SQLAlchemy is not installed. Install requirements.txt to use DATABASE_URL persistence.")
    ensure_schema(engine)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM chat_sessions WHERE user_id = :user_id AND session_id = :session_id"),
            {"user_id": user_id, "session_id": session_id},
        )
