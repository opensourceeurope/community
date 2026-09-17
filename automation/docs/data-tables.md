# Data tables

## `ose_applications`

The n8n Data table that holds every application's state, from intake through
decision. Keyed on `slug`, the Open Collective collective slug. One row per
application. n8n Data tables live inside the same Postgres as everything else
n8n stores, so the backup procedure in `automation/infra/README.md` covers
this table too.

Data tables support only `Boolean`, `Date`, `Number` and `String` columns, so
anything structured (the AI verdict fields, form answers) is stored as a JSON
string and parsed by whichever node reads it.

Column names are final. The workflows in `automation/n8n/` use them verbatim.
"Written by" names the workflow that owns each column.

| Column | Type | Written by | Notes |
|---|---|---|---|
| `slug` | String | intake | Open Collective collective slug. Primary key. |
| `org` | String | intake | Always `OSE`, the only org this automation serves. Kept as a column so the `org_name` derivation stays data driven. See "Filling the email and prompt templates" below. |
| `host_slug` | String | intake | The OC host account the application targets. Always `europe`, the OSE host slug on Open Collective. The decision check queries the application's status against exactly this host. |
| `collective_name` | String | intake | Display name, from the host admin API. |
| `collective_url` | String | intake | Public Open Collective page for the collective. |
| `description` | String | intake | The collective's public one line description. An input to the AI review. |
| `long_description` | String | intake | The collective's public long description. An input to the AI review. |
| `repository_url` | String | intake | First GitHub or GitLab social link on the collective, if any. An input to the AI review. |
| `website_url` | String | intake | First website social link on the collective, if any. An input to the AI review. |
| `application_message` | String | intake | The message the applicant wrote when applying on OC. An input to the AI review. |
| `applicant_email` | String | intake | The application's `customData` contact email when present, otherwise the first collective admin email visible to the host admin. The `collective.apply` webhook payload carries no application data, so intake reads all of this from the API. Personal data. |
| `stage` | String | every workflow, as the application progresses | One of the nine stage values below. |
| `applied_at` | Date | intake | The application's `createdAt` on Open Collective. |
| `ai_verdict` | String | AI review | One of `fits`, `wrong_host`, `not_open_source`, `unclear`. See `automation/prompts/verdict.schema.json`. |
| `ai_confidence` | Number | AI review | 0 to 1, from the verdict object. |
| `ai_reasoning` | String | AI review | Reviewer facing explanation from the model. Never shown to the applicant. |
| `ai_applicant_message` | String | AI review | Applicant facing text from the model. Used in the `advised-wrong-host` and `advised-not-open-source` email templates. Never presented as a decision. |
| `ai_model` | String | AI review | The model identifier used for this review (`AI_MODEL`), so old verdicts stay traceable after a model or prompt change. |
| `ai_reviewed_at` | Date | AI review | When the review ran. |
| `contact_email` | String | form workflow | Given by the applicant on form page 1. Distinct from `applicant_email`. This is who the applicant says to contact, which may differ from the address the OC application came from. Personal data. |
| `form_invited_at` | Date | follow-up | When the invitation email was sent. The template varies by verdict, and every reviewed application is invited. |
| `form_reminded_at` | Date | follow-up | When the `reminder` email was sent, after silence following the invite. Driven by `REMINDER_AFTER_MINUTES` and `SWEEP_CRON`. |
| `form_page` | Number | form workflow | Which page of the multi page form the applicant has reached. Answers persist per page. |
| `answers` | String (JSON) | form workflow | Form responses so far. Shape below. |
| `form_submitted_at` | Date | form workflow | When the final form page was submitted. |
| `slack_notified_at` | Date | form workflow and follow-up | When the pipeline reached a stage that tells Slack about this row, either ready for evaluation or an escalation. It records the attempt, not the delivery: a Slack post that failed or was skipped for a row with no thread still stamps it, because the stage advanced either way. Nothing reads it. |
| `decision` | String | intake (decision branch) | `approved` or `rejected`, from the human decision made on Open Collective. |
| `decided_at` | Date | intake (decision branch) | When that decision was recorded. |
| `dry_run` | Boolean | every workflow that emails an applicant | Set when an applicant-facing email for this row went to `DRY_RUN_RECIPIENT` instead of the applicant, so a row that advanced during a rehearsal is visibly a rehearsal. Slack messages are internal and always send, so they never set it. |
| `freshdesk_ticket_id` | String | none | Reserved for a possible future Freshdesk integration. No workflow writes it. |
| `slack_channel_id` | String | intake | The channel holding this application's Slack thread. Written once, by whichever intake workflow created the row. |
| `slack_thread_ts` | String | intake | The parent message's timestamp, which is the thread anchor. A Slack identifier, not a number: stored as a number it rounds and every reply fails. |
| `ai_summary` | String | apply 5 | The summary posted to the thread: the description, then the gaps as lines. Empty means apply 5 has not succeeded for this row, which is what makes its sweep idempotent. |
| `ai_summary_model` | String | apply 5 | The model that produced it, so a summary stays traceable across a model change. |
| `ai_summarised_at` | Date | apply 5 | When the summary was written. |
| `readme_source` | String | apply 5 | `github`, `gitlab`, `generic` or `none`, so a reviewer can see whether the summary had a README to work from. |

