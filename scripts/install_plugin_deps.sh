#!/bin/bash
#
# Install the dependencies every installed plugin declares.
#
# A plugin is a directory somebody copies in. It may declare:
#
#   requirements.txt   Python packages, one pip requirement per line.
#   packages.txt       SYSTEM packages, one apt package name per line.
#   tessdata/          Tesseract language files, copied to the tessdata dir.
#
# WHY packages.txt EXISTS. Plugin authors have always needed system packages and
# have always had nowhere to say so, so they wrote it in prose at the top of
# requirements.txt - where the merge step then deleted it, because it stripped
# comment lines. An operator following the documented "unzip a plugin and
# rebuild" workflow got a plugin that installed cleanly and failed at its first
# task with a FileNotFoundError from a binary nothing had installed.
#
# WHY CONSTRAINTS. Plugin requirements are third-party text installed into the
# same environment as the backend. Without a constraint file a plugin pinning an
# older version of a shared library silently downgrades it - including the
# security floors the backend deliberately sets. `-c` lets a plugin add packages
# but never move one the backend already pinned.
#
# WHY TWO PIP PASSES. PEP 517 builds each package in an isolated environment
# containing only its declared build requirements. A source distribution whose
# setup.py imports a *runtime* dependency therefore cannot build, no matter what
# order the requirements file lists things in - the classic case being a package
# whose setup.py does `import torch` to detect CUDA. Plain requirements are
# installed first, then VCS and archive requirements are installed against the
# environment that now contains them.
#
# Usage: install_plugin_deps.sh <plugins-dir> [<plugins-dir> ...]

set -euo pipefail

CONSTRAINTS="${PLUGIN_CONSTRAINTS:-/tmp/constraints.txt}"
TESSDATA_DIR="${TESSDATA_DIR:-}"

# An apt package name. Deliberately strict: this text comes from a third-party
# directory and is about to be handed to a command running as root, so anything
# that is not plainly a package name is refused rather than escaped.
PACKAGE_PATTERN='^[a-z0-9][a-z0-9+.-]*$'

log() { echo "[plugin-deps] $*"; }

