#!/bin/bash

# get the current directory
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

# get the root directory
ROOT_DIR=$DIR

if [ ! -d "/usr/share/tesseract-ocr/4.00/tessdata/" ]; then
    mkdir -p "/usr/share/tesseract-ocr/4.00/tessdata/"
fi

if [ ! -d "/app/uploads" ]; then
    mkdir -p "/app/uploads"
fi

if [ ! -d "/app/userfiles" ]; then
    mkdir -p "/app/userfiles"
fi

if [ ! -d "/app/webfiles" ]; then
    mkdir -p "/app/webfiles"
fi

# get the requirements file
REQUIREMENTS_FILE="$ROOT_DIR/requirements.txt"

# if the requirements file does not have an empty line at the end, add one
sed -i -e '$a\' "$REQUIREMENTS_FILE"

# make a copy of the requirements file
cp "$REQUIREMENTS_FILE" "$REQUIREMENTS_FILE.bak"

# iterate over all requirements.txt files in the plugins directory
for file in $ROOT_DIR/app/plugins/*/requirements.txt; do
    # if the file does not have an empty line at the end, add one
    sed -i -e '$a\' "$file"

    # append the requirements to the requirements.txt file
    cat "$file" >> "$REQUIREMENTS_FILE"
done

# iterate over all plugins directories and check if there are tessdata folder and files in them
for dir in $ROOT_DIR/app/plugins/*; do
    # check if the tessdata folder exists
    if [ -d "$dir/tessdata" ]; then
        # iterate over all files in the tessdata folder
        for file in $dir/tessdata/*; do
            # move the file to /usr/share/tesseract-ocr/4.00/tessdata/
            cp "$file" "/usr/share/tesseract-ocr/4.00/tessdata/"
        done
    fi
done


# remove duplicate requirements
awk '!seen[$0]++' "$REQUIREMENTS_FILE" > "$REQUIREMENTS_FILE.tmp" && mv "$REQUIREMENTS_FILE.tmp" "$REQUIREMENTS_FILE"

# remove empty lines
sed -i '/^$/d' "$REQUIREMENTS_FILE"

# remove comments
sed -i '/^#/d' "$REQUIREMENTS_FILE"

# save the requirements file
echo "Requirements file generated at $REQUIREMENTS_FILE"