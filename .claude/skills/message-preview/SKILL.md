---
name: message-preview
description: Use when a change touches the text of a Slack message or an email the pipeline sends, such as a "Render ..." Code node that feeds a Slack or email node, a file in automation/emails, its wording, emoji, placeholders, or escaping, or when someone asks what a message or email looks like. Use it also before calling such a change done, because nobody can judge a message from the code that builds it.
---

# Previewing Slack messages and emails

Every Slack post and email in `automation/n8n/` is fed by a Code node that builds
it. The export shows the template, never the message: placeholders, escaping,
emoji, the prefilled form link, and the continuation of long replies appear only
when the node runs. `automation/scripts/message-preview.py` runs the committed
nodes in a throwaway container on the n8n image that production runs, with sample input
from `automation/test/fixtures/message-preview.json`. Nothing is posted or sent.

## Run it

Edit the export in your worktree first, then:

```bash
python3 automation/scripts/message-preview.py --out /path/to/out
python3 automation/scripts/message-preview.py --node "Render reminder" --out /path/to/out
python3 automation/scripts/message-preview.py --all --out /path/to/out
```

Put `--out` in your scratchpad. With no `--node`, it renders every message whose
node this branch changed, before and after. The before version is `origin/main`
at the point where the branch left it, so changes merged since then are not shown as yours. If it prints
`no message differs`, nothing changed and it writes no page. `--node` takes the
render node or the Slack or email node it feeds. The run writes `index.html` into
`--out`, and `builder-links.txt` with one Block Kit Builder link per Slack message, in
the order it printed them. It exits 1 if a node threw or sent nothing for its sample.

## Show it

1. Read the messages in `index.html` yourself and fix anything wrong first. The
   page does not highlight what changed, so compare before and after line by line.
2. Show every Slack message as its Block Kit Builder link, before and after
   alike: open them with `xargs -n 1 open < /path/to/out/builder-links.txt`
   and put each link in your reply, labeled with the message and whether it
   is before or after. Never hand the user `index.html` or any `file://` path
   for a Slack message. Builder shows the message as Slack does, and the page
   does not.
   Quote the changed line in your reply when the change is easy to miss
   side by side.
3. Open `index.html` only for an email, which Builder cannot show, or to check
   indentation yourself, since Builder drops leading spaces.
4. Tell them which messages changed and what to look at, one line each. Builder
   needs them signed in to Slack and nothing more.

## Changing the samples

Follow these rules for the sample data:

- A new message needs a fixture entry, or the script stops and names it. An entry
  lists its `feeds` in order: one per `$('...')` the node reads, and the last one
  is the node's input. CI runs `--all`, so a missing entry fails the PR.
- To show a variant, such as another verdict or an empty description, add a row
  to that entry's input rather than a new entry. Each row renders as a message.
- The data is invented and this repository is public. Never put a real Collective,
  person or address in it. Keep the hostile samples such as `<!channel>`: they
  show that `escapeSlack` still works.

## What the preview does not show exactly

The preview differs from the real message in the following ways:

- Emails render as if `DRY_RUN` were `false`, so the subject is the applicant's.
  In a dry run, when `DRY_RUN` is anything but `false`, the subject names the
  intended recipient instead and the mail goes to `DRY_RUN_RECIPIENT`. Line
  breaks in the body are the ones the template has.
- The workflows post Slack text, and Builder only renders blocks, so each message
  is shown as `section` blocks. Bold, italics, emoji, links, and line breaks match.
  Builder drops leading spaces that indent a line, so judge indentation from the
  raw text on the page.
- A section holds 3000 characters, so a longer Slack message appears as several
  sections in Builder. It still posts as one message.
- Thread links and mentions point at sample IDs and go nowhere.
