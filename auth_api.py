# -*- coding: utf-8 -*-
"""HTTP API for Momentum mobile Telegram auth."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from auth_manager import AuthManager

logger = logging.getLogger(__name__)


def _json_error(message: str, status: int = 400, **extra) -> web.Response:
    payload = {"ok": False, "error": message, **extra}
    return web.json_response(payload, status=status)


def _cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


async def health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "momentum-auth"}, headers=_cors_headers())


async def create_session(request: web.Request) -> web.Response:
    auth: AuthManager = request.app["auth_manager"]
    bot_username: str = request.app["bot_username"]

    try:
        body = {}
        if request.can_read_body and request.body_exists:
            raw = await request.read()
            if raw:
                body = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return _json_error("invalid_json", 400)

    username = (body.get("username") or "").strip()
    if username:
        try:
            session = await auth.request_code_for_username(username)
        except ValueError as exc:
            code = str(exc)
            if code == "user_not_linked":
                return _json_error(
                    "user_not_linked",
                    404,
                    message=(
                        "Сначала откройте бота в Telegram и нажмите /start, "
                        "затем запросите код снова."
                    ),
                    bot_username=bot_username,
                    deep_link=f"https://t.me/{bot_username}?start=app",
                )
            return _json_error(code, 400)

        return web.json_response(
            {
                "ok": True,
                "session_id": session.session_id,
                "expires_at": session.expires_at,
                "message": "Код отправлен в Telegram",
            },
            headers=_cors_headers(),
        )

    session = auth.create_session()
    deep_link = f"https://t.me/{bot_username}?start=login_{session.session_id}"
    return web.json_response(
        {
            "ok": True,
            "session_id": session.session_id,
            "expires_at": session.expires_at,
            "bot_username": bot_username,
            "deep_link": deep_link,
            "message": "Откройте бота в Telegram или введите @username",
        },
        headers=_cors_headers(),
    )


async def verify_code(request: web.Request) -> web.Response:
    auth: AuthManager = request.app["auth_manager"]
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _json_error("invalid_json", 400)

    session_id = (body.get("session_id") or "").strip()
    code = (body.get("code") or "").strip()
    if not session_id or not code:
        return _json_error("session_id_and_code_required", 400)

    try:
        session = auth.verify_code(session_id, code)
    except ValueError as exc:
        err = str(exc)
        status = 404 if err == "session_not_found" else 401
        return _json_error(err, status)

    display = session.first_name or session.username or f"User {session.telegram_id}"
    return web.json_response(
        {
            "ok": True,
            "access_token": session.access_token,
            "user": {
                "telegram_id": session.telegram_id,
                "username": session.username,
                "first_name": session.first_name,
                "last_name": session.last_name,
                "display_name": display,
            },
        },
        headers=_cors_headers(),
    )


async def session_status(request: web.Request) -> web.Response:
    auth: AuthManager = request.app["auth_manager"]
    session_id = request.match_info.get("session_id", "")
    session = auth.get_session(session_id)
    if not session:
        return _json_error("session_not_found", 404)

    return web.json_response(
        {"ok": True, **session.to_public_dict()},
        headers=_cors_headers(),
    )


async def options_handler(_: web.Request) -> web.Response:
    return web.Response(status=204, headers=_cors_headers())


def create_app(auth_manager: AuthManager, bot_username: str) -> web.Application:
    app = web.Application()
    app["auth_manager"] = auth_manager
    app["bot_username"] = bot_username

    app.router.add_route("OPTIONS", "/api/auth/session", options_handler)
    app.router.add_route("OPTIONS", "/api/auth/verify", options_handler)
    app.router.add_route("OPTIONS", "/api/auth/session/{session_id}", options_handler)
    app.router.add_get("/api/health", health)
    app.router.add_post("/api/auth/session", create_session)
    app.router.add_post("/api/auth/verify", verify_code)
    app.router.add_get("/api/auth/session/{session_id}", session_status)
    return app


async def start_auth_api(auth_manager: AuthManager, bot_username: str, host: str, port: int):
    app = create_app(auth_manager, bot_username)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Auth API listening on http://%s:%s", host, port)
    return runner
