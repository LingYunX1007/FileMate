"""Windows path validation and handle-based, non-replacing file moves."""

import ctypes
import hashlib
import os
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
from pathlib import Path
import re
import stat
from typing import BinaryIO


class FileProblem(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AuthorizedRoot:
    path: str
    device: str
    inode: str


@dataclass(frozen=True)
class Directory:
    path: Path
    handle: int
    device: str
    inode: str


@dataclass
class OpenFile:
    stream: BinaryIO
    handle: int


_WINDOWS = os.name == "nt"
_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])\Z", re.I)
_INVALID = re.compile(r'[<>:"|?*\x00-\x1f]')


def relative_parts(path: str) -> tuple[str, ...]:
    parts = tuple(path.replace("\\", "/").split("/"))
    for part in parts:
        if (not part or part in {".", ".."} or part.endswith((" ", "."))
                or _INVALID.search(part) or _RESERVED.fullmatch(part.split(".")[0].rstrip(" "))
                or len(part.encode("utf-16-le", errors="surrogatepass")) > 510
                or any(0xD800 <= ord(character) <= 0xDFFF for character in part)):
            raise FileProblem("invalid_path", "Use a relative path with valid Windows names.")
    return parts


def normalized_relative(path: str) -> str:
    return "/".join(relative_parts(path))


def _os_problem(exc: OSError) -> FileProblem:
    if isinstance(exc, FileNotFoundError):
        return FileProblem("missing", "The path no longer exists.")
    if isinstance(exc, FileExistsError):
        return FileProblem("target_exists", "The destination already exists.")
    if isinstance(exc, (PermissionError, IsADirectoryError, NotADirectoryError)):
        return FileProblem("path_unavailable", "The path is locked, inaccessible, or has the wrong type.")
    return FileProblem("filesystem_error", f"Filesystem operation failed (error {exc.winerror if _WINDOWS else exc.errno}).")


if _WINDOWS:
    from ctypes import wintypes
    import msvcrt

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _create = _kernel.CreateFileW
    _create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    _create.restype = wintypes.HANDLE
    _close = _kernel.CloseHandle
    _close.argtypes = (wintypes.HANDLE,)
    _close.restype = wintypes.BOOL
    _final_name = _kernel.GetFinalPathNameByHandleW
    _final_name.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
    _final_name.restype = wintypes.DWORD
    _drive_type = _kernel.GetDriveTypeW
    _drive_type.argtypes = (wintypes.LPCWSTR,)
    _drive_type.restype = wintypes.UINT
    _compare_ordinal = _kernel.CompareStringOrdinal
    _compare_ordinal.argtypes = (wintypes.LPCWSTR, ctypes.c_int, wintypes.LPCWSTR, ctypes.c_int, wintypes.BOOL)
    _compare_ordinal.restype = ctypes.c_int

    class _FileInformation(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("created", wintypes.FILETIME),
                    ("accessed", wintypes.FILETIME), ("modified", wintypes.FILETIME),
                    ("device", wintypes.DWORD), ("size_high", wintypes.DWORD),
                    ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
                    ("inode_high", wintypes.DWORD), ("inode_low", wintypes.DWORD)]

    _file_information = _kernel.GetFileInformationByHandle
    _file_information.argtypes = (wintypes.HANDLE, ctypes.POINTER(_FileInformation))
    _file_information.restype = wintypes.BOOL
    _rename_information = _kernel.SetFileInformationByHandle
    _rename_information.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    _rename_information.restype = wintypes.BOOL
    _invalid_handle = ctypes.c_void_p(-1).value


def _native(path: Path) -> str:
    return "\\\\?\\" + str(path) if _WINDOWS else str(path)


def paths_equal(left: str, right: str) -> bool:
    if left == right:
        return True
    if not _WINDOWS or len(left) != len(right):
        return False
    # Use the OS uppercase table without linguistic Unicode expansions such as ß → ss.
    comparison = _compare_ordinal(left, -1, right, -1, 1)
    if comparison == 0:
        raise FileProblem("unsafe_path", "The path could not be compared safely.")
    return comparison == 2


