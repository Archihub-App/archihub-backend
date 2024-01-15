# script that generates requirements.txt file iterating over all /app/plugins/*/requirements.txt files
# and appending them to the requirements.txt file in the root of the project

# get the current directory
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

# get the root directory
ROOT_DIR=$DIR

# get the requirements file
REQUIREMENTS_FILE="$ROOT_DIR/requirements.txt"

# make a copy of the requirements file
cp "$REQUIREMENTS_FILE" "$REQUIREMENTS_FILE.bak"

# iterate over all requirements.txt files in the plugins directory
for file in $ROOT_DIR/app/plugins/*/requirements.txt; do
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
            mv "$file" "/usr/share/tesseract-ocr/4.00/tessdata/"
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