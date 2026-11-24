from datetime import UTC, datetime
from hashlib import sha256
import json
from uuid import UUID

from sqlalchemy import select

from .db import Root
from .filesystem import AuthorizedRoot


class Problem(Exception):
    def __init__(self, status: int, code: str, message: str, **information):
        super().__init__(message)
        self.status = status
        self.detail = {"code": code, "message": message, **information}


def digest(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                             allow_nan=False).encode("utf-8")).hexdigest()


def path_key(path: str) -> str:
    # The filesystem supplies canonical spellings. Unicode casefold can merge different NTFS names.
    return digest({"path": path.replace("\\", "/")})


def now():
    return datetime.now(UTC)


def require(session, entity, key: UUID | str, *, lock=False):
    key = UUID(key) if isinstance(key, str) else key
    if lock:
        result = session.scalar(select(entity).where(entity.id == key).with_for_update())
    else:
        result = session.get(entity, key)
    if result is None:
        raise Problem(404, "not_found", "The requested record could not be found.")
    return result


def authorized(row: Root) -> AuthorizedRoot:
    return AuthorizedRoot(row.path, row.device, row.inode)
