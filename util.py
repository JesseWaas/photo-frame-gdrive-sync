import shlex
import subprocess
import time
import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class CmdError(RuntimeError):
    """Raised when an external command invoked by this script fails."""

    def __init__(self, argv: list[str], rc: int, out: str):
        super().__init__(f"Command failed rc={rc}: {shlex.join(argv)}\n{out}")
        self.argv, self.rc, self.out = argv, rc, out


def run(argv: list[str], *, timeout: int = 120, retries: int = 0, backoff_s: float = 0.7) -> str:
    """Run a subprocess and return combined stdout/stderr on success.

    Retries on failure or timeout with exponential backoff.
    """
    last_out = ""
    last_rc = 1
    for attempt in range(retries + 1):
        try:
            p = subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=False)
            out = (p.stdout or "") + (p.stderr or "")
            if p.returncode == 0:
                return out
            last_out, last_rc = out, p.returncode
        except subprocess.TimeoutExpired:
            last_out, last_rc = f"TIMEOUT after {timeout}s\n", 124

        if attempt < retries:
            time.sleep(backoff_s * (2 ** attempt))

    raise CmdError(argv, last_rc, last_out)


def normalize_choice(value: str | None, allowed: set[str], default: str, *, label: str) -> str:
    """Strip/lower a config value, falling back to default when empty or unknown."""
    if value is None or str(value).strip() == "":
        return default

    choice = str(value).strip().lower()
    if choice not in allowed:
        logger.warning("Unknown %s %r; using %r", label, value, default)
        return default
    return choice


_file_hash_cache: dict[tuple[str, int, int], str] = {}


def file_content_hash(path: Path, *, hash_algorithm: str = "blake2s") -> str:
    """Return the hex digest of a file's contents.

    Defaults to blake2s for cheaper hashing on low-power hosts (e.g. Pi Zero).
    Results are cached by resolved path, mtime, and size so repeated sync
    runs avoid re-reading unchanged files.
    """
    path = Path(path)
    stat = path.stat()
    cache_key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    cached = _file_hash_cache.get(cache_key)
    if cached is not None:
        return cached

    hash_object = hashlib.new(hash_algorithm)
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hash_object.update(chunk)

    digest = hash_object.hexdigest()
    _file_hash_cache[cache_key] = digest
    return digest
