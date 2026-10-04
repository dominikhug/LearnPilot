from learnpilot import auth


def login(client, password):
    return client.post("/api/auth/login", json={"password": password})


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_me_requires_login(client):
    assert client.get("/api/auth/me").status_code == 401


def test_login_sets_session_and_logout_clears_it(client):
    response = login(client, "correct horse")
    assert response.status_code == 204
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie

    assert client.get("/api/auth/me").json() == {"id": 1, "name": "default"}

    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_wrong_password_is_rejected(client):
    assert login(client, "wrong").status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_unset_password_never_matches(client, monkeypatch):
    monkeypatch.setattr(auth.settings, "app_password", "")
    assert login(client, "").status_code == 401


def test_five_failures_lock_out_even_the_right_password(client, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: now[0])

    for _ in range(4):
        assert login(client, "wrong").status_code == 401
    assert login(client, "wrong").status_code == 401  # fifth failure starts the lockout

    locked = login(client, "correct horse")
    assert locked.status_code == 429
    assert locked.headers["retry-after"] == "60"

    now[0] += 59
    assert login(client, "correct horse").status_code == 429

    now[0] += 1
    assert login(client, "correct horse").status_code == 204


def test_success_resets_failure_count(client):
    for _ in range(4):
        login(client, "wrong")
    assert login(client, "correct horse").status_code == 204
    for _ in range(4):
        assert login(client, "wrong").status_code == 401
    assert login(client, "correct horse").status_code == 204
