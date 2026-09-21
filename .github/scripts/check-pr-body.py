#!/usr/bin/env python3
"""Fail a pull request whose description is missing, or long enough to go unread.

Counts prose only. Fenced code blocks, table rows, headings, links' URLs and the
AI disclosure line do not count, because structure and evidence are not the thing
that makes a description hard to read. Sprawling prose is.

Reads the body from PR_BODY, or from a file given as the first argument.
"""
import os
import re
import sys

MAX_WORDS = 350
MIN_WORDS = 20


def prose_words(body: str) -> int:
    text = re.sub(r"```.*?```", " ", body, flags=re.S)          # fenced code
    # Inline code goes before URLs on purpose. A URL pattern that ends at
    # whitespace eats the closing backtick of `https://...`, and the orphan then
    # pairs with a later backtick and swallows everything between. That silently
    # under-counted a 362-word description as 71.
    text = re.sub(r"`[^`\n]*`", " ", text)                      # inline code
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("|"):                             # table rows
            continue
        if stripped.startswith("#"):                             # headings
            continue
        if stripped.startswith("Generated-by:"):                 # required disclosure
            continue
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)         # keep link text, drop URL
    text = re.sub(r"https?://[^\s`)\]]+", " ", text)              # bare URLs
    return len([w for w in text.split() if any(c.isalnum() for c in w)])


def main() -> int:
    body = open(sys.argv[1], encoding="utf-8").read() if len(sys.argv) > 1 else os.environ.get("PR_BODY", "")
    words = prose_words(body)
    if words < MIN_WORDS:
        print(f"The description is {words} prose words. Say what changed and why, in at least {MIN_WORDS}.")
        return 1
    if words > MAX_WORDS:
        print(
            f"The description is {words} prose words, over the {MAX_WORDS} limit.\n"
            "Cut it rather than raising the limit. Code blocks, tables and headings are\n"
            "not counted, so move detail into them, or leave it in the commits where it\n"
            "belongs."
        )
        return 1
    print(f"{words} prose words, within the {MAX_WORDS} limit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
