"""Verification of OpenID Connect ID tokens (RS256 only).

Only what single sign-on needs: check the signature against the provider's published keys, then the claims that
make the token valid for this sign-in (issuer, audience, expiry, nonce). Anything unexpected is refused.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

MIN_KEY_BITS = 2048
MAX_TOKEN_BYTES = 16_000


def b64url(data: str) -> bytes:
    try: return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except Exception: raise ValueError("The token is not valid base64")


def _json(part: str, what: str) -> dict:
    try: value = json.loads(b64url(part))
    except ValueError: raise ValueError(f"The token {what} is not valid JSON")
    if not isinstance(value, dict): raise ValueError(f"The token {what} is not an object")
    return value


def _public_key(jwk: dict) -> rsa.RSAPublicKey:
    try:
        n, e = int.from_bytes(b64url(jwk["n"]), "big"), int.from_bytes(b64url(jwk["e"]), "big")
        key = rsa.RSAPublicNumbers(e, n).public_key()
    except (KeyError, TypeError, ValueError): raise ValueError("A signing key from the provider is malformed")
    if key.key_size < MIN_KEY_BITS: raise ValueError("A signing key from the provider is too weak")
    return key


def verify_id_token(token: str, keys: list[dict], *, issuer: str, client_id: str, nonce: str, now: float | None = None, leeway: int = 60) -> dict:
    """Return the claims of a valid ID token, or raise ValueError saying why it is not valid."""
    if not isinstance(token, str) or len(token) > MAX_TOKEN_BYTES: raise ValueError("The ID token is missing or too large")
    parts = token.split(".")
    if len(parts) != 3: raise ValueError("The ID token is not a signed token")
    header, claims = _json(parts[0], "header"), _json(parts[1], "payload")
    if header.get("alg") != "RS256": raise ValueError(f"Unsupported signing algorithm {str(header.get('alg'))[:20]!r} (only RS256 is accepted)")
    kid = header.get("kid")
    candidates = [k for k in keys if isinstance(k, dict) and k.get("kty") == "RSA" and k.get("use", "sig") == "sig" and (not kid or k.get("kid") == kid)]
    if not candidates: raise ValueError("signing key not found")
    signature, signed = b64url(parts[2]), (parts[0] + "." + parts[1]).encode("ascii")
    valid = False
    for jwk in candidates[:5]:
        try: _public_key(jwk).verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature: continue
        valid = True
        break
    if not valid: raise ValueError("The ID token signature is not valid")

    moment = time.time() if now is None else now
    if claims.get("iss") != issuer: raise ValueError("The ID token comes from a different issuer")
    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    if client_id not in audiences: raise ValueError("The ID token was not issued for this application")
    if len(audiences) > 1 and claims.get("azp") != client_id: raise ValueError("The ID token has several audiences and is not authorised for this application")
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool) or exp + leeway < moment: raise ValueError("The ID token has expired")
    for field in ("iat", "nbf"):
        if field in claims:
            v = claims[field]
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v - leeway > moment: raise ValueError(f"The ID token is not valid yet ({field})")
    if not nonce or claims.get("nonce") != nonce: raise ValueError("The ID token does not match this sign-in attempt")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]: raise ValueError("The ID token has no subject")
    return claims
