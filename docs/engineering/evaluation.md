# Pull-budget evaluation of confirmed sessions

Date: 2026-11-05. Slice 2 of [issue #7](https://github.com/mcastrucci/athlete-platform/issues/7). It connects the [pull-budget engine](engine.md) to the athlete's [confirmed sessions](sources.md#confirmation) and profile. Only confirmed sessions are evaluated; interpretations, corrections and unconfirmed sources never affect a result. Tests use invented athletes, dates and exercises.

Decisions 0022 and 0023 of the personal repository (weekly mixed-piece targets, block tests, the hip-flexor body model) stay pending and are not implemented here.

## Inputs

| Input | Where it comes from |
| --- | --- |
| Pull budgets | `pull_budgets` in the latest profile revision (see below) |
| Sessions | The athlete's confirmed canonical sessions dated from six days before the week's Monday through its Sunday |
| Catalog | `domain/engine/catalog.py`, version `catalog-v1` |
| Rules | The engine and the adapter, version `pull-budget-v1` |

Free-text profile `constraints` are never parsed. Only the structured `pull_budgets` are evaluated.

### Pull budgets in the profile

`ProfileContent.pull_budgets` is an optional list of at most 50 budgets: `{id, weekly_caps, active}`. `id` is 1 to 64 characters and unique within the profile. `weekly_caps` maps 1 to 20 category names to a finite cap of 0 or more. `active` defaults to true. Unknown fields are rejected. The field is `null` when absent, so profile revisions stored before it existed still load unchanged, and a profile `PUT` replaces the whole content as before (omit it to clear the budgets).

Category names are not checked against the catalog when the profile is saved, so a profile never becomes unreadable when a catalog changes. A cap on a category no catalog exercise counts toward cannot be certified: it reports status `manual` and an `uncatalogued_budget_category` assumption.

## Catalog

`catalog-v1` is generic movement knowledge, not athlete data. Ids are lowercase snake_case. The adapter first normalizes an item's `exercise_id` (trim, lowercase, hyphens and spaces to `_`), so `Pull-Up` finds `pull_up`; nothing else is guessed.

| Pull category | Exercise ids |
| --- | --- |
| `strict_pull` | `pull_up`, `chin_up`, `chest_to_bar_pull_up`, `weighted_pull_up` |
| `rope` | `rope_climb`, `legless_rope_climb` |
| `muscle_up` | `bar_muscle_up`, `ring_muscle_up` |
| `kipping_core` | `toes_to_bar` |

Common movements outside any pull budget are listed with no category (for example `back_squat`, `bench_press`, `strict_press`, `clean_and_jerk`, `burpee`, `run`, `bike`, `row`, `ring_row`). `ring_row` is a horizontal pull and deliberately does not count toward `strict_pull`; a later catalog version can change that. `metcon` is not a movement and is not listed. Changing any mapping, including adding an id, bumps `CATALOG_VERSION`, which makes earlier evaluations stale.

## Adapter rules

The adapter (`application/pull_evaluation.py`) is pure: canonical sessions in, engine sessions out, no database.

- `performed` becomes a log (`Session(performed=True)`), `planned` a plan. The engine picks one record per date, so a log replaces a plan on the same date; a planned session before the week is ignored.
- **Exercise.** The exercise is the item's `exercise_id`. An item with no id, or with an id the catalog does not list, is *unclassified*. It is reported as an assumption (`unclassified_item` or `unknown_exercise_id`, with the session id and item position). Because it could be a pull, its date becomes unknown for every pull category, so each regulated category on that date is `manual`, never `ok`, and the item is never silently counted as zero pulls. The item's `kind` is not used to rule out pulls, because it is model output. A replaced plan (a planned session on a date that has a log) does not count, so its items are not reported. A confirmed id with no pull category (`back_squat`) never matters, with or without a dose.
- **Dose.** A missing value stays unknown; only an explicit `0` is a zero.

| Item | Performed session | Planned session |
| --- | --- | --- |
| `sets` and `reps` known | one set record of `reps` per set | `sets` and `reps` |
| `sets` known, `reps` missing | unknown | unknown |
| `sets` missing, `reps` known | unknown (not guessed as one set) | unknown |
| `rounds` and `reps` known | `completed_reps = rounds x reps` of one component | `rounds x reps` reserved for the component |
| `rounds` known, `reps` missing | unknown | unknown |
| `sets` and `rounds` both present | unknown, with an `ambiguous_dose` assumption | same |
| any of `sets`, `reps`, `rounds` is `0` | confirmed zero | confirmed zero |

- `load`, `duration` and `distance` are never read. Their units are never summed or converted. A pull exercise with only a duration is therefore unknown.
- Findings state their assumptions through the evaluation's `assumptions`, so a reader can see why a result is manual.

Changing the adapter or the engine bumps `ENGINE_VERSION` (`pull-budget-v1`) so that earlier evaluations are stale.

## Manifest and hash

Every evaluation stores the manifest it was computed from:

```json
{
  "week": "2030-W10",
  "profile_version": 2,
  "catalog_version": "catalog-v1",
  "engine_version": "pull-budget-v1",
  "confirmations": [{"source_id": "…", "interpretation_version": 1}]
}
```

`confirmations` holds each confirmed `(source_id, interpretation_version)` that has at least one session in the span, sorted by source id and version. Canonical sessions are write-once per confirmation, so this pair identifies exactly which sessions were evaluated. `profile_version` is the latest profile revision at evaluation time (any profile edit, not only to the budgets, changes it; that is conservative). A confirmation whose sessions all fall outside the span is not in the manifest.

`manifest_hash` is the SHA-256 hex digest of the canonical JSON of the manifest: sorted keys, no whitespace, ASCII only, and the confirmation set sorted and deduplicated, so the hash ignores input order and changes with any input.

## Idempotency and races

`POST /evaluations` builds the manifest, hashes it, and looks up `(athlete, week, manifest_hash)`.

| Situation | Result |
| --- | --- |
| An evaluation with this hash exists | 200 with the stored evaluation |
| None exists | the engine runs, the evaluation is stored, 201 |

The unique constraint on `(athlete_id, week, manifest_hash)` decides races. The result is inserted with `INSERT … ON CONFLICT DO NOTHING`. A concurrent identical request waits for the first to commit, inserts nothing, reads the winner's row and returns it with 200. Exactly one row exists. No athlete or source row is locked, so evaluation never blocks profile updates, submissions or confirmations.

The session rows and the confirmation versions come from one SQL statement, so the manifest always describes exactly the rows that were evaluated. A confirmation that commits during an evaluation lands in the next manifest. The profile is read as the revision of the athlete's current version, which never changes once written.

Results are never updated. A new input produces a new row; earlier rows stay readable.

## Staleness

`stale` is computed on read, not stored. A reader rebuilds the current manifest for the stored week (current profile version, current confirmations in the span, the running catalog and engine versions), hashes it, and compares it with the stored hash. Different means stale. Reasons: a new or changed confirmation with sessions in the span, any profile revision, a new catalog or engine version. A confirmation outside the span, or an unconfirmed interpretation, does not change the hash. Stale evaluations stay fully readable and unchanged; request a new evaluation to refresh.

## Findings and assumptions

Each stored finding has `constraint_id`, `category`, `kind` (`weekly` or `rolling`), `value`, `cap`, `status` (`ok`, `fail`, `manual`), `window_start`, `window_end`, `unknown_dates`, `detail`, `contributions` and the `catalog_version` and `engine_version` used. The semantics of value, window and status are those of the engine: the weekly total over the ISO week, the worst rolling seven-day window ending inside the week, `fail` when the known total exceeds the cap, `manual` when any dose is unknown.

`contributions` lists `{session_id, item_position, session_date, pulls}` for the items that add known pulls inside the finding's window, plus items whose dose is unknown on one of its `unknown_dates` (`pulls: null`). Items contributing zero are omitted. Unclassified items appear as unknown for every category.

`assumptions` holds `{code, detail, session_id, item_position}`:

| Code | Meaning |
| --- | --- |
| `no_budgets_configured` | The profile has no `pull_budgets`; there are no findings |
| `no_active_budgets` | Every budget is inactive; there are no findings |
| `uncatalogued_budget_category` | A cap names a category the catalog never counts toward; its findings are `manual` |
| `unclassified_item` | An item without an exercise id |
| `unknown_exercise_id` | An exercise id not in the catalog |
| `ambiguous_dose` | A pull item with both sets and rounds |

An athlete without budgets still gets a stored evaluation, with no findings and the first assumption above.

## Storage and API

Migration `0007_evaluations` adds `evaluations`: `id`, `athlete_id` (foreign key), `week`, `manifest` (JSONB), `manifest_hash`, `evaluated_at`, `findings` (JSONB array), `assumptions` (JSONB array), the unique constraint above, and CHECK constraints on the week and hash formats and on the JSON types.

| Request | Result |
| --- | --- |
| `POST /v1/athletes/{athlete_id}/evaluations` with `{"week": "YYYY-Www"}` | 201 new, 200 existing; 422 for an invalid ISO week (spelling, a week the year lacks, years outside 2000 to 2100) |
| `GET /v1/athletes/{athlete_id}/evaluations/{evaluation_id}` | The stored evaluation with `stale` |
| `GET /v1/athletes/{athlete_id}/evaluations?week=YYYY-Www&limit=` | That week's evaluations, newest first, each with `stale`; `limit` defaults to and is at most 50; `has_more` marks truncation |

All paths require the caller to own the athlete. Another athlete's id, or an evaluation of another athlete, returns 404.

## Not done

- Evaluations are computed in the request, not through the job queue. The input is bounded to a 13-day span, and the work is a pure function.
- A confirmation cannot yet be withdrawn or superseded (see [sources.md](sources.md#next-slices-in-6)); when it can, the manifest already changes with it.
- No manual-review overrides, explanations, proposals or other constraint kinds.
