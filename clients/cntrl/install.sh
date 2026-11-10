#!/bin/sh
# Install the cntrl CLI from the server this script was downloaded from.
#
#   curl -fsSL <server>/cli/install.sh | sh
#
# The server fills in BASE when it serves this file. Installs into
# ~/.local/share/cntrl and puts `cntrl` and `jav3` (the old name) launchers in
# ~/.local/bin (override with CNTRL_BIN; JAV3_BIN still works). Its libraries (httpx, and textual for the TUI) come
# from the system python if it already has both, else from a private venv —
# never `pip install` into the system interpreter.
# Everything runs inside main(), and the call is wrapped in `{ ...; }` so a
# download cut short anywhere — even inside the last line — is a syntax error
# that executes nothing.
set -eu

# Same constraint as the server's requirements.txt; the upper bound keeps an
# incompatible major release from landing on a fresh install unannounced.
HTTPX_SPEC='httpx>=0.27,<1'
TUI_SPECS='textual>=8,<9'

main() {
  BASE="@@BASE@@"
  bin_dir="${CNTRL_BIN:-${JAV3_BIN:-$HOME/.local/bin}}"
  data="${XDG_DATA_HOME:-$HOME/.local/share}"
  share="$data/cntrl"
  old="$data/jav3"

  command -v python3 >/dev/null 2>&1 || { echo "cntrl needs python3 (3.11+)" >&2; exit 1; }
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
    || { echo "cntrl needs Python 3.11 or newer ($(python3 -V 2>&1) found)" >&2; exit 1; }

  mkdir -p "$share" "$bin_dir"
  tmp="$share/cntrl.download"
  # an older server only knows the old name
  curl -fsSL "$BASE/cli/cntrl" -o "$tmp" 2>/dev/null || curl -fsSL "$BASE/cli/jav3" -o "$tmp"
  python3 -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' "$tmp" \
    || { echo "downloaded CLI is not valid Python; not installing" >&2; rm -f "$tmp"; exit 1; }
  mv "$tmp" "$share/cntrl"

  py="$(command -v python3)"
  if ! python3 -c 'import httpx, textual' >/dev/null 2>&1; then
    if [ ! -x "$share/venv/bin/python" ]; then
      python3 -m venv "$share/venv" || {
        echo "could not create a venv — install python3-venv, or httpx and" >&2
        echo "textual via your package manager, then re-run this installer" >&2
        exit 1; }
    fi
    # word-split on purpose: TUI_SPECS may hold several specs
    # shellcheck disable=SC2086
    "$share/venv/bin/python" -m pip install --quiet --disable-pip-version-check \
      "$HTTPX_SPEC" $TUI_SPECS
    py="$share/venv/bin/python"
  fi

  # `jav3` stays as a second launcher so old habits and scripts keep working; an old
  # install in ~/.local/share/jav3 is left where it is, and both launchers now run
  # the new one
  for name in cntrl jav3; do
    cat > "$bin_dir/$name" <<EOF
#!/bin/sh
exec "$py" "$share/cntrl" "\$@"
EOF
    chmod 755 "$bin_dir/$name"
  done

  [ ! -d "$old" ] || echo "note: the old install in $old is no longer used; remove it when you like"
  echo "installed: $bin_dir/cntrl (and $bin_dir/jav3)"
  case ":$PATH:" in *":$bin_dir:"*) ;; *) echo "note: $bin_dir is not on your PATH" ;; esac
  echo "next: cntrl         (logs in on first run: paste the line from Settings → Add computer)"
}

{ main "$@"; exit; }
