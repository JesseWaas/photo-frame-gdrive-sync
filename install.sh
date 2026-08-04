#!/usr/bin/env bash
# Deploys Photo Frame Sync and installs a systemd timer.
#
# From a local checkout:
#   sudo ./install.sh --remote google:photo-frame --hub-location 1-1
#
# One-liner from GitHub (replace OWNER/REPO):
#   curl -sL https://raw.githubusercontent.com/OWNER/REPO/main/install.sh \
#     | sudo bash -s -- --repo-url https://github.com/OWNER/REPO.git --hub-location 1-1
#
# Requires Linux + systemd. Install rclone, gphoto2, and optionally uhubctl
# beforehand, or pass --install-deps on Debian/Ubuntu.

set -euo pipefail

REMOTE="google:photo-frame"
FRAME_DIR="/store_00010001/DCIM"
LOCAL_PATH=""
INTERVAL="5min"
DEPLOY_DIR="/opt/photoframe"
BIN_PATH="/usr/local/bin/photoframe-sync"
RUN_AS_USER="photoframe"
SERVICE_NAME="photoframe-sync"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
TIMER_PATH="/etc/systemd/system/${SERVICE_NAME}.timer"
CREATE_USER=1
INSTALL_DEPS=0
SHARED_WITH_ME=1
HUB_LOCATION=""
HUB_VENDOR=""
SERVICE_ACCOUNT_FILE_SRC=""
REPO_URL="${PHOTOFRAME_REPO_URL:-}"
REPO_REF="${PHOTOFRAME_REPO_REF:-main}"
UDEV_USB_RULES="/etc/udev/rules.d/52-usb.rules"
SRC_DIR=""
CLEANUP_SRC=0

PYTHON_FILES=(
  sync.py
  cli.py
  config.py
  crop.py
  googledrive.py
  photoframe.py
  usbhub.py
  util.py
  timestamps.py
  rotation.py
  requirements.txt
)

usage() {
  sed -n '1,40p' "$0" 2>/dev/null || cat <<'EOF'
Photo Frame Sync installer. See script header for examples.
EOF
}

require_linux() {
  if [[ "$(uname -s)" != "Linux" ]]; then
    echo "This installer targets Linux with systemd." >&2
    exit 1
  fi
}

require_root_or_sudo() {
  if [[ "${EUID}" -ne 0 ]]; then
    if ! command -v sudo >/dev/null 2>&1; then
      echo "Please run as root or install sudo." >&2
      exit 1
    fi
  fi
}

do_as_root() {
  if [[ "${EUID}" -eq 0 ]]; then
    bash -c "$*"
  else
    sudo bash -c "$*"
  fi
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --remote) REMOTE="$2"; shift 2 ;;
      --frame-dir) FRAME_DIR="$2"; shift 2 ;;
      --local-path) LOCAL_PATH="$2"; shift 2 ;;
      --interval) INTERVAL="$2"; shift 2 ;;
      --deploy-dir) DEPLOY_DIR="$2"; shift 2 ;;
      --bin-path) BIN_PATH="$2"; shift 2 ;;
      --user) RUN_AS_USER="$2"; shift 2 ;;
      --create-user) CREATE_USER=1; shift 1 ;;
      --no-create-user) CREATE_USER=0; shift 1 ;;
      --install-deps) INSTALL_DEPS=1; shift 1 ;;
      --shared-with-me) SHARED_WITH_ME=1; shift 1 ;;
      --no-shared-with-me) SHARED_WITH_ME=0; shift 1 ;;
      --hub_location|--hub-location) HUB_LOCATION="$2"; shift 2 ;;
      --hub_vendor|--hub-vendor) HUB_VENDOR="$2"; shift 2 ;;
      --service-account-file) SERVICE_ACCOUNT_FILE_SRC="$2"; shift 2 ;;
      --repo-url) REPO_URL="$2"; shift 2 ;;
      --repo-ref) REPO_REF="$2"; shift 2 ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Unknown option: $1" >&2
        exit 1
        ;;
    esac
  done

  if [[ -z "${LOCAL_PATH}" ]]; then
    LOCAL_PATH="/home/${RUN_AS_USER}/google_photo_sync"
  fi
}