resolve_tessdata_dir() {
    # Derived, never hardcoded: the path carries Tesseract's major version, so a
    # base image moving from 4 to 5 changes it. Writing to a stale path succeeds
    # and installs the languages where nothing will look for them.
    if [[ -n "$TESSDATA_DIR" ]]; then
        echo "$TESSDATA_DIR"
        return
    fi
    if command -v tesseract >/dev/null 2>&1; then
        local prefix
        prefix="$(tesseract --print-parameters 2>/dev/null | head -0 || true)"
        for candidate in /usr/share/tesseract-ocr/*/tessdata /usr/share/tessdata; do
            if [[ -d "$candidate" ]]; then
                echo "$candidate"
                return
            fi
        done
    fi
    echo ""
}

install_system_packages() {
    local manifest="$1" plugin="$2"
    local -a wanted=()

    while IFS= read -r line; do
        line="${line%%#*}"
        line="$(echo "$line" | tr -d '[:space:]')"
        [[ -z "$line" ]] && continue
        if [[ ! "$line" =~ $PACKAGE_PATTERN ]]; then
            echo "[plugin-deps] ERROR: ${plugin}/packages.txt has an entry that is not a package name: '${line}'" >&2
            exit 1
        fi
        wanted+=("$line")
    done < "$manifest"

    if [[ ${#wanted[@]} -eq 0 ]]; then
        return
    fi

    log "${plugin}: system packages -> ${wanted[*]}"
    apt-get update
    apt-get install -y --no-install-recommends "${wanted[@]}"
    # Shrinks the layer. Non-fatal: this script is written for an image build,
    # but it is runnable by hand and must not abort a working install because
    # the caller cannot write to apt's cache.
    rm -rf /var/lib/apt/lists/* 2>/dev/null || true
}

install_python_packages() {
    local manifest="$1" plugin="$2"
    local plain="/tmp/${plugin}.plain.txt"
    local deferred="/tmp/${plugin}.deferred.txt"

    : > "$plain"
    : > "$deferred"

    while IFS= read -r raw; do
        local line="${raw%%$'\r'}"
        local trimmed="${line#"${line%%[![:space:]]*}"}"
        [[ -z "$trimmed" || "$trimmed" == \#* ]] && continue

        # A requirement pip must build from source goes in the second pass.
        if [[ "$trimmed" == git+* || "$trimmed" == hg+* || "$trimmed" == svn+* \
              || "$trimmed" == bzr+* || "$trimmed" == http://* || "$trimmed" == https://* ]]; then
            echo "$trimmed" >> "$deferred"
        else
            echo "$trimmed" >> "$plain"
        fi
    done < "$manifest"

    if [[ -s "$plain" ]]; then
        log "${plugin}: python packages"
        pip install --no-cache-dir -c "$CONSTRAINTS" -r "$plain"
    fi

    if [[ -s "$deferred" ]]; then
        log "${plugin}: source builds (no build isolation)"
        # setuptools and wheel must be present in the ambient environment,
        # because without isolation pip does not provide them.
        pip install --no-cache-dir --upgrade setuptools wheel
        pip install --no-cache-dir --no-build-isolation -c "$CONSTRAINTS" -r "$deferred"
    fi

    rm -f "$plain" "$deferred"
}

install_tessdata() {
    local source_dir="$1" plugin="$2"

    # An EMPTY tessdata directory is not a declaration. Two plugins ship one as
    # a placeholder, and treating "the directory exists" as "there are language
    # files" turns a plugin that needs nothing into a build failure.
    local -a files=()
    while IFS= read -r -d '' f; do files+=("$f"); done \
        < <(find "$source_dir" -maxdepth 1 -type f -print0 2>/dev/null)

    if [[ ${#files[@]} -eq 0 ]]; then
        return
    fi

    local target
    target="$(resolve_tessdata_dir)"

    if [[ -z "$target" ]]; then
        echo "[plugin-deps] ERROR: ${plugin} ships ${#files[@]} tessdata file(s) but no tessdata directory exists." >&2
        echo "[plugin-deps]        Declare 'tesseract-ocr' in ${plugin}/packages.txt, or set TESSDATA_DIR." >&2
        exit 1
    fi

    log "${plugin}: ${#files[@]} tessdata file(s) -> ${target}"
    mkdir -p "$target"
    cp -f "${files[@]}" "$target"/
}

if [[ $# -eq 0 ]]; then
    echo "usage: $0 <plugins-dir> [<plugins-dir> ...]" >&2
    exit 2
fi

if [[ ! -f "$CONSTRAINTS" ]]; then
    log "writing constraints from the installed environment -> ${CONSTRAINTS}"
    pip freeze --exclude-editable > "$CONSTRAINTS"
fi

found_any=0
for plugins_dir in "$@"; do
    [[ -d "$plugins_dir" ]] || { log "no such directory, skipping: ${plugins_dir}"; continue; }

    for plugin_dir in "$plugins_dir"/*/; do
        [[ -d "$plugin_dir" ]] || continue
        plugin="$(basename "$plugin_dir")"
        [[ "$plugin" == "framework" || "$plugin" == "__pycache__" ]] && continue

        found_any=1
        [[ -f "${plugin_dir}packages.txt" ]] && install_system_packages "${plugin_dir}packages.txt" "$plugin"
        [[ -d "${plugin_dir}tessdata" ]] && install_tessdata "${plugin_dir}tessdata" "$plugin"
        [[ -f "${plugin_dir}requirements.txt" ]] && install_python_packages "${plugin_dir}requirements.txt" "$plugin"
    done
done

if [[ "$found_any" -eq 0 ]]; then
    log "no plugins found in: $*"
fi

log "done"
