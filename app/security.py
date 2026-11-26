import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt

from .config import settings


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"{salt.hex()}:{h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, h_hex = stored.split(":")
        h = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
        return hmac.compare_digest(h.hex(), h_hex)
    except Exception:
        return False


def make_token(user_id: str, tenant_id: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    return jwt.encode({"sub": user_id, "tid": tenant_id, "exp": exp}, settings.jwt_secret, "HS256")


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


# ---------- regras de cadastro ----------
SPECIAL = set("!@#$%^&*()-_=+[]{};:'\",.<>/?|`~\\")


def password_problems(p: str) -> list[str]:
    """O que falta para a senha ser aceita (vazio = senha boa)."""
    out = []
    if len(p) < 8:
        out.append("ter pelo menos 8 caracteres")
    if not any(c.isupper() for c in p):
        out.append("ter uma letra maiúscula")
    if not any(c in SPECIAL for c in p):
        out.append("ter um caractere especial (ex.: ! @ # $ -)")
    return out


def only_digits(s: str) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


def valid_cpf(d: str) -> bool:
    if len(d) != 11 or d == d[0] * 11:
        return False
    for n in (9, 10):
        s = sum(int(d[i]) * (n + 1 - i) for i in range(n))
        if (s * 10 % 11) % 10 != int(d[n]):
            return False
    return True


def valid_cnpj(d: str) -> bool:
    if len(d) != 14 or d == d[0] * 14:
        return False
    for n, w in ((12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]), (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])):
        r = sum(int(d[i]) * w[i] for i in range(n)) % 11
        if (0 if r < 2 else 11 - r) != int(d[n]):
            return False
    return True