### The nine `stage` values

Every reviewed application is invited to the form. The AI verdict changes
only which email carries the invitation, never whether it is sent. An
`unclear` or adverse first read is a hint in the email, not a gate. The form
is where a thin Open Collective description gets filled out.

In order through a normal application, plus the escalation branch:

1. `applied`: intake has created the row.
2. `reviewed`: the AI review has run.
3. `form_invited`: the form invitation has been sent.
4. `form_started`: the applicant has submitted at least one form page.
5. `form_submitted`: the applicant has submitted the final form page.
6. `awaiting_decision`: Slack has been notified, and a human needs to decide.
7. `approved`: the terminal decision, recorded from Open Collective.
8. `rejected`: the terminal decision, recorded from Open Collective.
9. `escalated`: no activity within `ESCALATE_AFTER_MINUTES` of the reminder,
   so the sweep flags it for a human. Not terminal. An escalated row resumes
   its normal path once the applicant acts.

### Shape of `answers`

```json
{
  "page": 3,
  "submitted_at": "2026-09-01T10:04:00Z",
  "responses": { "question_id": "answer" }
}
```

`page` is the last page these responses cover. `responses` accumulates across
pages as the applicant progresses, keyed by question ID.

The question IDs of `form-ose`, in page order. Page 1 contributes
`applicant_type`, the answer to `What are you applying as?`, which is asked on
every path.

Page 2 depends on that answer. A single project, or a group of projects,
is asked `repository_url`, `project_website`, `licence` and
`open_development`. A community, meetup or events collective is asked
`community_home`, `community_open` and `project_website` instead, because it
has no codebase to name. A key the applicant was never asked is stored empty,
and on a row written before its question existed it is missing altogether, so
anything reading `repository_url`, `licence`, `open_development`,
`community_home`, `community_open` or `project_website` has to tolerate both.
Page 3 has
`legal_entity`, `legal_entity_detail`, `operating_duration`,
`fundraising_to_date`, `fundraising_goal`, `funding_sources`,
`expected_expenses` and `payee_countries`.
`legal_entity_detail` is optional and only meaningful when `legal_entity` is
`Yes`. Page 4 has
`activities`, `mission_fit` and `notes`.

Page 1 asks `contact_email` and the collective URL, which are stored as
columns rather than in `answers`, and the applicant type, which is stored in
`answers` as `applicant_type`. The questions adapt the documented application
questions and add the open source evidence the advisory emails point
applicants at: repository, licence, and open development.

## Filling the email and prompt templates

Not every `{{ placeholder }}` used in `automation/emails/*.md` and
`automation/prompts/review.user.md` comes from a column on this table:

- `org_name`: the templates need a display name ("Open Source Europe"), but
  the table only stores the code (`org`: `OSE`). The render step of the
  sending workflow maps the code to the display name at send time. `org_name`
  itself is never stored.
- `form_url`: the form link is static configuration, `FORM_URL_OSE` in the
  n8n environment (see `automation/infra/docker-compose.yml` and
  `automation/infra/README.md`). There is no `form_url` column and no token
  for it in this table.
- `collective_name`, `ai_applicant_message` and the rest of the template
  placeholders come straight from the columns above.

A form submission is matched back to its row without a token in the URL:
form page 1 asks the applicant for their Open Collective collective URL, and
the workflow derives the slug from it to look up the row keyed on `slug`.

## Personal data

`applicant_email` and `contact_email` are the only personal data in this
table. Neither is ever sent to the model. `automation/prompts/review.user.md`
sends only `org_name`, `collective_name`, `collective_slug`, `description`,
`long_description`, `repository_url`, `website_url` and
`application_message`. No name and no email address, for either the applicant
or the collective's contact.

The summary in apply 5 sends a second set: the form answers under the keys listed
in "Shape of `answers`", the collective's public description, the repository URL
and the fetched README. It selects them from a fixed list rather than iterating
the row, which is what keeps the two address columns out of it. See
`automation/prompts/summary.system.md`.
