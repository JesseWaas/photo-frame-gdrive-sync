"""Per-host device settings loaded from synced config/<hostname>.json."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import logging
import os
import re
from pathlib import Path

from croniter import croniter

from crop import DEFAULT_ASPECT_RATIO, parse_aspect_ratio
from timestamps import DEFAULT_DATE_POLICY, normalize_date_policy
from rotation import normalize_rotation, parse_byte_size, parse_max_files

logger = logging.getLogger(__name__)

CONFIG_DIRNAME = "config"
DEFAULTS_FILENAME = "defaults.json"
DEFAULT_FILTER = ".*"
DEFAULT_SCHEDULE = "* * * * *"
DEFAULT_CROP = "none"
DEFAULT_PRESERVE_UNMANAGED = False


@dataclass(frozen=True)
class VisibilityRule:
    """One visibility rule: which paths appear on the frame, and when."""

    filter: str
    schedule: str = DEFAULT_SCHEDULE


@dataclass(frozen=True)
class CropRule:
    """One crop rule: how matching paths are cropped (independent of schedule)."""

    filter: str
    crop: str = DEFAULT_CROP


@dataclass(frozen=True)
class HostConfig:
    """Parsed settings for one host after merging defaults."""

    visibility_rules: list[VisibilityRule] | None
    crop_rules: list[CropRule] | None = None
    aspect_ratio: str = DEFAULT_ASPECT_RATIO
    date: str = DEFAULT_DATE_POLICY
    max_bytes: int | None = None
    max_files: int | None = None
    rotation: str | None = None
    preserve_unmanaged: bool = DEFAULT_PRESERVE_UNMANAGED


@dataclass(frozen=True)
class SelectedFile:
    """A local cache file selected for the frame, with crop strategy."""

    path: Path
    crop: str = DEFAULT_CROP


def schedule_is_active(schedule: str, when: datetime | None = None) -> bool:
    """Return True if `when` satisfies a 5-field cron expression."""
    when = when or datetime.now()
    try:
        return croniter.match(schedule, when)
    except (KeyError, ValueError, TypeError) as exc:
        logger.error("Invalid cron schedule %r: %s", schedule, exc)
        return False


def parse_visibility_rules(raw_rules) -> list[VisibilityRule]:
    """Parse a list of visibility rule mappings into VisibilityRule values."""
    if raw_rules is None:
        return []

    if not isinstance(raw_rules, list):
        logger.error("Host visibility_rules must be a list; got %s", type(raw_rules).__name__)
        return []

    rules: list[VisibilityRule] = []
    for index, entry in enumerate(raw_rules):
        if not isinstance(entry, dict):
            logger.error("Ignoring invalid visibility_rule at index %d: expected mapping", index)
            continue

        filter_pattern = entry.get("filter")
        if not filter_pattern:
            logger.error("Ignoring visibility_rule at index %d: missing filter", index)
            continue

        rules.append(
            VisibilityRule(
                filter=str(filter_pattern),
                schedule=str(entry.get("schedule", DEFAULT_SCHEDULE)),
            )
        )

    return rules


def parse_crop_rules(raw_rules) -> list[CropRule]:
    """Parse a list of crop rule mappings into CropRule values."""
    if raw_rules is None:
        return []

    if not isinstance(raw_rules, list):
        logger.error("Host crop_rules must be a list; got %s", type(raw_rules).__name__)
        return []

    rules: list[CropRule] = []
    for index, entry in enumerate(raw_rules):
        if not isinstance(entry, dict):
            logger.error("Ignoring invalid crop_rule at index %d: expected mapping", index)
            continue

        filter_pattern = entry.get("filter")
        if not filter_pattern:
            logger.error("Ignoring crop_rule at index %d: missing filter", index)
            continue

        rules.append(
            CropRule(
                filter=str(filter_pattern),
                crop=str(entry.get("crop") or DEFAULT_CROP),
            )
        )

    return rules


def parse_bool(value, *, default: bool, label: str) -> bool:
    """Parse a boolean-ish config value."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)

    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False

    logger.error("Invalid %s %r; using %s", label, value, default)
    return default


def parse_host_entry(raw_entry) -> HostConfig:
    """Parse a merged defaults+host mapping into HostConfig."""
    if not isinstance(raw_entry, dict):
        logger.error("Host config must be a mapping; got %s", type(raw_entry).__name__)
        return HostConfig(visibility_rules=[], crop_rules=[])

    aspect_ratio = str(raw_entry.get("aspect_ratio") or DEFAULT_ASPECT_RATIO)
    try:
        parse_aspect_ratio(aspect_ratio)
    except ValueError as exc:
        logger.error("Invalid aspect_ratio %r (%s); using %s", aspect_ratio, exc, DEFAULT_ASPECT_RATIO)
        aspect_ratio = DEFAULT_ASPECT_RATIO

    date_policy = normalize_date_policy(raw_entry.get("date"))

    try:
        max_bytes = parse_byte_size(raw_entry.get("max_bytes"))
    except ValueError as exc:
        logger.error("Invalid max_bytes (%s); ignoring limit", exc)
        max_bytes = None

    try:
        max_files = parse_max_files(raw_entry.get("max_files"))
    except ValueError as exc:
        logger.error("Invalid max_files (%s); ignoring limit", exc)
        max_files = None

    return HostConfig(
        visibility_rules=parse_visibility_rules(raw_entry.get("visibility_rules")),
        crop_rules=parse_crop_rules(raw_entry.get("crop_rules")),
        aspect_ratio=aspect_ratio,
        date=date_policy,
        max_bytes=max_bytes,
        max_files=max_files,
        rotation=normalize_rotation(raw_entry.get("rotation")),
        preserve_unmanaged=parse_bool(
            raw_entry.get("preserve_unmanaged"),
            default=DEFAULT_PRESERVE_UNMANAGED,
            label="preserve_unmanaged",
        ),
    )


