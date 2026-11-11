#!/bin/bash
#
# Compile every .po into the .mo the runtime actually reads.
#
# EDITING A .po CHANGES NOTHING UNTIL THIS RUNS. gettext loads the compiled
# catalog only, so a translation added to the source file and not compiled is
# silently absent - the message renders in English and nothing reports it.

set -euo pipefail

ROOT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

compile_dir() {
    local dir="$1"
    if command -v pybabel >/dev/null 2>&1; then
        pybabel compile -d "$dir"
    elif command -v msgfmt >/dev/null 2>&1; then
        find "$dir" -name 'messages.po' | while read -r pofile; do
            msgfmt "$pofile" -o "${pofile%.po}.mo"
        done
    else
        echo "Error: neither pybabel nor msgfmt found in PATH" >&2
        return 1
    fi
}

compile_dir "$ROOT_DIR/archihub/translations"

# A plugin ships its catalog inside its own directory, which is what lets one be
# installed by copying it in - its translations arrive with it.
for dir in "$ROOT_DIR"/archihub/plugins/*/translations; do
    if [ -d "$dir" ] && find "$dir" -path '*/LC_MESSAGES/messages.po' -print -quit | grep -q .; then
        compile_dir "$dir"
    fi
done

echo "Translations compiled successfully"
