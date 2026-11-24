import os
from dataclasses import dataclass

from sqlalchemy.engine import URL, make_url


def postgres_url(value: str | URL) -> URL:
    url = make_url(value)
    if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
        raise ValueError("FileMate requires PostgreSQL using psycopg.")
    return url.set(drivername="postgresql+psycopg")


@dataclass(frozen=True)
class Settings:
    database_url: str | URL
    tool_token: str
    admin_token: str

    def __post_init__(self):
        postgres_url(self.database_url)
        if not self.tool_token.strip() or not self.admin_token.strip() or self.tool_token == self.admin_token:
            raise ValueError("FILEMATE_TOOL_TOKEN and FILEMATE_ADMIN_TOKEN must be distinct and nonempty.")

    @classmethod
    def from_env(cls):
        return cls(os.environ["FILEMATE_DATABASE_URL"], os.environ["FILEMATE_TOOL_TOKEN"],
                   os.environ["FILEMATE_ADMIN_TOKEN"])