running_from_checkout() {
  local candidate="${BASH_SOURCE[0]:-}"
  [[ -n "${candidate}" ]] \
    && [[ "${candidate}" != /dev/fd/* ]] \
    && [[ "${candidate}" != /proc/self/fd/* ]] \
    && [[ -f "${candidate}" ]] \
    && [[ -f "$(cd "$(dirname "${candidate}")" && pwd)/sync.py" ]]
}

resolve_source_dir() {
  if running_from_checkout; then
    SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    echo "Using local project source: ${SRC_DIR}"
    return 0
  fi

  if [[ -z "${REPO_URL}" ]]; then
    cat >&2 <<'EOF'
This installer was run via curl/pipe, so it needs a repository URL.

Pass --repo-url, or set PHOTOFRAME_REPO_URL, for example:
  curl -sL https://raw.githubusercontent.com/OWNER/REPO/main/install.sh \
    | sudo bash -s -- --repo-url https://github.com/OWNER/REPO.git
EOF
    exit 1
  fi

  if ! command -v git >/dev/null 2>&1; then
    echo "git is required to fetch the project when installing via curl." >&2
    exit 1
  fi

  local tmp
  tmp="$(mktemp -d)"
  CLEANUP_SRC=1
  echo "Fetching ${REPO_URL} (${REPO_REF}) into ${tmp}..."
  git clone --depth 1 --branch "${REPO_REF}" "${REPO_URL}" "${tmp}/repo"
  SRC_DIR="${tmp}/repo"

  if [[ ! -f "${SRC_DIR}/sync.py" ]]; then
    echo "Fetched repo does not contain sync.py" >&2
    exit 1
  fi
}

cleanup() {
  if [[ "${CLEANUP_SRC}" -eq 1 && -n "${SRC_DIR}" ]]; then
    rm -rf "$(dirname "${SRC_DIR}")"
  fi
}
trap cleanup EXIT

install_deps() {
  if [[ "${INSTALL_DEPS}" -ne 1 ]]; then
    return 0
  fi

  if ! command -v apt-get >/dev/null 2>&1; then
    echo "--install-deps is currently supported on apt-based systems only." >&2
    exit 1
  fi

  echo "Installing system packages..."
  do_as_root "apt-get update"
  do_as_root "DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 python3-venv python3-pip rclone gphoto2 uhubctl git \
    libopenblas0 libwebpmux3 libwebpdemux2 libwebp7 libtiff6 \
    libopenjp2-7 libxcb1 libgomp1 libatomic1 libopenexr-3-1-30 \
    libavcodec61 libavformat61 libavutil59 libswscale8 \
    python3-numpy python3-opencv python3-pil opencv-data"
}

ensure_user() {
  if id -u "${RUN_AS_USER}" >/dev/null 2>&1; then
    return 0
  fi
  if [[ "${CREATE_USER}" -eq 1 ]]; then
    echo "Creating system user: ${RUN_AS_USER}"
    do_as_root "useradd --system --create-home --shell /usr/sbin/nologin '${RUN_AS_USER}'"
  else
    echo "User '${RUN_AS_USER}' does not exist. Re-run with --create-user or choose --user." >&2
    exit 1
  fi
}

ensure_device_access() {
  if getent group plugdev >/dev/null 2>&1; then
    echo "Adding '${RUN_AS_USER}' to 'plugdev'..."
    do_as_root "usermod -aG plugdev '${RUN_AS_USER}'" || true
  else
    echo "Group 'plugdev' not found; ensure udev rules grant camera access to '${RUN_AS_USER}'."
  fi

  if getent group dialout >/dev/null 2>&1; then
    echo "Adding '${RUN_AS_USER}' to 'dialout'..."
    do_as_root "usermod -aG dialout '${RUN_AS_USER}'" || true
  fi
}

install_usb_rules() {
  echo "Installing udev USB rules for uhubctl: ${UDEV_USB_RULES}"
  local tmp
  tmp="$(mktemp)"
  cat > "${tmp}" <<'EOF'
SUBSYSTEM=="usb", DRIVER=="hub|usb", MODE="0666"
# Linux 6.0 or later (safe to keep on older kernels)
SUBSYSTEM=="usb", DRIVER=="hub|usb", \
  RUN="/bin/sh -c \"chmod -f 666 $sys$devpath/*port*/disable || true\""
EOF
  do_as_root "install -m 0644 '${tmp}' '${UDEV_USB_RULES}'"
  rm -f "${tmp}"
  do_as_root "udevadm control --reload-rules || true"
  do_as_root "udevadm trigger --subsystem-match=usb || true"
}

is_armv6() {
  # Original Pi Zero / Pi 1 — pip NumPy/OpenCV wheels often SIGILL here.
  [[ "$(uname -m)" == "armv6l" ]]
}

install_files() {
  echo "Creating deploy directory: ${DEPLOY_DIR}"
  do_as_root "mkdir -p '${DEPLOY_DIR}'"

  local file
  for file in "${PYTHON_FILES[@]}"; do
    if [[ ! -f "${SRC_DIR}/${file}" ]]; then
      echo "Missing required project file: ${file}" >&2
      exit 1
    fi
    echo "Deploying ${file}"
    do_as_root "install -m 0644 '${SRC_DIR}/${file}' '${DEPLOY_DIR}/${file}'"
  done

  if [[ -f "${SRC_DIR}/requirements-armv6.txt" ]]; then
    do_as_root "install -m 0644 '${SRC_DIR}/requirements-armv6.txt' '${DEPLOY_DIR}/requirements-armv6.txt'"
  fi

  # Example host config only; runtime config/ is expected from Drive sync.
  if [[ -d "${SRC_DIR}/config.example" ]]; then
    do_as_root "rm -rf '${DEPLOY_DIR}/config.example'"
    do_as_root "cp -a '${SRC_DIR}/config.example' '${DEPLOY_DIR}/config.example'"
  fi

  do_as_root "mkdir -p '${LOCAL_PATH}'"
  do_as_root "chown -R '${RUN_AS_USER}:${RUN_AS_USER}' '${DEPLOY_DIR}' '${LOCAL_PATH}'"

  local venv_args=()
  local req_file="${DEPLOY_DIR}/requirements.txt"
  if is_armv6; then
    echo "Detected armv6 (Pi Zero/1): using apt NumPy/OpenCV/Pillow via --system-site-packages"
    venv_args+=(--system-site-packages)
    if [[ -f "${DEPLOY_DIR}/requirements-armv6.txt" ]]; then
      req_file="${DEPLOY_DIR}/requirements-armv6.txt"
    fi
  fi

  echo "Creating Python virtualenv in ${DEPLOY_DIR}/venv"
  do_as_root "sudo -H -u '${RUN_AS_USER}' python3 -m venv ${venv_args[*]} '${DEPLOY_DIR}/venv'"
  do_as_root "sudo -H -u '${RUN_AS_USER}' '${DEPLOY_DIR}/venv/bin/pip' install --upgrade pip"
  do_as_root "sudo -H -u '${RUN_AS_USER}' '${DEPLOY_DIR}/venv/bin/pip' install -r '${req_file}'"

  if [[ -n "${SERVICE_ACCOUNT_FILE_SRC}" ]]; then
    install_service_account
  fi

  install_wrapper
}

install_service_account() {
  if [[ ! -f "${SERVICE_ACCOUNT_FILE_SRC}" ]]; then
    echo "Specified --service-account-file not found: ${SERVICE_ACCOUNT_FILE_SRC}" >&2
    exit 1
  fi

  local cfg_dir sa_dest remote_name
  cfg_dir="/home/${RUN_AS_USER}/.config/photoframe"
  sa_dest="${cfg_dir}/photo-frame.json"
  remote_name="${REMOTE%%:*}"

  if [[ -z "${remote_name}" ]]; then
    echo "Invalid --remote value: ${REMOTE}" >&2
    exit 1
  fi

  echo "Installing service account file to ${sa_dest}"
  do_as_root "mkdir -p '${cfg_dir}' && chown -R '${RUN_AS_USER}:${RUN_AS_USER}' '${cfg_dir}' && chmod 700 '${cfg_dir}'"
  do_as_root "install -m 600 -o '${RUN_AS_USER}' -g '${RUN_AS_USER}' '${SERVICE_ACCOUNT_FILE_SRC}' '${sa_dest}'"

  echo "Configuring rclone remote '${remote_name}' for user '${RUN_AS_USER}'..."
  do_as_root "sudo -H -u '${RUN_AS_USER}' rclone config create '${remote_name}' drive service_account_file='${sa_dest}' scope=drive --non-interactive || sudo -H -u '${RUN_AS_USER}' rclone config update '${remote_name}' service_account_file='${sa_dest}' scope=drive --non-interactive"
}

install_wrapper() {
  echo "Creating wrapper: ${BIN_PATH}"
  local wrapper_tmp shared_flag hub_args
  wrapper_tmp="$(mktemp)"
  shared_flag="--shared-with-me"
  if [[ "${SHARED_WITH_ME}" -eq 0 ]]; then
    shared_flag="--no-shared-with-me"
  fi

  hub_args=""
  if [[ -n "${HUB_LOCATION}" ]]; then
    hub_args+=" --hub-location '${HUB_LOCATION}'"
  fi
  if [[ -n "${HUB_VENDOR}" ]]; then
    hub_args+=" --hub-vendor '${HUB_VENDOR}'"
  fi

  cat > "${wrapper_tmp}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
PATH="/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin"
cd '${DEPLOY_DIR}'
exec '${DEPLOY_DIR}/venv/bin/python' '${DEPLOY_DIR}/sync.py' \\
  --sync-files --update-frame \\
  --remote '${REMOTE}' \\
  --local-path '${LOCAL_PATH}' \\
  --frame-dir '${FRAME_DIR}' \\
  ${shared_flag} \\
  -v${hub_args}
EOF
  do_as_root "install -m 0755 '${wrapper_tmp}' '${BIN_PATH}'"
  rm -f "${wrapper_tmp}"
}

install_systemd() {
  echo "Writing systemd unit: ${SERVICE_PATH}"
  local svc_tmp supp_groups=() supp_groups_line=""
  svc_tmp="$(mktemp)"

  if getent group plugdev >/dev/null 2>&1; then supp_groups+=("plugdev"); fi
  if getent group dialout >/dev/null 2>&1; then supp_groups+=("dialout"); fi
  if [[ ${#supp_groups[@]} -gt 0 ]]; then
    supp_groups_line="SupplementaryGroups=$(IFS=' '; echo "${supp_groups[*]}")"
  fi

  cat > "${svc_tmp}" <<EOF
[Unit]
Description=Photo Frame Sync
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=${RUN_AS_USER}
Group=${RUN_AS_USER}
WorkingDirectory=${DEPLOY_DIR}
Environment=PATH=${DEPLOY_DIR}/venv/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
ExecStart=${BIN_PATH}
StandardOutput=journal+console
StandardError=journal+console
${supp_groups_line}

[Install]
WantedBy=multi-user.target
EOF
  do_as_root "install -m 0644 '${svc_tmp}' '${SERVICE_PATH}'"
  rm -f "${svc_tmp}"

  echo "Writing systemd timer: ${TIMER_PATH}"
  local timer_tmp
  timer_tmp="$(mktemp)"
  cat > "${timer_tmp}" <<EOF
[Unit]
Description=Run Photo Frame Sync every ${INTERVAL}

[Timer]
OnBootSec=2min
OnUnitActiveSec=${INTERVAL}
Unit=${SERVICE_NAME}.service

[Install]
WantedBy=timers.target
EOF
  do_as_root "install -m 0644 '${timer_tmp}' '${TIMER_PATH}'"
  rm -f "${timer_tmp}"
}

enable_timer() {
  echo "Reloading systemd and enabling timer..."
  do_as_root "systemctl daemon-reload"
  do_as_root "systemctl enable --now '${SERVICE_NAME}.timer'"
  echo "Timer status:"
  do_as_root "systemctl status '${SERVICE_NAME}.timer' --no-pager || true"
}

check_tools() {
  local missing=()
  command -v rclone >/dev/null 2>&1 || missing+=("rclone")
  command -v gphoto2 >/dev/null 2>&1 || missing+=("gphoto2")
  command -v python3 >/dev/null 2>&1 || missing+=("python3")
  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "Missing required tools: ${missing[*]}" >&2
    echo "Install them first, or re-run with --install-deps on Debian/Ubuntu." >&2
    exit 1
  fi
  if ! command -v uhubctl >/dev/null 2>&1; then
    echo "Warning: uhubctl not found; USB hub reset recovery will be unavailable."
  fi
}

main() {
  require_linux
  require_root_or_sudo
  parse_args "$@"
  resolve_source_dir
  install_deps
  check_tools
  ensure_user
  ensure_device_access
  install_usb_rules
  install_files
  install_systemd
  enable_timer
  do_as_root "udevadm control --reload-rules || true"
  do_as_root "udevadm trigger --attr-match=subsystem=usb || true"

  cat <<EOF

Installed.
- Deploy dir: ${DEPLOY_DIR}
- Wrapper:    ${BIN_PATH}
- Cache:      ${LOCAL_PATH}
- Service:    ${SERVICE_NAME}.service
- Timer:      ${SERVICE_NAME}.timer (every ${INTERVAL})

View logs:   journalctl -u ${SERVICE_NAME} -f
Force run:   sudo systemctl start ${SERVICE_NAME}.service
Disable:     sudo systemctl disable --now ${SERVICE_NAME}.timer

Ensure Drive contains config/<hostname>.json (and optional config/defaults.json), and that rclone works as '${RUN_AS_USER}'.
EOF
}

main "$@"
