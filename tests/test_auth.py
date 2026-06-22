"""
Tests for authentication + application ownership (#38).

Register / login / me, wrong-password and invalid-token → 401, password hashing,
and ownership scoping: an applicant sees only their own applications and cannot
access another applicant's; an underwriter sees everything.
"""
from fastapi.testclient import TestClient

from lending.auth import AuthService, hash_password, verify_password, verify_token
from lending.auth.service import users_table
from lending.los import ApplicationRepository, make_engine
from lending.los.api import create_app


def _stack():
    engine = make_engine()
    repo = ApplicationRepository(engine)
    auth = AuthService(engine, "test-secret")
    # workflow_starter avoids any Temporal dependency for /start
    app = create_app(repo, auth_service=auth, workflow_starter=lambda i: f"run-{i}")
    return TestClient(app), repo, auth, engine


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Password hashing + tokens (unit)
# ---------------------------------------------------------------------------

def test_password_hash_roundtrip_and_not_plaintext():
    h = hash_password("hunter2")
    assert h != "hunter2"
    assert verify_password("hunter2", h) is True
    assert verify_password("wrong", h) is False


def test_token_verifies_and_rejects_tampering():
    tok = "abc.def"
    assert verify_token(tok, "secret") is None             # garbage
    from lending.auth import create_token

    good = create_token("u1", "applicant", "secret")
    assert verify_token(good, "secret")["sub"] == "u1"
    assert verify_token(good, "other-secret") is None      # wrong key
    assert verify_token(good + "x", "secret") is None       # tampered


def test_expired_token_is_rejected():
    from lending.auth import create_token

    expired = create_token("u1", "applicant", "secret", ttl_seconds=-10)
    assert verify_token(expired, "secret") is None


# ---------------------------------------------------------------------------
# Register / login / me
# ---------------------------------------------------------------------------

def test_register_login_me():
    client, *_ = _stack()
    reg = client.post("/auth/register", json={"email": "a@x.com", "password": "pw", "name": "Asha", "role": "applicant"})
    assert reg.status_code == 200
    token = reg.json()["token"]
    assert reg.json()["user"]["role"] == "applicant"
    assert "password" not in reg.json()["user"]

    me = client.get("/auth/me", headers=_bearer(token))
    assert me.status_code == 200
    assert me.json()["email"] == "a@x.com"

    login = client.post("/auth/login", json={"email": "a@x.com", "password": "pw"})
    assert login.status_code == 200
    assert login.json()["user"]["email"] == "a@x.com"


def test_wrong_password_is_401():
    client, *_ = _stack()
    client.post("/auth/register", json={"email": "a@x.com", "password": "pw", "name": "A", "role": "applicant"})
    bad = client.post("/auth/login", json={"email": "a@x.com", "password": "nope"})
    assert bad.status_code == 401


def test_duplicate_email_is_400():
    client, *_ = _stack()
    body = {"email": "a@x.com", "password": "pw", "name": "A", "role": "applicant"}
    assert client.post("/auth/register", json=body).status_code == 200
    assert client.post("/auth/register", json=body).status_code == 400


def test_missing_or_invalid_token_is_401():
    client, *_ = _stack()
    assert client.get("/auth/me").status_code == 401                       # missing
    assert client.get("/auth/me", headers=_bearer("garbage")).status_code == 401


def test_stored_password_is_hashed_in_db():
    client, _, _, engine = _stack()
    client.post("/auth/register", json={"email": "a@x.com", "password": "pw", "name": "A", "role": "applicant"})
    with engine.connect() as conn:
        row = conn.execute(users_table.select()).first()
    assert row.password_hash != "pw"
    assert row.password_hash.startswith("pbkdf2_sha256$")


# ---------------------------------------------------------------------------
# Ownership scoping
# ---------------------------------------------------------------------------

def _register(client, email, role):
    return client.post("/auth/register", json={"email": email, "password": "pw", "name": email, "role": role}).json()["token"]


def test_applicant_sees_only_own_applications():
    client, *_ = _stack()
    a = _register(client, "a@x.com", "applicant")
    b = _register(client, "b@x.com", "applicant")

    a_app = client.post("/applications", json={"applicant": {"full_name": "A"}}, headers=_bearer(a)).json()
    client.post("/applications", json={"applicant": {"full_name": "B"}}, headers=_bearer(b))

    a_list = client.get("/applications", headers=_bearer(a)).json()["applications"]
    assert [x["application_id"] for x in a_list] == [a_app["application_id"]]   # only A's


def test_applicant_cannot_access_other_applicants_application():
    client, *_ = _stack()
    a = _register(client, "a@x.com", "applicant")
    b = _register(client, "b@x.com", "applicant")
    a_app = client.post("/applications", json={"applicant": {"full_name": "A"}}, headers=_bearer(a)).json()

    forbidden = client.get(f"/applications/{a_app['application_id']}", headers=_bearer(b))
    assert forbidden.status_code == 403


def test_underwriter_sees_all_applications():
    client, *_ = _stack()
    a = _register(client, "a@x.com", "applicant")
    u = _register(client, "u@x.com", "underwriter")
    a_app = client.post("/applications", json={"applicant": {"full_name": "A"}}, headers=_bearer(a)).json()

    u_list = client.get("/applications", headers=_bearer(u)).json()["applications"]
    assert a_app["application_id"] in [x["application_id"] for x in u_list]
    # and can open it
    assert client.get(f"/applications/{a_app['application_id']}", headers=_bearer(u)).status_code == 200
