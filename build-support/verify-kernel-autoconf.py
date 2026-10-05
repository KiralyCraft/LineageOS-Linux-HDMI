#!/usr/bin/env python3
"""Compare effective autoconf definitions, allowing kheaders' removed comments."""
import argparse
import pathlib
import re


def definitions(text):
    # gen_kheaders.sh removes the generated leading C comment. Only ignore
    # leading comments, never comment-like text inside a CONFIG string value.
    text = text.lstrip()
    while text.startswith("/*"):
        end = text.find("*/", 2)
        if end < 0:
            raise ValueError("unterminated header comment")
        text = text[end + 2:].lstrip()
    result = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"#define (CONFIG_[A-Za-z0-9_]+) (.+)", line)
        if not match:
            raise ValueError(f"unexpected autoconf content: {line!r}")
        name, value = match.groups()
        if name in result:
            raise ValueError(f"duplicate definition: {name}")
        result[name] = value
    if not result:
        raise ValueError("no configuration definitions")
    return result


def compare(runtime, rebuilt):
    expected, actual = definitions(runtime), definitions(rebuilt)
    missing = sorted(expected.keys() - actual.keys())
    added = sorted(actual.keys() - expected.keys())
    changed = sorted(name for name in expected.keys() & actual.keys()
                     if expected[name] != actual[name])
    if missing or added or changed:
        raise ValueError(f"configuration definitions differ: missing={missing}, "
                         f"added={added}, changed={changed}")
    return len(expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=pathlib.Path)
    parser.add_argument("rebuilt", type=pathlib.Path)
    args = parser.parse_args()
    try:
        count = compare(args.runtime.read_text(), args.rebuilt.read_text())
    except (OSError, ValueError) as error:
        parser.exit(1, f"autoconf verification failed: {error}\n")
    print(f"Effective autoconf definitions: PASS ({count} definitions)")


if __name__ == "__main__":
    main()
