# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import hmac
import secrets
import time


def now_ms() -> int:
    return int(time.time() * 1000)


def random_id(prefix: str) -> str:
    clean_prefix = (prefix or "id").strip("_") or "id"
    return f"{clean_prefix}_{secrets.token_urlsafe(12).replace('-', '').replace('_', '')}"


def generate_pairing_token() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def generate_auth_token() -> str:
    return f"audora_{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def token_matches(token: str, token_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), token_hash or "")
