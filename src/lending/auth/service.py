"""
Auth service — user store (persisted) + register / login / token verification.

Users live in their own table on the shared engine. Roles: applicant | underwriter.
(In production underwriters would be provisioned, not self-registered; for the
demo, role is chosen at registration.)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, DateTime, MetaData, String, Table, select
from sqlalchemy.engine import Engine

from .security import create_token, hash_password, verify_password, verify_token

ROLES = ("applicant", "underwriter")

_metadata = MetaData()
users_table = Table(
    "users",
    _metadata,
    Column("user_id", String, primary_key=True),
    Column("email", String, unique=True, nullable=False),
    Column("password_hash", String, nullable=False),
    Column("role", String, nullable=False),
    Column("name", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


class AuthError(Exception):
    """Invalid credentials / registration error."""


@dataclass(frozen=True)
class User:
    user_id: str
    email: str
    role: str
    name: str

    def public(self) -> dict:
        return {"user_id": self.user_id, "email": self.email, "role": self.role, "name": self.name}


class AuthService:
    def __init__(self, engine: Engine, secret: str) -> None:
        self._engine = engine
        self._secret = secret
        _metadata.create_all(engine)

    # ---- registration / login ----
    def register(self, email: str, password: str, name: str, role: str) -> tuple[User, str]:
        email = (email or "").strip().lower()
        if not email or not password:
            raise AuthError("email and password are required")
        if role not in ROLES:
            raise AuthError(f"invalid role: {role!r}")
        if self._get_row_by_email(email) is not None:
            raise AuthError("email already registered")
        user = User(user_id=uuid4().hex, email=email, role=role, name=name or email)
        with self._engine.begin() as conn:
            conn.execute(users_table.insert().values(
                user_id=user.user_id, email=email, password_hash=hash_password(password),
                role=role, name=user.name, created_at=datetime.now(timezone.utc),
            ))
        return user, self._issue(user)

    def login(self, email: str, password: str) -> tuple[User, str]:
        row = self._get_row_by_email((email or "").strip().lower())
        if row is None or not verify_password(password, row.password_hash):
            raise AuthError("invalid email or password")
        user = _to_user(row)
        return user, self._issue(user)

    def user_from_token(self, token: str) -> User | None:
        payload = verify_token(token, self._secret)
        if not payload:
            return None
        return self.get_by_id(payload["sub"])

    # ---- lookups ----
    def get_by_id(self, user_id: str) -> User | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(users_table).where(users_table.c.user_id == user_id)).first()
        return _to_user(row) if row else None

    def _get_row_by_email(self, email: str):
        with self._engine.connect() as conn:
            return conn.execute(select(users_table).where(users_table.c.email == email)).first()

    def _issue(self, user: User) -> str:
        return create_token(user.user_id, user.role, self._secret)


def _to_user(row) -> User:
    return User(user_id=row.user_id, email=row.email, role=row.role, name=row.name)
