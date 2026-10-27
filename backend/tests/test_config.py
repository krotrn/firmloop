import pytest

from firmloop.config import Settings

DEFAULT = ["http://localhost:3000", "http://127.0.0.1:3000"]


def test_default_frontend_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRONTEND_ORIGINS", raising=False)
    assert Settings(_env_file=None).frontend_origins == DEFAULT  # pyright: ignore[reportCallIssue]


def test_frontend_origins_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRONTEND_ORIGINS", "http://a.example:3000, http://b.example:3000")
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert settings.frontend_origins == ["http://a.example:3000", "http://b.example:3000"]


def test_frontend_origins_single(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRONTEND_ORIGINS", "http://a.example:3000")
    assert Settings(_env_file=None).frontend_origins == ["http://a.example:3000"]  # pyright: ignore[reportCallIssue]