def _win_info(handle: int):
    info = _FileInformation()
    if not _file_information(handle, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return info


def _win_canonical(handle: int) -> str:
    canonical = ctypes.create_unicode_buffer(32768)
    length = _final_name(handle, canonical, len(canonical), 0)
    if not length or length >= len(canonical):
        raise FileProblem("unsafe_path", "The canonical path could not be verified.")
    return canonical.value


def _windows_open(path: Path, access: int, sharing: int) -> int:
    # OPEN_EXISTING and OPEN_REPARSE_POINT. The latter inspects the final entry itself.
    handle = _create(_native(path), access, sharing, None, 3, 0x02200000, None)
    if handle == _invalid_handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = _win_info(handle)
        if info.attributes & 0x400:
            raise FileProblem("unsafe_path", "Symlinks, junctions and other reparse points are blocked.")
        if os.path.normcase(_win_canonical(handle)) != os.path.normcase(_native(path)):
            raise FileProblem("unsafe_path", "The path resolves through an alias.")
    except BaseException:
        _close(handle)
        raise
    return handle


def _metadata(handle: int) -> tuple[bool, int, str, str, int, int]:
    if _WINDOWS:
        info = _win_info(handle)
        inode = (info.inode_high << 32) | info.inode_low
        size = (info.size_high << 32) | info.size_low
        ticks = (info.modified.dwHighDateTime << 32) | info.modified.dwLowDateTime
        return bool(info.attributes & 0x10), info.links, str(info.device), str(inode), size, (
            ticks - 116444736000000000
        ) * 100
    info = os.fstat(handle)
    return stat.S_ISDIR(info.st_mode), info.st_nlink, str(info.st_dev), str(info.st_ino), (
        info.st_size
    ), info.st_mtime_ns


@contextmanager
def _open_directory(path: Path, parent: Directory | None):
    try:
        if _WINDOWS:
            # Deny delete sharing, so the directory cannot be renamed or swapped while held.
            handle = _windows_open(path, 0x80, 0x3)
        else:
            handle = os.open(path if parent is None else path.name,
                             os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                             dir_fd=None if parent is None else parent.handle)
    except OSError as exc:
        raise _os_problem(exc) from exc
    try:
        is_dir, _, device, inode, _, _ = _metadata(handle)
        if not is_dir:
            raise FileProblem("unsafe_path", "The path component is not a directory.")
        yield Directory(path, handle, device, inode)
    finally:
        (_close if _WINDOWS else os.close)(handle)


def _absolute(root_path: str) -> Path:
    if not root_path or "\x00" in root_path or len(root_path) > 32000:
        raise FileProblem("invalid_path", "Enter a valid absolute local directory path.")
    if _WINDOWS:
        path = root_path.replace("\\", "/")
        if not re.match(r"^[A-Za-z]:/", path):
            raise FileProblem("invalid_path", "Use an absolute local drive path.")
        tail = path[3:].removesuffix("/")
        if tail:
            relative_parts(tail)
        if _drive_type(path[:3].replace("/", "\\")) not in {2, 3}:
            raise FileProblem("invalid_path", "Use a local fixed or removable drive.")
        return Path(path[:3] + tail)
    if not root_path.startswith("/") or root_path.startswith("//"):
        raise FileProblem("invalid_path", "Enter a valid absolute local directory path.")
    tail = root_path[1:].removesuffix("/")
    if tail:
        relative_parts(tail)
    return Path("/" + tail)


@contextmanager
def _walk(root_path: Path, root: AuthorizedRoot | None, parts=(), *, create=False, allow_missing=False):
    with ExitStack() as stack:
        current = stack.enter_context(_open_directory(Path(root_path.anchor), None))
        for part in root_path.parts[1:]:
            current = stack.enter_context(_open_directory(current.path / part, current))
        if root is not None and (current.device != root.device or current.inode != root.inode):
            raise FileProblem("root_changed", "The authorized root directory has been replaced.")

        for part in parts:
            next_path = current.path / part
            try:
                next_directory = stack.enter_context(_open_directory(next_path, current))
            except FileProblem as exc:
                if exc.code != "missing" or not (create or allow_missing):
                    raise
                if not create:
                    yield current, False
                    return
                try:
                    if _WINDOWS:
                        os.mkdir(_native(next_path))
                    else:
                        os.mkdir(part, dir_fd=current.handle)
                except FileExistsError:
                    pass
                except OSError as error:
                    raise _os_problem(error) from error
                next_directory = stack.enter_context(_open_directory(next_path, current))
            if next_directory.device != current.device:
                raise FileProblem("unsafe_path", "Mounted folders inside an authorized path are blocked.")
            current = next_directory
        yield current, True


def authorize_root(path: str) -> AuthorizedRoot:
    root_path = _absolute(path)
    with _walk(root_path, None) as (directory, _):
        canonical = _win_canonical(directory.handle)[4:] if _WINDOWS else str(root_path)
        return AuthorizedRoot(canonical, directory.device, directory.inode)


@contextmanager
def _parent(root: AuthorizedRoot, relative: str, *, create=False, allow_missing=False):
    *parts, filename = relative_parts(relative)
    with _walk(Path(root.path), root, parts, create=create, allow_missing=allow_missing) as (directory, exists):
        yield directory, filename, exists


@contextmanager
def _open_file(directory: Directory, name: str, *, for_move=False):
    descriptor = None
    try:
        if _WINDOWS:
            # Share read only, so no other handle can write to or rename this source.
            path = directory.path / name
            handle = _windows_open(path, 0x80010000 if for_move else 0x80000000, 1)
            try:
                descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            except BaseException:
                _close(handle)
                raise
        else:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory.handle)
            handle = descriptor
        is_dir, links, device, inode, _, _ = _metadata(handle)
        if (is_dir or links != 1 or device != directory.device or inode == "0"
                or (not _WINDOWS and not stat.S_ISREG(os.fstat(handle).st_mode))):
            raise FileProblem("unsafe_file", "Use ordinary files without hard links inside the authorized root.")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            yield OpenFile(stream, handle)
    except OSError as exc:
        raise _os_problem(exc) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _fingerprint(file: OpenFile) -> dict:
    before = _metadata(file.handle)
    file.stream.seek(0)
    digest = hashlib.file_digest(file.stream, "sha256").hexdigest()
    if _metadata(file.handle) != before:
        raise FileProblem("source_changed", "The file changed while it was being read.")
    _, _, device, inode, size, mtime_ns = before
    return {"device": device, "inode": inode, "size": size, "mtime_ns": mtime_ns,
            "sha256": digest}


