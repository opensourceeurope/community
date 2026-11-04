---
name: slack-preview
description: Use when a change touches the text of a Slack message the pipeline posts, such as a "Render ..." Code node that feeds a Slack node, its wording, emoji, escaping or packing, or when someone asks what a Slack message looks like. Also before calling such a change done, since a reviewer cannot judge a message from the code that builds it.
---

# Previewing Slack messages

Every Slack post in `automation/n8n/` is fed by a Code node that builds `text`. The
export shows the template, never the message: escaping, emoji, bold, line breaks and
the continuation of long replies only happen when the node runs.
`automation/scripts/slack-preview.py` runs the committed nodes in a throwaway
container on the image production runs, with sample input from
`automation/test/fixtures/slack-preview.json`. Nothing is posted.

## Run it

Edit the export in your worktree first, then:

```bash
python3 automation/scripts/slack-preview.py --out /path/to/out
python3 automation/scripts/slack-preview.py --node "Render AI review thread reply" --out /path/to/out
python3 automation/scripts/slack-preview.py --all --out /path/to/out
```

With no `--node` it renders every message whose node differs from `origin/main`,
before and after. `--node` takes the render node or the Slack node it feeds. It
writes `index.html` into `--out`, using the scratchpad, and exits 1 if a node threw.

## Show it

1. Read the messages in `index.html` yourself and fix anything wrong first.
2. Run `open /path/to/out/index.html` so the user sees it in their browser.
3. Tell them which messages changed and what to look at, one line each, and that
   **Open in Block Kit Builder** shows a message as Slack renders it. Builder needs
   them signed in to Slack and nothing more.

## Changing the samples

- A new Slack message needs a fixture entry, or the script stops and names it. An
  entry lists its `feeds` in order: one per `$('...')` the node reads, and the last
  one is the node's input. CI runs `--all`, so a missing entry fails the PR.
- To show a variant, such as an empty description or a very long answer, add a row
  to that entry's input rather than a new entry. Each row renders as a message.
- The data is invented and this repository is public. Never put a real collective,
  person or address in it. Keep the hostile samples such as `<!channel>`: they
  show that `escapeSlack` still works.

## What Builder does not show exactly

- The workflows post plain `text`, and Builder only renders blocks, so each message
  is shown as `section` blocks. Bold, italics, emoji, links and line breaks match.
  Leading spaces used to indent a line are dropped in Builder, so judge
  indentation from the raw text on the page.
- A section holds 3000 characters, so a longer message appears as several sections
  in Builder. It still posts as one message.
- Thread links and mentions point at sample IDs and go nowhere.