def load_json_mapping(path: Path) -> dict | None:
    """Load a JSON object from path.

    Returns:
      dict on success (empty if file missing),
      None if the file exists but cannot be parsed as an object.
    """
    path = Path(path)
    if not path.is_file():
        return {}

    try:
        with open(path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse %s: %s", path, exc)
        return None

    if not isinstance(loaded, dict):
        logger.error("%s must be a JSON object; got %s", path, type(loaded).__name__)
        return None

    return loaded


def load_host_config(config_dir: Path, hostname: str) -> HostConfig:
    """Load settings for hostname from config/defaults.json + config/<hostname>.json.

    visibility_rules is None if the config directory is missing (caller should default to all files).
    visibility_rules is [] if the host file is absent or unreadable.
    Host keys override defaults; rule lists are replaced, not concatenated.
    """
    config_dir = Path(config_dir)
    if not config_dir.is_dir():
        return HostConfig(visibility_rules=None, crop_rules=None)

    defaults_path = config_dir / DEFAULTS_FILENAME
    host_path = config_dir / f"{hostname}.json"

    defaults = load_json_mapping(defaults_path)
    if defaults is None:
        logger.error("Ignoring invalid %s", defaults_path)
        defaults = {}

    if not host_path.is_file():
        logger.warning("No config file for host %s at %s; selecting no files", hostname, host_path)
        return HostConfig(visibility_rules=[], crop_rules=[])

    host_raw = load_json_mapping(host_path)
    if host_raw is None:
        logger.error("Invalid host config %s; selecting no files", host_path)
        return HostConfig(visibility_rules=[], crop_rules=[])

    merged = {**defaults, **host_raw}
    host_config = parse_host_entry(merged)
    logger.info(
        "Loaded %d visibility rule(s) and %d crop rule(s) for host %s from %s "
        "(defaults=%s, aspect_ratio=%s, date=%s, max_bytes=%s, max_files=%s, "
        "rotation=%s, preserve_unmanaged=%s)",
        len(host_config.visibility_rules or []),
        len(host_config.crop_rules or []),
        hostname,
        host_path,
        defaults_path if defaults_path.is_file() else "none",
        host_config.aspect_ratio,
        host_config.date,
        host_config.max_bytes,
        host_config.max_files,
        host_config.rotation or "off",
        host_config.preserve_unmanaged,
    )
    return host_config


def active_visibility_rules(rules: list[VisibilityRule], when: datetime | None = None) -> list[VisibilityRule]:
    """Return visibility rules whose cron schedule matches `when`."""
    when = when or datetime.now()
    active = [rule for rule in rules if schedule_is_active(rule.schedule, when)]
    logger.info(
        "%d of %d visibility rule(s) are active at %s",
        len(active),
        len(rules),
        when.isoformat(timespec="seconds"),
    )
    return active


def _compile_filter(pattern: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.PatternError as exc:
        logger.error("Invalid regex pattern %r (%s); treating as never-matching", pattern, exc)
        return re.compile(r"(?!)")


def select_files(
    directory: Path,
    visibility_rules: list[VisibilityRule] | None,
    crop_rules: list[CropRule] | None = None,
    *,
    when: datetime | None = None,
    exclude_names: set[str] | None = None,
    exclude_dirs: set[str] | None = None,
) -> list[SelectedFile]:
    """Select files under directory using active visibility rules (logical OR).

    If visibility_rules is None (no config directory), every file is selected.
    If visibility_rules is empty, or none are currently active, nothing is selected.
    Crop is resolved independently from crop_rules (schedule is ignored for crop).
    When multiple crop rules match a file, the first matching crop rule wins.
    """
    directory = Path(directory)
    exclude_names = exclude_names or set()
    exclude_dirs = exclude_dirs or {CONFIG_DIRNAME, ".upload_stage", ".crop_cache"}
    when = when or datetime.now()

    if visibility_rules is None:
        effective_rules = [VisibilityRule(filter=DEFAULT_FILTER)]
    else:
        effective_rules = active_visibility_rules(visibility_rules, when)

    if not effective_rules:
        return []

    compiled = [(rule, _compile_filter(rule.filter)) for rule in effective_rules]
    compiled_crops = [(_compile_filter(rule.filter), rule.crop) for rule in (crop_rules or [])]
    selected: dict[Path, SelectedFile] = {}

    for root, dirnames, filenames in os.walk(directory):
        dirnames[:] = [name for name in dirnames if name not in exclude_dirs]

        for filename in filenames:
            if filename in exclude_names:
                continue

            full_path = Path(root) / filename
            relative_path = full_path.relative_to(directory).as_posix()

            matched = False
            for _rule, regex in compiled:
                if regex.match(relative_path):
                    matched = True
                    break
            if not matched:
                continue

            crop = DEFAULT_CROP
            for regex, strategy in compiled_crops:
                if regex.match(relative_path):
                    crop = strategy
                    break

            selected.setdefault(full_path, SelectedFile(path=full_path, crop=crop))

    return list(selected.values())
