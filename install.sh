#!/usr/bin/env bash
# Distribution installer for Herdr Orchestrator
# Usage: curl -fsSL 'https://raw.githubusercontent.com/okinawa-systembit/herdr-orchestration/main/install.sh' | bash
# See skills/herdr-orchestrator/references/distribution-install.md for specifications.

set -euo pipefail

CLEANUP_TEMP_DIR=""
CLEANUP_STAGING_DIR=""
CLEANUP_BACKUP_DIR=""
CLEANUP_DEST=""
CLEANUP_SWAPPED=0

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM HUP
  # Rollback if swapped and failed
  if [[ ${CLEANUP_SWAPPED:-0} -eq 1 && $exit_code -ne 0 ]]; then
    echo "Installation failed. Rolling back to previous installation..." >&2
    if [[ -n "${CLEANUP_BACKUP_DIR:-}" && -d "$CLEANUP_BACKUP_DIR" ]]; then
      rm -rf "$CLEANUP_DEST" 2>/dev/null || true
      mv "$CLEANUP_BACKUP_DIR" "$CLEANUP_DEST" 2>/dev/null || true
      echo "Rolled back successfully." >&2
    fi
  fi
  # Clean up staging, backup on success, and temp_dir
  if [[ -n "${CLEANUP_STAGING_DIR:-}" ]]; then
    rm -rf "$CLEANUP_STAGING_DIR" 2>/dev/null || true
  fi
  if [[ $exit_code -eq 0 && -n "${CLEANUP_BACKUP_DIR:-}" ]]; then
    rm -rf "$CLEANUP_BACKUP_DIR" 2>/dev/null || true
  fi
  if [[ -n "${CLEANUP_TEMP_DIR:-}" ]]; then
    rm -rf "$CLEANUP_TEMP_DIR" 2>/dev/null || true
  fi
  exit "$exit_code"
}
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
trap cleanup EXIT

