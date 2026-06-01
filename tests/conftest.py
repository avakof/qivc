import pytest

from qivc.config import Settings


def pytest_configure(config: pytest.Config) -> None:
    pass


@pytest.fixture
def settings(tmp_path: pytest.TempPathFactory) -> Settings:
    """Return a Settings instance with a temp-dir DB path, no real .env required."""
    return Settings(
        edgar_user_agent="Test User test@example.com",
        db_path=str(tmp_path) + "/qivc.db",  # type: ignore[arg-type]
        output_dir=str(tmp_path) + "/runs",  # type: ignore[arg-type]
    )
