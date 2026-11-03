---
name: form-preview
description: Use when a change touches the application form (form-ose.json, apply 4) and someone wants to see a page as an applicant would, or before calling a form change done. Screenshots form pages from a local throwaway n8n with a headless browser, without touching the production instance or opening a browser.
---

# Previewing form pages

The form is client-rendered and n8n filters its custom HTML and CSS, so reading
the export or fetching the live form proves nothing about what an applicant
sees. `automation/scripts/form-preview.py` renders pages for real: it starts
the n8n image production runs in a throwaway container, imports one preview
workflow per page, and screenshots each page from a Playwright container. Both
containers and their network are removed afterwards. Nothing reaches the
production instance.

## Run it

```bash
python3 automation/scripts/form-preview.py --page "Form page 5" --out /path/to/out
python3 automation/scripts/form-preview.py --all --out /path/to/out
python3 automation/scripts/form-preview.py --export /path/to/edited.json --page "Form page 6"
```

It prints one PNG path per page, then the path of an `index.html` in `--out`
that shows every PNG there. Use the scratchpad for `--out` and for edited
exports.

## Share them

Read the PNGs yourself first, and fix anything wrong before the user sees it.
Then show them in the user's browser, served from localhost:

1. Put the pictures the user should see into one `--out` folder, with names
   that say what each one shows and number them in reading order, for example
   `1-admin-page.png` and `2-admin-page-no-admin-list.png`. Several runs can
   write into the same folder, or you can copy the PNGs in afterwards.
2. Start the server in the background (Bash `run_in_background`). It rebuilds
   the gallery and serves it on 127.0.0.1 only:
   ```bash
   python3 automation/scripts/form-preview.py --serve-only --serve 8765 --out /path/to/out
   ```
   Passing `--serve 8765` to a normal run serves straight after it.
3. Wait until `http://127.0.0.1:8765/` answers, then run
   `open http://127.0.0.1:8765/` to show it to the user. Give them the URL as
   well, along with one line per picture saying what to look at.
4. If the port is taken, pick another. Leave the server running while the user
   reviews, and stop it when they are done.

- `--export` defaults to `automation/n8n/form-ose.json`. To preview a change
  before it is in the export, write an edited copy of the export and point
  `--export` at it.
- `--fixture` defaults to `automation/test/fixtures/form-preview.json`. It
  holds the sample data, keyed by node name. A page whose expressions read
  `$('Some node')` gets a stub node of that name returning the fixture entry.
  The script stops and names any node the fixture lacks. To show a variant,
  such as a row with an empty column, copy the fixture and change that entry.
- The fixture's data is invented. Never put a real collective, person or
  address in it. This repository is public.

## What it does not cover

- `Form page 1` is the start page and cannot be previewed this way.
- It renders one page in isolation. It does not walk the branches, check that
  answers are stored, or run the persist nodes. Those need their own tests.
- Fields come out empty unless the page itself sets `defaultValue`, as
  `Form page 6` does.

## When it fails

A failed page is saved as `failed-<page>.png` and the last n8n log lines are
printed. The usual causes are an expression that does not parse, often
adjacent `}}` inside the expression, and a fixture entry with the wrong shape,
such as an `answers` column that is not a JSON string.

Needs Docker. The first run pulls the Playwright image, which takes a few
minutes.
