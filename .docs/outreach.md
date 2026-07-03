# Email Outreach System

Source files:

- [`portal/api/campaigns.py`](../portal/api/campaigns.py) — campaign generation + staging routes (`APIRouter`)
- [`portal/services/campaign_service.py`](../portal/services/campaign_service.py) — `render_template_string()` and
  `render_draft_emails()`; the blacklist/exclude filtering + Jinja2 rendering logic shared by campaign creation and
  the "add more leads" endpoint
- [`portal/api/dispatcher.py`](../portal/api/dispatcher.py) — async SMTP dispatch worker; also exposes
  `resolve_credential_pool()`, shared by the dispatch loop and the pre-flight check in `POST .../dispatch`
- [`portal/api/templates.py`](../portal/api/templates.py) — email template CRUD (`APIRouter`)
- [`portal/api/credentials.py`](../portal/api/credentials.py) — SMTP credential management (`APIRouter`)
- [`portal/api/blacklist.py`](../portal/api/blacklist.py) — email/domain blacklist (`APIRouter`)
- [`portal/services/csv_import.py`](../portal/services/csv_import.py) — `parse_contacts_csv()`, shared by
  `POST /api/leads/import-csv` and `POST /api/test-campaigns/parse-csv`

All route modules pull the shared `Database` instance via `Depends(get_db)` from
[`portal/api/deps.py`](../portal/api/deps.py) rather than through closures.

---

## Overview

The outreach system lets you turn crawled leads into email campaigns. The workflow is:

1. Create an **Email Template** with Jinja2 variables.
2. Create a **Campaign** selecting leads + a template → draft emails are auto-rendered.
3. Review and edit individual drafts; deselect emails with missing data.
4. Add **SMTP Credentials**.
5. **Dispatch** — a background worker sends emails with rate-limit handling and automatic hard-bounce blacklisting.

A separate **Test Campaign** flow lets you validate SMTP credentials and template rendering against dummy recipients
before sending to real leads.

---

## Email Templates

