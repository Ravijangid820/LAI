"""bcrypt password hashing.

Thin wrapper around :mod:`passlib` so the rest of the codebase never
imports passlib directly. Encapsulates two design decisions:

1. **Algorithm pinning.** ``passlib.context.CryptContext`` is created
   with a single scheme (``bcrypt``) and ``deprecated="auto"``. There
   is no "legacy hash format" — every row stored uses the current
   scheme, full stop. If we ever add Argon2 later, this is the one
   place that changes.
2. **Work factor centralisation.** The cost (rounds) comes from
   :class:`AuthConfig.bcrypt_rounds`. We do not embed the constant in
   call sites — that makes raising the floor during the next OWASP
   refresh a one-line config change instead of a code change.

The module is intentionally side-effect-free at import: the
:class:`CryptContext` is built lazily by :class:`PasswordHasher` so
test harnesses can construct one per-test with a low cost.
"""

from __future__ import annotations

from passlib.context import CryptContext

from lai.common.auth.config import AuthConfig

__all__ = ["PasswordHasher"]


class PasswordHasher:
    """bcrypt hash + verify with a pinned work factor.

    Construct once at app startup and share read-only — internally the
    :class:`CryptContext` is stateless after configuration, so the
    instance is safe to call concurrently from any thread.

    Args:
        config: Auth configuration. Reads :attr:`AuthConfig.bcrypt_rounds`
            and nothing else; the rest of the config is irrelevant here.
    """

    __slots__ = ("_context",)

    def __init__(self, config: AuthConfig) -> None:
        self._context: CryptContext = CryptContext(
            schemes=["bcrypt"],
            deprecated="auto",
            bcrypt__rounds=config.bcrypt_rounds,
        )

    def hash(self, plain: str) -> str:
        """Hash a plaintext password.

        Args:
            plain: The user-supplied password. Caller is responsible
                for length-bounds checking (see :class:`AuthConfig`).

        Returns:
            The bcrypt hash string (``$2b$<rounds>$<salt><digest>``),
            suitable for storage in ``users.password_hash``.
        """
        return str(self._context.hash(plain))

    def verify(self, plain: str, hashed: str) -> bool:
        """Constant-time-ish verification of plain against an existing hash.

        Returns ``False`` for *any* failure mode (mismatch, malformed
        hash, unknown scheme) so callers cannot distinguish "wrong
        password" from "corrupt row" via timing or exception type.

        Args:
            plain: The submitted password.
            hashed: The stored hash (typically ``users.password_hash``).

        Returns:
            ``True`` if the password matches the hash, ``False`` otherwise.
        """
        try:
            return bool(self._context.verify(plain, hashed))
        except (ValueError, TypeError):
            # Malformed or unrecognised hash payload. Treat as a miss;
            # never raise into the auth router — that would let an
            # attacker discriminate accounts whose hashes were corrupted.
            return False

    def needs_rehash(self, hashed: str) -> bool:
        """Whether ``hashed`` should be re-hashed at next login.

        ``True`` after the configured ``bcrypt_rounds`` has been raised
        (e.g., during the next OWASP cost-floor bump). The auth router
        opportunistically re-hashes on successful login so the floor
        ratchets up without a mass-rotation event.

        Args:
            hashed: An existing stored hash.

        Returns:
            ``True`` if a re-hash with the current cost is recommended.
        """
        return bool(self._context.needs_update(hashed))
