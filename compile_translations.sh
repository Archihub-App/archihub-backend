#!/bin/bash

set -euo pipefail

ROOT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

pybabel compile -d "$ROOT_DIR/app/translations"

for dir in "$ROOT_DIR"/app/plugins/*/translations; do
    if [ -d "$dir" ] && find "$dir" -path '*/LC_MESSAGES/messages.po' -print -quit | grep -q .; then
        pybabel compile -d "$dir"
    fi
done

echo "Translations compiled successfully"