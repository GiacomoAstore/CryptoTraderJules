"""Validate .env API keys without printing secrets."""
import asyncio
import hashlib
import hmac
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def mask(value: str) -> str:
    if not value:
        return "(vuoto)"
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}...{value[-4:]} (len={len(value)})"


async def test_binance(key: str, secret: str) -> dict:
    if not key or not secret:
        return {"ok": False, "message": "BINANCE_API_KEY o BINANCE_API_SECRET mancanti"}
    if key.startswith("your_") or "here" in key.lower():
        return {"ok": False, "message": "Chiavi placeholder, non valide"}

    params = {"timestamp": int(time.time() * 1000)}
    qs = urlencode(params)
    sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f"https://api.binance.com/api/v3/account?{qs}&signature={sig}"
    headers = {"X-MBX-APIKEY": key}

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, headers=headers)
    if r.status_code == 200:
        data = r.json()
        balances = [
            b for b in data.get("balances", [])
            if float(b.get("free", 0)) > 0 or float(b.get("locked", 0)) > 0
        ]
        return {
            "ok": True,
            "message": f"Autenticazione OK — {len(balances)} asset con saldo > 0",
            "can_trade": data.get("canTrade"),
            "permissions": data.get("permissions"),
        }
    try:
        err = r.json()
        msg = err.get("msg", r.text[:200])
        code = err.get("code", r.status_code)
    except Exception:
        msg, code = r.text[:200], r.status_code
    return {"ok": False, "message": f"HTTP {r.status_code} — code {code}: {msg}"}


async def test_telegram(token: str, chat_id: str) -> dict:
    if not token:
        return {"ok": False, "message": "TELEGRAM_BOT_TOKEN mancante"}
    if token.startswith("your_"):
        return {"ok": False, "message": "Token placeholder"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        me = await client.get(f"https://api.telegram.org/bot{token}/getMe")
    if me.status_code != 200:
        return {"ok": False, "message": f"getMe fallito: HTTP {me.status_code}"}
    me_data = me.json()
    if not me_data.get("ok"):
        return {"ok": False, "message": me_data.get("description", "getMe error")}

    bot_name = me_data.get("result", {}).get("username", "?")
    result = {"ok": True, "message": f"Bot @{bot_name} valido"}

    if not chat_id:
        result["message"] += " — TELEGRAM_CHAT_ID non impostato (alert non inviabili)"
        return result

    async with httpx.AsyncClient(timeout=15.0) as client:
        chat = await client.get(
            f"https://api.telegram.org/bot{token}/getChat",
            params={"chat_id": chat_id},
        )
    if chat.status_code == 200 and chat.json().get("ok"):
        chat_type = chat.json().get("result", {}).get("type", "?")
        result["message"] += f" — chat_id OK (tipo: {chat_type})"
    else:
        desc = chat.json().get("description", chat.text[:120]) if chat.status_code == 200 else chat.text[:120]
        result["ok"] = False
        result["message"] = f"Bot OK ma chat_id invalido: {desc}"
    return result


async def test_groq(api_key: str) -> dict:
    if not api_key:
        return {"ok": False, "message": "GROQ_API_KEY mancante"}
    if api_key.startswith("your_") or api_key == "gsk_":
        return {"ok": False, "message": "Chiave placeholder o incompleta"}

    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get("https://api.groq.com/openai/v1/models", headers=headers)
    if r.status_code == 200:
        models = r.json().get("data", [])
        return {"ok": True, "message": f"API OK — {len(models)} modelli disponibili"}
    try:
        err = r.json().get("error", {}).get("message", r.text[:200])
    except Exception:
        err = r.text[:200]
    return {"ok": False, "message": f"HTTP {r.status_code}: {err}"}


async def test_local_api(admin_password: str) -> dict:
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            r = await client.post(
                "http://localhost:8000/api/login",
                data={"username": "admin", "password": admin_password},
            )
        except httpx.ConnectError:
            return {"ok": None, "message": "API Gateway non raggiungibile su :8000 (avvia docker compose)"}
    if r.status_code == 200 and r.json().get("access_token"):
        return {"ok": True, "message": "Login admin OK — JWT emesso"}
    return {"ok": False, "message": f"Login fallito HTTP {r.status_code}: {r.text[:120]}"}


async def main():
    env = load_env(ENV_PATH)
    if not env:
        print(f"ERRORE: .env non trovato in {ENV_PATH}")
        sys.exit(1)

    print("=== Test chiavi .env (valori mascherati) ===\n")
    print(f"File: {ENV_PATH}\n")

    checks = [
        ("BINANCE_API_KEY", env.get("BINANCE_API_KEY", "")),
        ("BINANCE_API_SECRET", env.get("BINANCE_API_SECRET", "")),
        ("TELEGRAM_BOT_TOKEN", env.get("TELEGRAM_BOT_TOKEN", "")),
        ("TELEGRAM_CHAT_ID", env.get("TELEGRAM_CHAT_ID", "")),
        ("GROQ_API_KEY", env.get("GROQ_API_KEY", "")),
        ("JWT_SECRET", env.get("JWT_SECRET", "")),
        ("ADMIN_PASSWORD", env.get("ADMIN_PASSWORD", "")),
    ]
    for name, val in checks:
        print(f"  {name}: {mask(val)}")

    print()

    tests = [
        ("Binance REST (account)", test_binance(
            env.get("BINANCE_API_KEY", ""),
            env.get("BINANCE_API_SECRET", ""),
        )),
        ("Telegram Bot", test_telegram(
            env.get("TELEGRAM_BOT_TOKEN", ""),
            env.get("TELEGRAM_CHAT_ID", ""),
        )),
        ("Groq API", test_groq(env.get("GROQ_API_KEY", ""))),
        ("API Gateway login", test_local_api(env.get("ADMIN_PASSWORD", ""))),
    ]

    failed = 0
    for label, coro in tests:
        result = await coro
        status = result["ok"]
        icon = "OK" if status is True else ("SKIP" if status is None else "FAIL")
        print(f"[{icon}] {label}")
        print(f"       {result['message']}\n")
        if status is False:
            failed += 1

    if env.get("JWT_SECRET") in ("", "super-secret-key", "development") and len(env.get("JWT_SECRET", "")) < 16:
        print("[WARN] JWT_SECRET debole — ok per dev, cambialo per staging/prod\n")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
