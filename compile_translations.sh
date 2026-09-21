#!/bin/bash
#
# Compile every .po into the .mo the runtime actually reads.
#
# EDITING A .po CHANGES NOTHING UNTIL THIS RUNS. gettext loads the compiled
# catalog only, so a translation added to the source file and not compiled is
# silently absent - the message renders in English and nothing reports it.

set -euo pipefail

ROOT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

pybabel compile -d "$ROOT_DIR/archihub/translations"

# A plugin ships its catalog inside its own directory, which is what lets one be
# installed by copying it in - its translations arrive with it.
for dir in "$ROOT_DIR"/archihub/plugins/*/translations; do
    if [ -d "$dir" ] && find "$dir" -path '*/LC_MESSAGES/messages.po' -print -quit | grep -q .; then
        pybabel compile -d "$dir"
    fi
done

echo "Translations compiled successfully"
