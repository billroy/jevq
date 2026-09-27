#!/bin/sh

set -eu

if [ "$#" -gt 1 ]; then
    printf 'Usage: %s [DESTINATION]\n' "$0" >&2
    exit 2
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
destination=${1:-"${HOME:?HOME is not set}/.local/bin/jevq"}

install -d -m 755 "$(dirname -- "$destination")"
install -m 755 "$script_dir/jevq.py" "$destination"
printf 'Installed jevq to %s\n' "$destination"
