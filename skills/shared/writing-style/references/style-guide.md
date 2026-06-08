# House style guide

This expands the principles in `SKILL.md` with rules and worked examples.

## Voice and tone

- Write to the reader as "you". Refer to the team or product as "we" only when
  it adds meaning.
- Be direct and neutral. Avoid hype ("blazingly fast", "world-class") and
  hedging ("we think maybe").
- Assume a competent reader who is new to this specific thing.

## Sentences and paragraphs

- One idea per sentence. One topic per paragraph.
- Front-load the subject and verb; push qualifiers to the end.
- Keep paragraphs to 2–4 sentences in docs.

### Long sentence, rewritten

> Before: In the event that the upload fails for some reason, the system will,
> after a short delay, attempt to retry the operation a number of times before
> ultimately giving up and surfacing an error to the user.

> After: If an upload fails, the system retries three times with backoff. After
> the last attempt it surfaces an error to the user.

## Words to avoid

| Avoid | Prefer |
| --- | --- |
| utilize | use |
| in order to | to |
| at this point in time | now |
| a number of / various / several | the actual count or list |
| leverage (as a verb) | use |
| facilitate | help, let, do |
| appropriate / relevant (unqualified) | name the specific thing |

## Passive voice

Passive voice hides who does what. Prefer active voice unless the actor is
unknown or irrelevant.

- Passive: "The config is loaded at startup."
- Active: "The server loads the config at startup."

## Commit messages

- Subject line: imperative mood, ≤50 chars, capitalized, no trailing period.
  - Good: `Add retry backoff to upload worker`
  - Bad: `added retries`, `Fixes the thing.`
- Blank line, then a body wrapped at ~72 chars explaining *why*, not *what*.
- Reference issues at the end (e.g. `Refs #123`).

## Release notes and changelogs

- Group entries by audience impact: Features, Fixes, Performance, Breaking
  changes.
- Write each entry as a user-facing outcome, not an implementation detail.
  - Good: "Uploads now resume automatically after a network drop."
  - Bad: "Refactored UploadManager to use exponential backoff."
