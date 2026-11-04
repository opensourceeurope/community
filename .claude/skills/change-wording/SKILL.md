---
name: change-wording
description: Use when someone asks to change, reword, shorten, translate or add a sentence to anything the application pipeline says to people - an email to applicants, a Slack message reviewers read, or the text of the application form - especially when the person asking does not work in n8n or code and expects to see the result before it goes live.
---

# Changing what the pipeline says

The person asking usually knows the wording they want and nothing about n8n. Your
job is to find every place the text lives, change them together, show them the
result, and ship it through a pull request. They never open n8n, and neither do
you: the live instance is changed only by merging to `main`.

Talk to them in their language and in plain words. Say "the rejection email", not
"Render decision email". Show the message, not the diff.

## 1. Pin down what changes

Restate the request as the message or messages it touches and the new wording, and
get a yes before editing. If it is vague ("make it friendlier"), draft the new text
and let them correct it.

| They mean | It lives in | Notes |
|---|---|---|
| An email to applicants | `automation/emails/<name>.md` **and** the same text in the render node that sends it | The README "Email templates" tables say which file goes out when. Check 10 fails if the two differ. |
| A Slack message | The `Render ...` Code node feeding that Slack node | `message-preview.py --all` lists every message with its node name. |
| Form wording | `automation/n8n/form-ose.json` | Page titles, descriptions and Custom HTML are wording. A field's label is not, see below. |

Find the node by searching the exports for a phrase from the current text.

These are not wording changes. Say so, and handle them under the AGENTS.md rules
rather than this skill:

- **Renaming a form field's label**: the label is the key the answer is stored
  under, and the invitation email's prefill link uses one of them.
- **A new placeholder** in an email: the render node must supply its value.
- **Changing who gets an email, or when**: that is the pipeline's logic.
- **Model prompts** in `automation/prompts/`: they need the model tests.

## 2. Edit

1. Work in a worktree: `.claude/scripts/worktree.sh new <branch>`.
2. Invoke the `n8n-skills:n8n` skill, as AGENTS.md requires before touching any
   workflow.
3. Change the exports in `automation/n8n/` with a small Python script, never by
   hand and never on the live instance. Load the JSON, replace the old text in
   the named node, assert it occurred exactly once, and write it back with
   `json.dump(doc, f, indent=2, ensure_ascii=False)` plus a final newline. That
   reproduces the file byte for byte apart from your edit. A node that exists in
   both intake workflows, such as `Render decision email`, changes in both.
4. For an email, make the same change in its `automation/emails/*.md` file.
5. Keep placeholders such as `{{ collective_name }}` exactly as they are.

## 3. Show it, and repeat until they are happy

- Emails and Slack: **REQUIRED SUB-SKILL:** `message-preview`.
- Form pages: **REQUIRED SUB-SKILL:** `form-preview`.

Ask whether it reads right. Change and preview again until they say yes.

## 4. Check and ship

```bash
python3 automation/scripts/check-workflows.py
```

It must pass. Then commit, push and open a pull request with a short description:
why, and what text changed, quoted before and after, plus the `Generated-by` line
from AGENTS.md. Wait for CI, which renders every message again.

Merge only when they say so. Merging is publishing: within ten minutes the box
deploys `main` to the live instance. The next real applicant reads the new text.

## 5. Confirm it is live

Wait for the deploy, about ten minutes after the merge. Then, in your worktree,
with `N8N_API_URL` and `N8N_API_KEY` set as "Providing the n8n API key to Claude
Code" in `automation/README.md` describes:

```bash
git fetch origin && git checkout --detach origin/main
python3 automation/scripts/export-workflows.py
git status --short automation/n8n
```

It reads the live workflows into the exports and changes nothing on the instance.
It has no `--help`: running it always exports. An empty `git status` means the
live instance runs exactly what was merged. Tell them it is live, or what differs.

## Common mistakes

- Changing the `.md` file and not the node, or one intake workflow and not the
  other. Check 10 catches both, but only if you run it.
- Showing the person a code diff instead of the preview.
- Touching `DRY_RUN` or any setting. Wording never needs it.
- Putting a real collective, person or address in the preview samples. The
  repository is public.
