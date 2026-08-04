# Photo Frame Sync

Sync photos from a Google Drive folder to a persistent local cache and then update a `gphoto2`-compatible photo frame from that cache.

## Overview

The sync flow is:

1. Run `rclone sync` from Google Drive into a local cache directory.
2. Read optional `config/defaults.json` plus `config/<hostname>.json` from the synced cache.
3. Select files for the current device using hostname-specific rules.
4. Dedupe selected files by content hash and assign stable frame names.
5. Compare the desired frame set to files already on the frame.
6. Delete frame files that are no longer selected.
7. For each missing file: stage (crop if needed), upload, then discard the staged copy.

Frame filenames are derived from source content hash plus crop/aspect/date policy.
Identical source bytes are only planned once. Crops are not kept on disk; they are
created one file at a time under `.upload_stage/` immediately before upload.

## Requirements

Python packages:

```bash
pip install -r requirements.txt
```

External tools:

- `rclone`
- `gphoto2`
- `uhubctl` only if you want automatic USB hub reset recovery

## Usage

Sync the local cache only:

```bash
python3 sync.py --sync-files
```

Sync the cache and update the frame:

```bash
python3 sync.py --sync-files --update-frame
```

Show the planned frame changes without modifying the frame:

```bash
python3 sync.py --update-frame --dry-run
```

Test end-to-end without hardware by using a local directory as a fake frame:

```bash
python3 sync.py \
  --sync-files --update-frame \
  --local-path ~/tmp/photo_cache \
  --mock-frame-dir ~/tmp/mock_frame \
  -v
```

The mock frame supports list/upload/delete against that directory, so you can inspect cropped uploads and deletions locally.

Show a diff-style plan only:

```bash
python3 sync.py --update-frame --diff
```

If the frame is sometimes missing after power events, provide hub details so the tool can retry after a reset:

```bash
python3 sync.py --update-frame --hub-location 1-1
```

Override paths and timeouts at runtime as needed:

```bash
python3 sync.py \
  --sync-files --update-frame \
  --remote google:photo-frame \
  --local-path ~/google_photo_sync \
  --frame-dir /store_00010001/DCIM \
  --rclone-timeout 600 \
  --gphoto-timeout 600 \
  --retries 2 \
  --no-shared-with-me
```

`--aspect-ratio` and `--date` are optional overrides for the per-host config values.

## Device Filtering

Place a `config/` directory in the Google Drive source folder. After sync, it is available in the local cache. Each host loads:

1. `config/defaults.json` (optional shared settings)
2. `config/<hostname>.json` (required for that host; hostname keys override defaults)

Host files replace `visibility_rules` and `crop_rules` wholesale when present; they are not concatenated with defaults. A JSON error in one host file only affects that host.

Example layout (see `config.example/` in this repo):

```text
config/
  defaults.json
  kitchen-frame.json
  office-frame.json
```

`config/defaults.json`:

```json
{
  "aspect_ratio": "3:4",
  "date": "upload",
  "max_bytes": "20GB",
  "rotation": "hourly",
  "preserve_unmanaged": true
}
```

`config/kitchen-frame.json`:

```json
{
  "visibility_rules": [
    {"filter": "^albums/family/.*", "schedule": "* * * * *"},
    {"filter": "^albums/holidays/.*", "schedule": "* * * 12 *"}
  ],
  "crop_rules": [
    {"filter": "^albums/family/.*", "crop": "faces"},
    {"filter": "^albums/holidays/.*", "crop": "faces"}
  ]
}
```

Supported keys (defaults and/or host file):

- `aspect_ratio`: target frame ratio as `W:H` (for example `3:4` or `9:16`)
- `date`: `upload` (sync/upload time) or `capture` (preserve EXIF/taken time)
- `max_bytes` / `max_files`: optional frame capacity limits
- `rotation`: `hourly` / `daily` / `every-run` / `off`
- `preserve_unmanaged`: when `true`, only delete this tool's hash-named uploads
- `visibility_rules`: which paths appear on the frame; a file is included if **any** currently active rule matches (logical OR)
- `crop_rules`: crop strategies by path; independent of schedule

Each visibility rule has:

- `filter`: regex matched against the path relative to the synced root
- `schedule`: 5-field cron expression; active when local time matches

Each crop rule has:

- `filter`: regex matched against the path relative to the synced root
- `crop`: `none` / `center` / `faces`

With `max_bytes`/`max_files` set, the sync keeps only a rotating window on the frame and deletes managed photos that fall out of the current window. The full filtered library can still live in the local cache.

Notes:

- If the `config/` directory is missing, every file in the cache is selected.
- If `config/` exists but this hostname has no file (or the file is invalid), nothing is selected.
- Crop is resolved only from `crop_rules` (first match wins). Paths with no crop match use `none`.
- Selection schedules do not affect crop; you can include an album seasonally while always cropping matching paths the same way.
- The `config/` directory is never uploaded to the frame.

### Config UI (Google Apps Script)

A small Drive-backed editor lives in `apps-script/`. It focuses on per-host visibility and crop rules and includes a nested album folder browser. Setup:

1. Copy `config.example/` into your Drive sync root as `config/`.
2. Create a Google Apps Script project; paste `apps-script/Code.gs` and add an HTML file named exactly `index` (paste `apps-script/index.html`). See `apps-script/README.md`.
3. Set script properties `CONFIG_FOLDER_ID` (Drive ID of `config/`) and optionally `ALBUMS_FOLDER_ID` (Drive ID of `albums/`).
4. Deploy → New deployment → Web app (execute as you; access: only yourself / your domain as appropriate).

The form overwrites `config/<hostname>.json` (creating it if needed). Prefer editing rules there rather than hand-editing JSON on hosts you manage through the UI.

## Deploy

On a Linux host with systemd:

```bash
sudo ./install.sh \
  --remote google:photo-frame \
  --hub-location 1-1 \
  --interval 5min
```

Or install directly from GitHub after the repo is published:

```bash
curl -sL https://raw.githubusercontent.com/OWNER/REPO/main/install.sh \
  | sudo bash -s -- \
    --repo-url https://github.com/OWNER/REPO.git \
    --remote google:photo-frame \
    --hub-location 1-1
```

Useful options:

- `--install-deps` — apt-install runtime tools plus libs needed by NumPy/OpenCV wheels (`libopenblas0`, `libwebpmux3`, …) on Debian/Ubuntu
- `--local-path` — persistent photo cache (default `/home/photoframe/google_photo_sync`)
- `--service-account-file` — install a Drive service-account JSON and configure rclone
- `--no-shared-with-me` — disable rclone shared-folder mode
- `--user` / `--no-create-user` — control the service account user

The installer deploys the Python modules to `/opt/photoframe`, creates a venv, installs `requirements.txt`, writes `/usr/local/bin/photoframe-sync`, and enables a systemd timer.

On original Pi Zero / Pi 1 (`armv6l`), pip NumPy/OpenCV wheels often crash with `SIGILL`. The installer detects that and uses Debian `python3-numpy` / `python3-opencv` / `python3-pil` via a `--system-site-packages` venv instead.

## Tests

Run the unit tests with:

```bash
python3 -m unittest discover -s test
```
