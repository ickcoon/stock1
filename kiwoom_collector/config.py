"""Load local credentials without exposing their values."""
from dataclasses import dataclass, field
from pathlib import Path
import os

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Credentials:
    app_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    environment: str = "mock"

    def __post_init__(self) -> None:
        if self.environment not in {"mock", "real"}:
            raise ValueError("KIWOOM_ENV는 mock 또는 real이어야 합니다.")

    @property
    def base_url(self) -> str:
        return {"mock": "https://mockapi.kiwoom.com", "real": "https://api.kiwoom.com"}[self.environment]


def load_credentials(env_file: Path | None = None) -> Credentials:
    # Existing process bindings take precedence; never print credential values.
    load_dotenv(env_file if env_file is not None else PROJECT_ROOT / ".env", override=False, encoding="utf-8-sig")
    environment = os.environ.get("KIWOOM_ENV", "mock").strip()
    if environment not in {"mock", "real"}:
        raise ValueError("KIWOOM_ENV는 mock 또는 real이어야 합니다.")
    names = ("KIWOOM_APP_KEY", "KIWOOM_SECRET_KEY")
    values = [os.environ.get(name, "").strip() for name in names]
    missing = [name for name, value in zip(names, values) if not value]
    if missing:
        raise ValueError(".env에 다음 항목을 입력하세요: " + ", ".join(missing))
    return Credentials(*values, environment=environment)
