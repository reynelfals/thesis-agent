from __future__ import annotations

from pathlib import Path

import pytest

from thesis.config import ConfigError, Settings
from thesis.research.alpaca_probe import ProbeError, _credentials


def _clear_account_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "THESIS_ACCOUNT_PROFILE",
        "DEV_APCA_API_KEY_ID",
        "DEV_APCA_API_SECRET_KEY",
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_account_profile_is_mandatory_and_strict(monkeypatch) -> None:
    _clear_account_environment(monkeypatch)

    with pytest.raises(ConfigError, match="missing THESIS_ACCOUNT_PROFILE"):
        Settings()

    monkeypatch.setenv("THESIS_ACCOUNT_PROFILE", "staging")
    with pytest.raises(ConfigError, match="development, judge"):
        Settings()


def test_every_profile_refuses_non_paper_base_url(monkeypatch) -> None:
    _clear_account_environment(monkeypatch)
    monkeypatch.setenv("THESIS_ACCOUNT_PROFILE", "development")
    monkeypatch.setenv("DEV_APCA_API_KEY_ID", "development-key")
    monkeypatch.setenv("DEV_APCA_API_SECRET_KEY", "development-secret")
    monkeypatch.setenv("APCA_API_BASE_URL", "https://api.alpaca.markets")

    with pytest.raises(ConfigError, match="base URL must be"):
        Settings().assert_paper()


def test_development_credentials_never_fall_back_to_judge(monkeypatch) -> None:
    _clear_account_environment(monkeypatch)
    monkeypatch.setenv("THESIS_ACCOUNT_PROFILE", "development")
    monkeypatch.setenv("APCA_API_KEY_ID", "judge-key-must-not-be-used")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "judge-secret-must-not-be-used")

    with pytest.raises(ConfigError, match="DEV_APCA_API_KEY_ID"):
        Settings()
    with pytest.raises(ProbeError, match="DEV_APCA_API_KEY_ID"):
        _credentials()


@pytest.mark.parametrize(
    ("profile", "key_name", "secret_name", "label"),
    (
        (
            "development",
            "DEV_APCA_API_KEY_ID",
            "DEV_APCA_API_SECRET_KEY",
            "Development paper account",
        ),
        (
            "judge",
            "APCA_API_KEY_ID",
            "APCA_API_SECRET_KEY",
            "Judge paper account",
        ),
    ),
)
def test_profile_selects_only_its_credentials_and_database(
    monkeypatch,
    profile,
    key_name,
    secret_name,
    label,
) -> None:
    _clear_account_environment(monkeypatch)
    monkeypatch.delenv("THESIS_DB", raising=False)
    monkeypatch.setenv("THESIS_ACCOUNT_PROFILE", profile)
    monkeypatch.setenv(key_name, f"{profile}-key")
    monkeypatch.setenv(secret_name, f"{profile}-secret")

    settings = Settings()

    assert settings.api_key == f"{profile}-key"
    assert settings.secret_key == f"{profile}-secret"
    assert settings.account_profile_label == label
    assert settings.db_path.name == f"{profile}-thesis.sqlite"


@pytest.mark.parametrize(
    ("profile", "key_name", "secret_name", "override"),
    (
        (
            "development",
            "DEV_APCA_API_KEY_ID",
            "DEV_APCA_API_SECRET_KEY",
            "data/judge-thesis.sqlite",
        ),
        (
            "judge",
            "APCA_API_KEY_ID",
            "APCA_API_SECRET_KEY",
            "data/development-thesis.sqlite",
        ),
        (
            "development",
            "DEV_APCA_API_KEY_ID",
            "DEV_APCA_API_SECRET_KEY",
            "data/arbitrary.sqlite",
        ),
        (
            "judge",
            "APCA_API_KEY_ID",
            "APCA_API_SECRET_KEY",
            "/tmp/arbitrary.sqlite",
        ),
    ),
)
def test_profile_rejects_cross_profile_and_arbitrary_database_overrides(
    monkeypatch,
    profile,
    key_name,
    secret_name,
    override,
) -> None:
    _clear_account_environment(monkeypatch)
    monkeypatch.setenv("THESIS_ACCOUNT_PROFILE", profile)
    monkeypatch.setenv(key_name, f"{profile}-key")
    monkeypatch.setenv(secret_name, f"{profile}-secret")
    monkeypatch.setenv("THESIS_DB", override)

    with pytest.raises(ConfigError, match="THESIS_DB must resolve to"):
        Settings()


@pytest.mark.parametrize("profile", ("development", "judge"))
def test_profile_accepts_equivalent_correct_database_paths(
    monkeypatch,
    profile,
) -> None:
    _clear_account_environment(monkeypatch)
    key_name = (
        "DEV_APCA_API_KEY_ID" if profile == "development" else "APCA_API_KEY_ID"
    )
    secret_name = (
        "DEV_APCA_API_SECRET_KEY"
        if profile == "development"
        else "APCA_API_SECRET_KEY"
    )
    monkeypatch.setenv("THESIS_ACCOUNT_PROFILE", profile)
    monkeypatch.setenv(key_name, f"{profile}-key")
    monkeypatch.setenv(secret_name, f"{profile}-secret")
    monkeypatch.setenv(
        "THESIS_DB",
        str(Path(__file__).resolve().parents[1] / "data" / f"{profile}-thesis.sqlite"),
    )

    settings = Settings()

    assert settings.db_path == (
        Path(__file__).resolve().parents[1]
        / "data"
        / f"{profile}-thesis.sqlite"
    ).resolve()


def test_judge_credentials_never_fall_back_to_development(monkeypatch) -> None:
    _clear_account_environment(monkeypatch)
    monkeypatch.setenv("THESIS_ACCOUNT_PROFILE", "judge")
    monkeypatch.setenv("DEV_APCA_API_KEY_ID", "dev-key-must-not-be-used")
    monkeypatch.setenv("DEV_APCA_API_SECRET_KEY", "dev-secret-must-not-be-used")

    with pytest.raises(ConfigError, match="APCA_API_KEY_ID"):
        Settings()
    with pytest.raises(ProbeError, match="APCA_API_KEY_ID"):
        _credentials()


def test_launch_commands_pin_separate_profiles_and_databases() -> None:
    project = Path(__file__).resolve().parents[1]
    root = project.parent
    replit = (root / ".replit").read_text()
    production = (project / "scripts" / "run_production.sh").read_text()

    assert replit.count("THESIS_ACCOUNT_PROFILE=development") == 2
    assert replit.count("THESIS_DB=data/development-thesis.sqlite") == 2
    assert "judge-thesis.sqlite" not in replit
    assert "[userenv.shared]" not in replit

    assert production.count("THESIS_ACCOUNT_PROFILE=judge") == 2
    assert production.count("THESIS_DB=data/judge-thesis.sqlite") == 2
    assert production.count("THESIS_ALLOW_EXECUTE=0") == 2
    assert "development-thesis.sqlite" not in production