Templates use [Jinja2](https://jinja.palletsprojects.com/) syntax. Subject and body are both template strings.

**Available variables at render time:**

| Variable            | Source                                                                                  |
|---------------------|-----------------------------------------------------------------------------------------|
| `{{ name }}`        | `lead.person_name` (falls back to `"Official"` in subject, `"[MISSING: name]"` in body) |
| `{{ designation }}` | `lead.designation` (falls back to `""` in subject, `"[MISSING: designation]"` in body)  |

**Example template:**

```
Subject: Important Communication — {{ designation }}, {{ name }}

Body:
Dear {{ designation }} {{ name }},

We are writing to inform you about ...

Regards,
[Your Name]
```

Templates are validated for Jinja2 syntax errors on create and update. Invalid syntax returns HTTP 400 with the line
number and error message.

---

## Campaign Creation

`POST /api/campaigns` runs the following pipeline:

1. **Load leads** from DB by `lead_ids`.
2. **Blacklist filter** — skip any lead whose email is in the `blacklist` table.
3. **Create `Campaign` row** with status `PAUSED`.
4. **Render drafts** via `campaign_service.render_draft_emails()` — for each remaining lead:
    - Detect missing variables (`name`, `designation`).
    - Render subject with clean fallbacks (`"Official"` for missing name).
    - Render body with `[MISSING: field]` markers so reviewers know what to fix.
    - Set `is_selected = False` for any email with missing fields.
5. **Bulk insert** `CampaignEmail` rows with status `DRAFT`.
6. **Restrict SMTP credentials** (optional) — if `credential_ids` is non-empty, only those credentials may be
   used to dispatch this campaign; empty (default) means any active credential. See Credential Assignment below.

The campaign starts in `PAUSED` status. No emails are sent until you explicitly dispatch.

`POST /api/campaigns/{id}/emails` (adding more leads to an existing campaign) runs the same
`render_draft_emails()` call with an additional `exclude_emails` set — recipients already staged in the
campaign are skipped and counted separately (`already_in_campaign` in the response) rather than treated as
new drafts.

---

## Draft Review

Before dispatching, you can:

| Action                       | API                                                |
|------------------------------|-----------------------------------------------------|
| View all drafts              | `GET /api/campaigns/{id}/emails`                   |
| Edit subject/body            | `PUT /api/campaigns/{id}/emails/{eid}`             |
| Select/deselect one          | `PATCH /api/campaigns/{id}/emails/{eid}/selection` |
| Select/deselect all drafts   | `PATCH /api/campaigns/{id}/emails/selection-all`   |
| Delete a draft                | `DELETE /api/campaigns/{id}/emails/{eid}`          |
| Add more leads                | `POST /api/campaigns/{id}/emails`                  |
| Change SMTP credential pool   | `PUT /api/campaigns/{id}/credentials`              |

Deselected emails (`is_selected = False`) are excluded from dispatch and counted as `skipped` in stats. Deselecting
a QUEUED email (not just DRAFT) pulls it back to DRAFT.

---

## Dispatch

`POST /api/campaigns/{id}/dispatch` starts `run_campaign_dispatch(campaign_id, db)` as an `asyncio.Task`.

```
run_campaign_dispatch(campaign_id, db):
  1. queue_campaign_emails()       DRAFT(is_selected=True) → QUEUED
     (a leftover QUEUED batch from a previously-paused run counts as "something
     to send" even if this call queues zero new drafts)
  2. resolve_credential_pool(assigned or all-active)  → PAUSED (pause_reason set)
     if empty
  3. Loop:
     a. Check campaign status:
        - PAUSED   → break (user kill-switch)
        - CANCELLED → cancel_remaining_queued() → break
     b. get_next_queued_email()    → None means done
     c. resolve_credential_pool() again (re-read fresh every iteration, so a live
        PUT .../credentials edit takes effect immediately) → round-robin selection
     d. _wait_for_credential_slot(cred_id)  — see Credential Rotation below
     e. _send_one_email(cred, recipient, subject, body)
        - Constructs MIMEText (plain/utf-8)
        - aiosmtplib.SMTP with TLS (port 465) or STARTTLS (port 587)
     f. On success  → mark_email_sent(cred_id)
        On hard bounce (550/553, via SMTPResponseException OR SMTPRecipientsRefused)
          → add_to_blacklist() + mark_email_failed(cred_id)
        On rate limit (421/450/451) → set_credential_cooldown(+1 hour) + retry
        On auth failure → disable_credential() + retry (email NOT marked failed)
        On network error (connect/OS/timeout) → set_credential_cooldown(+15 min) + retry
  4. Update campaign status:
     - All emails processed + no remaining drafts → COMPLETED
     - Deselected drafts remain → PAUSED
```

Every email records which credential sent (or last attempted) it, in `credential_id` on `campaign_emails` /
`test_campaign_emails`.

### Credential Rotation

Credentials are selected round-robin from the pool `resolve_credential_pool()` returns (see Credential Assignment
below), re-read fresh every loop iteration. **Send pacing is per-credential, not per-loop-iteration:**
`_wait_for_credential_slot(cred_id)` enforces a 30-90s gap since that credential's last send, tracked in
module-level state **shared across every campaign dispatch task in the process** — so two campaigns sending through
the same credential can't both fire within its jitter window, while different credentials in the same campaign's
rotation can send back-to-back with no wait between them.

### Credential Assignment

A campaign may be restricted to a specific set of SMTP credentials via `credential_ids` on
`POST /api/campaigns`, or changed later with `PUT /api/campaigns/{id}/credentials`
([api-reference.md](api-reference.md#campaigns)). `resolve_credential_pool()`:

- Uses the campaign's assigned credentials if any exist.
- Falls back to **every active credential** if none are explicitly assigned (unchanged legacy behavior).
- Excludes any credential that has already hit its `daily_send_limit` for the current UTC day, regardless of which
  pool it came from.

Because the assignment is re-read every dispatch iteration, editing it on a RUNNING campaign takes effect on the
very next send — no need to pause first.

### Credential States

| State    | Condition                                                  | Effect                                                                |
|----------|-------------------------------------------------------------|-----------------------------------------------------------------------|
| Active   | `is_active=True`, `cooldown_until=NULL or past`             | Available for round-robin                                             |
| Cooling  | `is_active=True`, `cooldown_until` in future                | Skipped until cooldown expires                                        |
| Capped   | `daily_send_limit` set and already reached today            | Excluded from the pool until the next UTC day                         |
| Disabled | `is_active=False`                                            | Never used; requires manual re-enable via `PUT /api/credentials/{id}` |

If every assigned/active credential is disabled, cooling, or capped, the campaign is auto-paused with
`pause_reason` set to a fixed message explaining why (see Stats Endpoint below).

---

## Blacklist

The blacklist prevents emails from being staged in new campaigns and from being sent in existing ones.

**Auto-blacklisting:** On SMTP hard bounce (codes 550 or 553), the recipient email and domain are added to the blacklist
and the email is marked `FAILED`.

**Manual blacklisting:** `POST /api/blacklist` with an email address.

**Effect on campaigns:** `create_campaign` loads the full blacklist set (`get_blacklisted_emails_set()`) and filters
leads before rendering any drafts. Already-staged FAILED emails are not re-sent.

---

## Test Campaigns

Test campaigns are structurally identical to production campaigns but use manually specified dummy recipients instead of
real leads. Use them to:

- Verify your SMTP credentials work.
- Preview template rendering before a real campaign.
- Confirm deliverability with your own email addresses.

**Key differences from production campaigns:**

| Feature              | Production                              | Test                                           |
|----------------------|-----------------------------------------|------------------------------------------------|
| Recipients           | From `leads` table                      | Manually provided `dummy_details`              |
| Credential selection | Round-robin over all active credentials | Optionally pin a specific `test_credential_id` |
| Blacklist check      | Yes                                     | No                                             |
| `lead_id` FK         | Required                                | Null (no real lead)                            |

**Create a test campaign:**

```json
POST /api/test-campaigns
{
  "name": "SMTP Sanity Check",
  "template_id": 1,
  "test_credential_id": 2,
  "dummy_details": [
    {
      "name": "Test User",
      "designation": "Director",
      "email": "your.own.email@example.com",
      "department": "Test Dept"
    }
  ]
}
```

---

## Campaign Status Reference

| Status      | Who sets it       | When                                                           |
|-------------|-------------------|----------------------------------------------------------------|
| `PAUSED`    | `create_campaign` | Initial state after draft generation                           |
| `RUNNING`   | `dispatch`        | When dispatch starts; or manually via PATCH                    |
| `PAUSED`    | Dispatcher        | No usable credentials (`pause_reason` set); or deselected drafts remain after batch |
| `CANCELLED` | User (PATCH)      | All QUEUED emails marked FAILED                                |
| `COMPLETED` | Dispatcher        | All selected emails sent, no remaining drafts                  |

`pause_reason` (nullable, on `campaigns`/`test_campaigns`) is set only for the "no usable credentials" auto-pause —
cleared automatically on any subsequent status change. It's surfaced via `GET /api/campaigns/{id}/stats`.

---

## SMTP Port Configuration

| Port  | Protocol            | `use_tls` | `start_tls` |
|-------|---------------------|-----------|-------------|
| 465   | SMTP over TLS (SSL) | `True`    | `False`     |
| 587   | SMTP with STARTTLS  | `False`   | `True`      |
| Other | Plain SMTP          | `False`   | `False`     |

---

## Stats Endpoint

`GET /api/campaigns/{id}/stats` returns a lightweight object polled every 3 seconds by the UI:

```json
{
  "draft": 8,
  "queued": 2,
  "sent": 45,
  "failed": 3,
  "skipped": 5,
  "total": 63,
  "campaign_status": "RUNNING",
  "pause_reason": null
}
```

`pause_reason` is non-null only when the dispatcher auto-paused the campaign for lack of a usable credential.

- `draft` = selected DRAFT emails (not yet dispatched)
- `skipped` = deselected DRAFT emails
- `queued` = emails moved to QUEUED but not yet sent
- `sent` + `failed` = terminal states