@contextmanager
def read_file(root: AuthorizedRoot, relative: str):
    with _parent(root, relative) as (directory, name, _), _open_file(directory, name) as file:
        yield file


def fingerprint(root: AuthorizedRoot, relative: str) -> dict:
    with read_file(root, relative) as file:
        return _fingerprint(file)


def _exists(directory: Directory, filename: str) -> bool:
    try:
        if _WINDOWS:
            os.lstat(_native(directory.path / filename))
        else:
            os.stat(filename, dir_fd=directory.handle, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _os_problem(exc) from exc
    return True


def check_move(source_root: AuthorizedRoot, source: str, target_root: AuthorizedRoot,
               target: str, expected: dict, *, check_source=True) -> None:
    if check_source and fingerprint(source_root, source) != expected:
        raise FileProblem("source_changed", "The source file changed since this plan version was created.")
    with _parent(target_root, target, allow_missing=True) as (directory, name, exists):
        if directory.device != expected["device"]:
            raise FileProblem("cross_volume", "Moves must stay on the same drive.")
        if exists and _exists(directory, name):
            raise FileProblem("target_exists", "The destination already exists.")


def _rename(file: OpenFile, parent: Directory, source: str, target_dir: Directory, target: str):
    if _WINDOWS:
        # Destination lineage stays open without delete sharing until the operation returns.
        name = _native(target_dir.path / target).encode("utf-16-le")

        class RenameInfo(ctypes.Structure):
            _fields_ = [("flags", wintypes.DWORD), ("root", wintypes.HANDLE),
                        ("length", wintypes.DWORD), ("name", ctypes.c_ubyte * (len(name) + 2))]

        info = RenameInfo()
        info.length = len(name)
        ctypes.memmove(ctypes.addressof(info) + RenameInfo.name.offset, name, len(name))
        if not _rename_information(file.handle, 3, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return

    # Linux offers a single non-replacing rename bound to the open parent directories.
    libc = ctypes.CDLL(None, use_errno=True)
    rename_at = getattr(libc, "renameat2", None)
    if rename_at is None:
        raise FileProblem("unsupported_platform", "This platform lacks a non-replacing rename operation.")
    current = os.stat(source, dir_fd=parent.handle, follow_symlinks=False)
    opened = os.fstat(file.handle)
    if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
        raise FileProblem("source_changed", "The source entry changed during execution.")
    rename_at.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename_at.restype = ctypes.c_int
    if rename_at(parent.handle, os.fsencode(source), target_dir.handle, os.fsencode(target), 1):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def move(source_root: AuthorizedRoot, source: str, target_root: AuthorizedRoot,
         target: str, expected: dict, *, before_write=None) -> None:
    check_move(source_root, source, target_root, target, expected, check_source=False)
    with _parent(source_root, source) as (parent, name, _), _open_file(parent, name, for_move=True) as file:
        if _fingerprint(file) != expected:
            raise FileProblem("source_changed", "The source file changed since this plan version was created.")
        if before_write is not None:
            before_write()
        with _parent(target_root, target, create=True) as (target_dir, target_name, _):
            if target_dir.device != expected["device"]:
                raise FileProblem("cross_volume", "Moves must stay on the same drive.")
            if _exists(target_dir, target_name):
                raise FileProblem("target_exists", "The destination already exists.")
            if before_write is not None:
                before_write()
            _rename(file, parent, name, target_dir, target_name)
    if recover(source_root, source, target_root, target, expected) != "applied":
        raise FileProblem("verify_failed", "The move could not be verified after execution.")


def recover(source_root: AuthorizedRoot, source: str, target_root: AuthorizedRoot,
            target: str, expected: dict) -> str:
    def observed(root, path):
        try:
            return fingerprint(root, path)
        except FileProblem as exc:
            return None if exc.code == "missing" else "unavailable"
        except OSError:
            return "unavailable"

    before = observed(source_root, source)
    after = observed(target_root, target)
    if before is None and after == expected:
        return "applied"
    if before == expected and after is None:
        return "not_applied"
    return "ambiguous"


@contextmanager
def scan_directory(root: AuthorizedRoot, parts=()):
    with _walk(Path(root.path), root, parts) as (directory, _):
        try:
            with os.scandir(_native(directory.path) if _WINDOWS else directory.handle) as entries:
                yield [(entry.name, entry.is_dir(follow_symlinks=False)) for entry in entries]
        except OSError as exc:
            raise _os_problem(exc) from exc
