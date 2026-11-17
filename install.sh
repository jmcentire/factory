#!/usr/bin/env bash
# One-command install of factory-setup for members of the wandercom GitHub org.
#
#   gh api -H "Accept: application/vnd.github.raw" repos/wandercom/chiron/contents/install.sh | bash
#
# or, from a checkout:   ./install.sh [factory-setup flags...]
#
# What it does: downloads the latest factory-setup release binary for this
# OS/arch through `gh` (which already holds your org credentials), verifies its
# SHA-256 against the release checksums, installs it to ~/.local/bin, and runs
# it, passing every argument through. Nothing else is installed here; the
# installer itself asks before touching the machine.
#
# Falls back to building from source when Go is present and no release fits.
set -euo pipefail

REPO="${FACTORY_SETUP_REPO:-wandercom/chiron}"
TAG="${FACTORY_SETUP_VERSION:-}"          # empty = latest release
BIN_DIR="${FACTORY_SETUP_BIN_DIR:-$HOME/.local/bin}"
NAME="factory-setup"

red() { printf '\033[31m%s\033[0m\n' "$*" >&2; }
dim() { printf '\033[2m%s\033[0m\n' "$*" >&2; }
die() { red "$*"; exit 1; }

os="$(uname -s)"
case "$os" in
  Darwin) os=darwin ;;
  Linux)  os=linux ;;
  *) die "factory-setup supports macOS and Linux; this is $os." ;;
esac
arch="$(uname -m)"
case "$arch" in
  arm64|aarch64) arch=arm64 ;;
  x86_64|amd64)  arch=amd64 ;;
  *) die "unsupported CPU architecture: $arch" ;;
esac
asset="${NAME}-${os}-${arch}"

need_gh() {
  cat >&2 <<EOF

factory-setup lives in the private repository ${REPO}, so the download goes
through the GitHub CLI using your org login.

  macOS:  brew install gh
  Linux:  https://github.com/cli/cli/blob/trunk/docs/install_linux.md
  then:   gh auth login

Re-run this command afterwards.
EOF
  exit 1
}

command -v gh >/dev/null 2>&1 || { red "GitHub CLI (gh) is not installed."; need_gh; }
gh auth status >/dev/null 2>&1 || { red "gh is installed but not logged in."; need_gh; }

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

download_release() {
  local args=(release download --repo "$REPO" --dir "$tmp" --pattern "$asset" --pattern checksums.txt)
  [[ -n "$TAG" ]] && args+=("$TAG")
  gh "${args[@]}" 2>"$tmp/gh.err" || return 1
  [[ -f "$tmp/$asset" && -f "$tmp/checksums.txt" ]] || return 1
  local want have
  want="$(grep " ${asset}\$" "$tmp/checksums.txt" | awk '{print $1}')"
  have="$(sha256 "$tmp/$asset")"
  [[ -n "$want" && "$want" == "$have" ]] || die "checksum mismatch for $asset (expected $want, got $have)"
  dim "downloaded $asset from ${REPO} ${TAG:-latest}; sha256 verified"
  return 0
}

build_from_source() {
  if ! command -v go >/dev/null 2>&1; then
    die "Could not download a release of ${REPO} for ${os}/${arch}, and Go is not installed to build one.
Ask a maintainer to publish a release (push a v* tag), or install Go (https://go.dev/dl/) and re-run."
  fi
  dim "no matching release; building from source with $(go version | awk '{print $3}')"
  local src="$tmp/src"
  gh repo clone "$REPO" "$src" -- --quiet --depth 1 ${FACTORY_SETUP_REF:+--branch "$FACTORY_SETUP_REF"} 2>/dev/null \
    || die "could not clone ${REPO}${FACTORY_SETUP_REF:+ at $FACTORY_SETUP_REF}"
  [[ -f "$src/go.mod" && -d "$src/cmd/factory-setup" ]] \
    || die "${REPO}${FACTORY_SETUP_REF:+@$FACTORY_SETUP_REF} has no cmd/factory-setup to build.
Set FACTORY_SETUP_REF to a Chiron revision containing cmd/factory-setup, or wait for a release."
  (cd "$src" && GOTOOLCHAIN="${GOTOOLCHAIN:-auto}" CGO_ENABLED=0 go build -trimpath -o "$tmp/$asset" ./cmd/factory-setup) \
    || die "go build failed; see output above"
}

if ! download_release; then
  dim "$(head -2 "$tmp/gh.err" 2>/dev/null)"
  build_from_source
fi

mkdir -p "$BIN_DIR"
install -m 0755 "$tmp/$asset" "$BIN_DIR/$NAME"
# macOS marks downloads quarantined; the binary is unsigned, so clear the flag.
[[ "$os" == darwin ]] && xattr -d com.apple.quarantine "$BIN_DIR/$NAME" 2>/dev/null || true

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) dim "note: $BIN_DIR is not on your PATH; add it to your shell rc to run '$NAME' later" ;;
esac

dim "installed $BIN_DIR/$NAME ($("$BIN_DIR/$NAME" --version 2>/dev/null || echo dev))"
echo >&2
# Piped into bash, our stdin is this script. Hand factory-setup the terminal
# instead, when there is one, so it can ask: the menu, and the Google sign-in
# for Kinbase, which only runs with a person at the terminal.
if [[ ! -t 0 ]] && { : </dev/tty; } 2>/dev/null; then
  exec "$BIN_DIR/$NAME" "$@" </dev/tty
fi
exec "$BIN_DIR/$NAME" "$@"
