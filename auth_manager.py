# -*- coding: utf-8 -*-
"""Telegram OTP sessions for Momentum mobile app login."""

from __future__ import annotations

import json
import os
import secrets
import string
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from aiogram import Bot

AUTH_SESSIONS_FILE = "auth_sessions.json"
AUTH_USERS_FILE = "auth_users.json"
CODE_TTL_MINUTES = 5
SESSION_TTL_MINUTES = 10


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass
class AuthSession:
    session_id: str
    code: str
    created_at: str
    expires_at: str
    telegram_id: Optional[int] = None
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    verified: bool = False
    access_token: Optional[str] = None

    def is_expired(self) -> bool:
        return _utc_now() > _parse_dt(self.expires_at)

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "verified": self.verified,
            "expires_at": self.expires_at,
            "telegram_id": self.telegram_id,
            "username": self.username,
            "first_name": self.first_name,
        }


class AuthManager:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.sessions: Dict[str, AuthSession] = {}
        self.users_by_username: Dict[str, int] = {}
        self.users_by_id: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(AUTH_SESSIONS_FILE):
            try:
                with open(AUTH_SESSIONS_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for sid, data in raw.items():
                    self.sessions[sid] = AuthSession(**data)
            except Exception:
                self.sessions = {}

        if os.path.exists(AUTH_USERS_FILE):
            try:
                with open(AUTH_USERS_FILE, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                self.users_by_username = {
                    k.lower(): int(v) for k, v in payload.get("by_username", {}).items()
                }
                self.users_by_id = payload.get("by_id", {})
            except Exception:
                self.users_by_username = {}
                self.users_by_id = {}

    def _save_sessions(self) -> None:
        with open(AUTH_SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {sid: asdict(s) for sid, s in self.sessions.items()},
                f,
                ensure_ascii=False,
                indent=2,
            )

    def _save_users(self) -> None:
        with open(AUTH_USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {"by_username": self.users_by_username, "by_id": self.users_by_id},
                f,
                ensure_ascii=False,
                indent=2,
            )

    @staticmethod
    def _normalize_username(username: str) -> str:
        return username.strip().lstrip("@").lower()

    @staticmethod
    def _generate_code() -> str:
        return "".join(secrets.choice(string.digits) for _ in range(6))

    @staticmethod
    def _generate_session_id() -> str:
        return secrets.token_urlsafe(12)

    @staticmethod
    def _generate_token() -> str:
        return secrets.token_urlsafe(32)

    def register_telegram_user(
        self,
        telegram_id: int,
        username: Optional[str],
        first_name: Optional[str],
        last_name: Optional[str],
    ) -> None:
        key = str(telegram_id)
        self.users_by_id[key] = {
            "telegram_id": telegram_id,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
        }
        if username:
            self.users_by_username[self._normalize_username(username)] = telegram_id
        self._save_users()

    def create_session(self) -> AuthSession:
        session_id = self._generate_session_id()
        now = _utc_now()
        session = AuthSession(
            session_id=session_id,
            code=self._generate_code(),
            created_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=SESSION_TTL_MINUTES)).isoformat(),
        )
        self.sessions[session_id] = session
        self._save_sessions()
        return session

    def get_session(self, session_id: str) -> Optional[AuthSession]:
        session = self.sessions.get(session_id)
        if not session:
            return None
        if session.is_expired():
            del self.sessions[session_id]
            self._save_sessions()
            return None
        return session

    async def bind_session_to_user(self, session_id: str, telegram_id: int) -> AuthSession:
        session = self.get_session(session_id)
        if not session:
            raise ValueError("session_not_found")

        user_meta = self.users_by_id.get(str(telegram_id), {})
        session.telegram_id = telegram_id
        session.username = user_meta.get("username")
        session.first_name = user_meta.get("first_name")
        session.last_name = user_meta.get("last_name")
        session.code = self._generate_code()
        self._save_sessions()

        await self.bot.send_message(
            telegram_id,
            (
                "🔐 <b>Вход в Momentum</b>\n\n"
                f"Ваш код подтверждения:\n<b>{session.code}</b>\n\n"
                f"Код действует {CODE_TTL_MINUTES} минут. "
                "Никому его не сообщайте."
            ),
            parse_mode="HTML",
        )
        return session

    async def request_code_for_username(self, username: str) -> AuthSession:
        normalized = self._normalize_username(username)
        if not normalized:
            raise ValueError("username_required")

        telegram_id = self.users_by_username.get(normalized)
        if not telegram_id:
            raise ValueError("user_not_linked")

        session = self.create_session()
        session.telegram_id = telegram_id
        meta = self.users_by_id.get(str(telegram_id), {})
        session.username = meta.get("username")
        session.first_name = meta.get("first_name")
        session.last_name = meta.get("last_name")
        self._save_sessions()

        await self.bot.send_message(
            telegram_id,
            (
                "🔐 <b>Вход в Momentum</b>\n\n"
                f"Ваш код подтверждения:\n<b>{session.code}</b>\n\n"
                f"Код действует {CODE_TTL_MINUTES} минут."
            ),
            parse_mode="HTML",
        )
        return session

    def verify_code(self, session_id: str, code: str) -> AuthSession:
        session = self.get_session(session_id)
        if not session:
            raise ValueError("session_not_found")
        if not session.telegram_id:
            raise ValueError("telegram_not_linked")
        if session.code != code.strip():
            raise ValueError("invalid_code")

        session.verified = True
        session.access_token = self._generate_token()
        self._save_sessions()
        return session
