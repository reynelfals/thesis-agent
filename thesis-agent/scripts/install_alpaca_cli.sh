#!/usr/bin/env bash
set -euo pipefail

ALPACA_CLI_VERSION="${ALPACA_CLI_VERSION:-v0.0.13}"
INSTALL_DIR="${ALPACA_CLI_INSTALL_DIR:-/home/runner/go/bin}"
TARGET="$INSTALL_DIR/alpaca"
export GOBIN="$INSTALL_DIR"
export PATH="$INSTALL_DIR:$PATH"

if ! command -v go >/dev/null 2>&1; then
  echo "Go is required to install Alpaca CLI ${ALPACA_CLI_VERSION}." >&2
  exit 1
fi

if [[ -x "$TARGET" ]] && [[ "$("$TARGET" version 2>/dev/null)" == "$ALPACA_CLI_VERSION" ]]; then
  "$TARGET" version
  exit 0
fi

mkdir -p "$INSTALL_DIR"
go install "github.com/alpacahq/cli/cmd/alpaca@${ALPACA_CLI_VERSION}"

installed_version="$("$TARGET" version)"
if [[ "$installed_version" != "$ALPACA_CLI_VERSION" ]]; then
  echo "Expected Alpaca CLI ${ALPACA_CLI_VERSION}, got ${installed_version}." >&2
  exit 1
fi
printf '%s\n' "$installed_version"