main() {
  local repo_owner="okinawa-systembit"
  local repo_name="herdr-orchestration"
  local target_version=""
  local dest="${HERDR_ORCHESTRATION_SKILL_DEST:-${HOME}/.agents/skills/herdr-orchestration}"
  local bin_dir="${HERDR_ORCHESTRATOR_BIN_DIR:-${HOME}/.local/bin}"
  local dry_run=0
  local force=0
  local local_tarball="${HERDR_INSTALL_LOCAL_TARBALL:-}"
  local local_checksum="${HERDR_INSTALL_LOCAL_CHECKSUM:-}"

  # Parse command-line options
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --version)
        target_version="${2:?missing version argument after --version}"
        shift 2
        ;;
      --dest)
        dest="${2:?missing path argument after --dest}"
        shift 2
        ;;
      --bin-dir)
        bin_dir="${2:?missing path argument after --bin-dir}"
        shift 2
        ;;
      --dry-run)
        dry_run=1
        shift
        ;;
      --force)
        force=1
        shift
        ;;
      -h | --help)
        usage
        exit 0
        ;;
      *)
        echo "Error: Unknown option: $1" >&2
        usage >&2
        exit 1
        ;;
    esac
  done

  # Step 1: Pre-environment checks
  for cmd in curl tar python3; do
    if ! command -v "$cmd" > /dev/null 2>&1; then
      echo "Error: Required command not found: $cmd" >&2
      exit 1
    fi
  done

  # Step 2: Version resolution
  if [[ -n "$target_version" ]]; then
    if [[ ! "$target_version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?$ ]]; then
      echo "Error: Invalid version format: '$target_version' (must follow SemVer, e.g. v0.1.0)" >&2
      exit 1
    fi
  elif [[ -z "$local_tarball" ]]; then
    echo "Resolving latest stable release version from GitHub..."
    target_version=$(resolve_latest_version "$repo_owner" "$repo_name")
    if [[ -z "$target_version" ]]; then
      echo "Error: Failed to resolve latest stable release version from GitHub." >&2
      exit 1
    fi
    echo "Resolved target version: ${target_version}"
  else
    target_version="local"
  fi

  local tarball_name="herdr-orchestration-${target_version}.tar.gz"
  local checksum_name="${tarball_name}.sha256"
  local base_download_url="https://github.com/${repo_owner}/${repo_name}/releases/download/${target_version}"
  local tarball_url="${base_download_url}/${tarball_name}"
  local checksum_url="${base_download_url}/${checksum_name}"
  local link_path="${bin_dir}/herdr-orchestrator"

  # Step 3: Dry-run execution
  if [[ $dry_run -eq 1 ]]; then
    echo "=== Dry-run: Herdr Orchestrator Distribution Installation ==="
    echo "Target Version:   ${target_version}"
    if [[ -n "$local_tarball" ]]; then
      echo "Source Tarball:   ${local_tarball} (local test override)"
    else
      echo "Download URL:     ${tarball_url}"
      echo "Checksum URL:     ${checksum_url}"
    fi
    echo "Destination Dir:  ${dest}"
    echo "Binary Symlink:   ${link_path} -> ${dest}/scripts/herdr-orchestrator"
    echo "Lock File:        ${HOME}/.agents/skills/.herdr-orchestrator.install.lock"
    echo ""
    echo "Planned Actions:"
    echo "  1. Acquire exclusive lock on install lockfile"
    echo "  2. Download distribution tarball and SHA256 checksum to temporary directory"
    echo "  3. Verify SHA256 integrity checksum"
    echo "  4. Validate tarball archive contents (no absolute paths, no .., no symlinks/devices)"
    echo "  5. Extract tarball to staging directory and verify 'scripts/herdr-orchestrator --help'"
    echo "  6. Safely swap existing ${dest} with backup and move staging into place"
    echo "  7. Update CLI symlink ${link_path}"
    echo "  8. Clean up temporary files and release lock"
    echo "=== Dry-run complete (no files were modified) ==="
    exit 0
  fi

  # Step 4: Check existing installation for git repo / dev clone
  if [[ -d "$dest" && -d "${dest}/.git" && $force -ne 1 ]]; then
    echo "Warning: ${dest} appears to be a git repository (development clone)." >&2
    echo "Overwriting it with a distribution package may delete local modifications." >&2
    echo "To force installation anyway, re-run with --force." >&2
    exit 1
  fi

  if [[ $force -eq 1 && -d "$dest" ]]; then
    echo "Warning: --force specified. Proceeding with installation over existing directory." >&2
  fi

  # Step 5: Install lock acquisition (flock)
  local lock_dir
  lock_dir=$(dirname "${HOME}/.agents/skills/.herdr-orchestrator.install.lock")
  mkdir -p "$lock_dir"
  local lock_file="${lock_dir}/.herdr-orchestrator.install.lock"

  # Acquire lock using python3 fcntl for high portability across Linux and macOS
  exec 200>"$lock_file"
  if ! python3 -c 'import fcntl, sys; fcntl.flock(200, fcntl.LOCK_EX | fcntl.LOCK_NB)' 2>/dev/null; then
    echo "Error: Another installation or update process is currently running (locked: ${lock_file})." >&2
    exit 1
  fi

  # Step 6: Temporary working directories & trap tracking setup
  local temp_dir
  temp_dir=$(mktemp -d "${TMPDIR:-/tmp}/herdr-install-XXXXXX")
  CLEANUP_TEMP_DIR="$temp_dir"
  CLEANUP_DEST="$dest"
  CLEANUP_STAGING_DIR="${dest}.staging-$$"
  CLEANUP_BACKUP_DIR="${dest}.backup-$$"
  CLEANUP_SWAPPED=0

  local staging_dir="$CLEANUP_STAGING_DIR"
  local backup_dir="$CLEANUP_BACKUP_DIR"

  # Step 7: Download tarball and checksum
  local dl_tarball="${temp_dir}/${tarball_name}"
  local dl_checksum="${temp_dir}/${checksum_name}"

  if [[ -n "$local_tarball" ]]; then
    echo "Using local tarball for installation: ${local_tarball}"
    cp "$local_tarball" "$dl_tarball"
    if [[ -n "$local_checksum" ]]; then
      cp "$local_checksum" "$dl_checksum"
    else
      # Compute checksum from local tarball
      compute_sha256 "$dl_tarball" > "$dl_checksum"
    fi
  else
    echo "Downloading ${tarball_url}..."
    download_file "$tarball_url" "$dl_tarball"
    echo "Downloading ${checksum_url}..."
    download_file "$checksum_url" "$dl_checksum"
  fi

  # Step 8: Integrity Verification (SHA256)
  echo "Verifying SHA256 checksum..."
  local expected_hash
  expected_hash=$(awk '{print $1}' "$dl_checksum" | head -n 1)
  local actual_hash
  actual_hash=$(compute_sha256 "$dl_tarball")

  if [[ -z "$expected_hash" || -z "$actual_hash" || "$expected_hash" != "$actual_hash" ]]; then
    echo "Error: Checksum verification failed!" >&2
    echo "  Expected: ${expected_hash}" >&2
    echo "  Actual:   ${actual_hash}" >&2
    exit 1
  fi
  echo "Checksum verification passed (${actual_hash})."

  # Step 9: Archive safety validation
  echo "Validating archive security and structure..."
  validate_archive_security "$dl_tarball"

  # Step 10: Extract to Staging and verify executable
  echo "Extracting to staging directory..."
  mkdir -p "$staging_dir"
  tar -xzf "$dl_tarball" -C "$staging_dir"

  chmod +x "${staging_dir}/scripts/herdr-orchestrator" \
           "${staging_dir}/scripts/task-registry.py" 2>/dev/null || true

  echo "Verifying CLI binary execution..."
  if ! HERDR_ENV="" "${staging_dir}/scripts/herdr-orchestrator" --help > /dev/null 2>&1; then
    echo "Error: Extracted CLI binary failed pre-verification execution ('herdr-orchestrator --help')." >&2
    exit 1
  fi

  # Step 11: Swap into place
  echo "Installing to ${dest}..."
  mkdir -p "$(dirname "$dest")"
  if [[ -d "$dest" ]]; then
    mv "$dest" "$backup_dir"
    CLEANUP_SWAPPED=1
  fi
  mv "$staging_dir" "$dest"

  # Step 12: Update CLI symlink
  mkdir -p "$bin_dir"
  ln -sf "${dest}/scripts/herdr-orchestrator" "$link_path"
  echo "Linked CLI: ${link_path} -> ${dest}/scripts/herdr-orchestrator"
  CLEANUP_SWAPPED=0 # installation and symlink fully successful

  # Success notification
  echo ""
  echo "================================================================="
  echo " Successfully installed Herdr Orchestration ${target_version}!"
  echo " Skill directory: ${dest}"
  echo " CLI symlink:     ${link_path}"
  echo "================================================================="
  echo ""
  if ! echo ":$PATH:" | grep -q ":${bin_dir}:"; then
    echo "Notice: '${bin_dir}' is not in your PATH."
    echo "Add it to your PATH by adding the following line to your shell profile (~/.bashrc, ~/.zshrc):"
    echo "  export PATH=\"${bin_dir}:\$PATH\""
    echo ""
  fi
  echo "Verify the installation by running:"
  echo "  herdr-orchestrator --help"
  echo ""
}

usage() {
  cat <<'EOF'
Usage: install.sh [OPTIONS]

Install Herdr Orchestrator from official GitHub Releases.

Options:
  --version <tag>   Target release tag to install (default: latest stable release)
  --dest <path>      Skill installation destination (default: ~/.agents/skills/herdr-orchestration)
  --bin-dir <path>   Binary symlink directory (default: ~/.local/bin)
  --dry-run          Print planned installation actions without making any changes
  --force            Force installation over existing development git repositories
  -h, --help         Show this help message and exit

Environment Variables:
  HERDR_ORCHESTRATION_SKILL_DEST  Override default installation destination directory
  HERDR_ORCHESTRATOR_BIN_DIR      Override default binary symlink directory
EOF
}

resolve_latest_version() {
  local owner=$1
  local repo=$2
  python3 -c '
import urllib.request, json, re, sys

owner = "'"${owner}"'"
repo = "'"${repo}"'"
url = f"https://api.github.com/repos/{owner}/{repo}/releases"
req = urllib.request.Request(url, headers={"User-Agent": "herdr-orchestrator-installer"})

try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        releases = json.loads(resp.read().decode("utf-8"))
        for rel in releases:
            if not rel.get("prerelease", False) and not rel.get("draft", False):
                tag = rel.get("tag_name", "")
                if re.match(r"^v[0-9]+\.[0-9]+\.[0-9]+$", tag):
                    print(tag)
                    sys.exit(0)
except Exception:
    pass

# Fallback: query latest release redirect
try:
    latest_url = f"https://github.com/{owner}/{repo}/releases/latest"
    req_latest = urllib.request.Request(latest_url, headers={"User-Agent": "herdr-orchestrator-installer"})
    with urllib.request.urlopen(req_latest, timeout=10) as resp:
        effective_url = resp.geturl()
        match = re.search(r"/releases/tag/(v[0-9]+\.[0-9]+\.[0-9]+)$", effective_url)
        if match:
            print(match.group(1))
            sys.exit(0)
except Exception:
    pass

sys.exit(1)
'
}

download_file() {
  local url=$1
  local output_path=$2
  curl -fsSL \
    --proto '=https' \
    --tlsv1.2 \
    "$url" -o "$output_path" || {
    echo "Error: Failed to download ${url}" >&2
    return 1
  }
}

compute_sha256() {
  local file_path=$1
  if command -v sha256sum > /dev/null 2>&1; then
    sha256sum "$file_path" | awk '{print $1}'
  elif command -v shasum > /dev/null 2>&1; then
    shasum -a 256 "$file_path" | awk '{print $1}'
  else
    python3 -c '
import hashlib, sys
h = hashlib.sha256()
with open(sys.argv[1], "rb") as f:
    while chunk := f.read(65536):
        h.update(chunk)
print(h.hexdigest())
' "$file_path"
  fi
}

validate_archive_security() {
  local tarball=$1

  # 1. List files and check for path traversal / forbidden elements
  local file_list
  file_list=$(tar -ztf "$tarball")

  while IFS= read -r path; do
    [[ -z "$path" ]] && continue

    # Reject absolute paths
    if [[ "$path" == /* ]]; then
      echo "Security Error: Archive contains absolute path: $path" >&2
      exit 1
    fi

    # Reject parent directory traversal
    if [[ "$path" == *../* || "$path" == */.. || "$path" == ".." ]]; then
      echo "Security Error: Archive contains path traversal (..): $path" >&2
      exit 1
    fi

    # Validate allowed root structure
    local root_element="${path%%/*}"
    case "$root_element" in
      SKILL.md | scripts | references) ;;
      *)
        echo "Security Error: Archive contains unexpected entry: $path" >&2
        exit 1
        ;;
    esac
  done <<< "$file_list"

  # 2. Check tar listing for symlinks, hardlinks, or device files
  # tar -tvf flags: first character indicates file type: '-' = regular file, 'd' = directory
  local detailed_list
  detailed_list=$(tar -ztvf "$tarball")
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    local file_type="${line:0:1}"
    if [[ "$file_type" != "-" && "$file_type" != "d" ]]; then
      echo "Security Error: Archive contains disallowed file type ($file_type): $line" >&2
      exit 1
    fi
  done <<< "$detailed_list"
}

main "$@"
