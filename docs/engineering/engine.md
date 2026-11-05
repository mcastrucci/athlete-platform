# Pull-budget engine

Status: implemented as the first extraction from the existing engine, as planned in section 7 of [pilot-plan.md](pilot-plan.md). The package is `athlete_platform.domain.engine`. It uses only the standard library and dataclasses, with no web, persistence, validation-framework, or provider imports.

## What was extracted

Source: the personal engine at commit `3f5897cf6a2fc16acc5c6794e6d8caed2fbab00f` (`athlete@3f5897c`, which includes the rolling-volume and unknown-dose behavior of its PR 8). Paths below are relative to that repository's `tools/` directory.

| Baseline function (`core.py`) | Engine function | Notes |
| --- | --- | --- |
| `valid_count` | `valid_count` | Finite, non-negative, non-boolean number. |
| `set_count` | `set_count` | Raises `ValueError` for an invalid integer count. |
| `rep_count` | `rep_count` | Intentional difference below. |
| `component_ids` | `component_ids` | |
| `pull_constraints` | `pull_constraints` | Filters on `PullConstraint.active`. |
| `iso_week` | `iso_week`, `iso_week_bounds` | |
| `session_pulls` | `session_pulls` | Intentional difference below. |
| `compute_totals` (pull part) | `weekly_pulls` | Also reports unknown dates. |
| `rolling_pulls` | `rolling_pulls` | Takes sessions explicitly instead of loading logs. |
| `check_constraints` (`weekly_pull_caps` branch) | `evaluate_pull_constraints` | Structured results instead of display rows. |
| `validate_log_quantities` | `quantity_errors` | Boundary validator for callers that want to reject invalid input. |

Not extracted: the other constraint handlers, volume, placement, inventory, export, file loading, YAML, and the plan-integrity checks (`pull_count` agreement, open formats without caps). No personal thresholds, catalog, constraints, dates, or logs were copied; every test uses invented data.

## Typed contract

All inputs are frozen dataclasses in `athlete_platform.domain.engine.model`.

- `Exercise(id, pull_category | None)` and `Catalog = Mapping[str, Exercise]`. An exercise missing from the catalog has no pull category.
- `SetRecord(reps | None)`.
- `Component(exercise, reps, completed_reps, conditional)`. `reps` is per round, `completed_reps` the total done, and `conditional` marks a replacement.
- `Block(exercise, sets, reps, caps, components, rounds)`. `sets` is an integer (prescription), a tuple of `SetRecord` (log), or `None`. `caps` maps component exercise ids to reserved reps in a planned open format. `rounds` is the complete rounds of a logged result. The exercise id `metcon` (`METCON_ID`) never contributes through `sets` and `reps`.
- `Session(date, blocks, performed)`. `date` is a `datetime.date`; a `datetime` raises `TypeError`. `performed=False` selects plan semantics, `True` log semantics.
- `PullConstraint(id, weekly_caps, active=True)`. Caps must be finite and non-negative, else `ValueError`.
- `PullCheckResult(constraint_id, category, kind, cap, value, window_start, window_end, status, unknown_dates, detail)` with `kind` in `weekly | rolling` and `status` in `ok | fail | manual`.

`select_sessions` (the per-date selection above) is public so callers can see which sessions an evaluation counted, and `ENGINE_VERSION` names the rules. `catalog.py` holds the generic catalog described in [evaluation.md](evaluation.md#catalog).

Quantities are typed `float | None` but validated at run time with `valid_count`, because callers may pass values parsed from untrusted documents.

## Semantics

- `None` means not reported. A missing or invalid dose is unknown and never zero. Only `completed_reps=0` is a confirmed zero.
- Invalid quantities (negative, boolean, NaN, infinite) are reported as unknown instead of raising. They never reduce a total. `quantity_errors(session)` lists them so a caller can reject them at the boundary.
- Plan: `sets x reps` of pull blocks plus the reserved `caps` of open formats. Conditional components are not planned exposure. A non-conditional component with a pull category that is not listed in `caps` is an unbounded dose and makes its category unknown.
- Log: performed set lists, and for metcon components `completed_reps` if reported, else complete `rounds x reps per round`. A conditional component without `completed_reps` was not done and is not unknown. Reserved `caps` are a plan-only reservation.
- Each date counts once. Inside the week, a date with a performed session uses the log and ignores that date's planned sessions (the log replaces the plan, even when it differs); a date with only plan uses the plan. Performed sessions before the week count as logged; planned sessions before the week never happened and are ignored. Weekly totals and rolling windows share this selection.
- Rolling window: any 7 days ending on a day from `week_start` to `week_end`. Performed sessions up to six days before the week and the per-date selection above inside the week contribute. The result is the maximum known total, its end date (earliest on ties), and the days from six days before the week to its end with unknown dose.
- Evaluation returns one `weekly` result per (constraint id, category), then one `rolling` result per (constraint id, category) when days before the week add to the worst window or any dose is unknown. Constraints sharing a category get separate results with their own id and cap.
- Status: `fail` when the known total exceeds the cap (unknown doses can only add to it); otherwise `manual` when any dose is unknown, because an unknown dose cannot certify a cap; otherwise `ok`. The cap itself is allowed.
- Nothing reads the wall clock. Week bounds are arguments; `week_end` before `week_start` raises `ValueError`.

## Intentional differences from the baseline

1. `rep_count` returns `None` for a logged set list containing a set without reps (the baseline raised `KeyError`; a later personal fix returns unknown). It also returns `None` for invalid reps, which the baseline summed as they were.
2. A planned pull block without a complete structured dose, with an invalid reserved cap, or a planned non-conditional pull component without a reserved cap (an open format such as an AMRAP) makes its category unknown. The baseline added zero and relied on separate plan checks ("no structured dose", "open format without cap") to fail the plan. The plan-integrity checks were not extracted, so the engine does not silently treat the gap as zero.
3. The weekly result can be `manual` for an unknown dose inside the week. The baseline's weekly row compared only the known total.
4. `completed_reps=None` means not reported. The baseline treated an explicit null the same as an invalid value (unknown) unless the key was absent.
5. Inside the evaluated week, a date with a performed session uses the log instead of the plan, so a plan and its log are never added together. The baseline had only logs before the week and the plan inside it.
6. Display strings are not ported. Results are structured; detail messages are English and dates are ISO 8601.
7. The manual-review override of the baseline (a reviewed note turning a manual row into ok) is not part of this slice. Persisted reviews belong to the application layer.

## Parity table

Synthetic tests live in `tests/unit/engine/`. Baseline test files are in `tests/` of the baseline repository.

| Synthetic test | Baseline test or function |
| --- | --- |
| `test_rolling_evaluation::test_rolling_window_catches_a_cross_week_spike_the_iso_week_misses` | `test_plan.py::test_tope_de_traccion_en_ventana_movil` |
| `test_rolling_evaluation::test_unknown_dose_before_the_week_is_manual_never_ok` | `test_plan.py::test_ventana_movil_dosis_desconocida_queda_manual` |
| `test_rolling_evaluation::test_confirmed_zero_is_not_unknown` | `test_plan.py::test_ventana_movil_cero_confirmado_no_es_desconocido` |
| `test_rolling_evaluation::test_negative_quantity_does_not_subtract_work` | `test_plan.py::test_cantidad_negativa_no_resta_y_se_rechaza` |
| `test_rolling_evaluation::test_constraints_sharing_a_category_get_separate_results` | `test_plan.py::test_ventana_movil_por_restriccion_con_revision_parcial` |
| `test_rolling_evaluation::test_same_day_plan_and_log_count_once`, `test_log_only_and_plan_only_dates_*`, `test_performed_session_with_unknown_dose_*` | none (intentional difference 5) |
| `test_session_pulls::test_planned_uncapped_*`, `test_rolling_evaluation::test_planned_uncapped_open_format_makes_the_week_manual` | none (intentional difference 2) |
| `test_rolling_evaluation::test_no_prior_records_adds_no_rolling_row` | `test_plan.py::test_ventana_movil_sin_registros_previos_no_agrega_filas` |
| `test_rolling_evaluation::test_planned_open_format_caps_count_against_the_weekly_cap` | `test_plan.py::test_tope_de_toes_to_bar_superado` |
| `test_dose_helpers::test_rep_count_sums_a_set_list_and_set_count_counts_records` | `test_regressions.py::test_actual_sets_list_counts_reps_and_sets` |
| `test_dose_helpers::test_rep_count_is_unknown_when_the_dose_is_missing` | `test_regressions.py::test_missing_reps_is_not_zero_volume` |
| `test_dose_helpers::test_rep_count_is_none_when_a_logged_set_has_no_reps` | none (intentional difference 1) |
| `test_session_pulls::test_planned_missing_dose_is_unknown_not_zero` | none (intentional difference 2) |
| `test_session_pulls::test_planned_*`, `test_logged_*`, `test_exercises_missing_*` | `core.py::session_pulls` branches |
| `test_dose_helpers::test_valid_count_*`, `test_quantity_errors_*` | `core.py::valid_count`, `validate_log_quantities` |
| `test_rolling_evaluation::test_rolling_pulls_*`, `test_window_boundary_*` | `core.py::rolling_pulls` |
| `test_rolling_evaluation::test_weekly_total_over_the_cap_fails` | `core.py::check_constraints` |

Each test docstring names its source. `tests/unit/test_engine_corpus_guard.py` scans the engine package and its tests for personal-workspace references.

## Rule changes in the personal repository after the baseline

Cross-checked on 2026-11-04 against the personal repository's main branch (merge `39863c5`). The pin stays at `3f5897c` for parity. These later rule changes are generic, not personal thresholds, and must be carried when the related domain is extracted:

| Personal repository change | Status here |
| --- | --- |
| `rep_count` treats a logged set with unknown reps as an unknown dose instead of failing (PR #16 there) | Applied: intentional difference 1 |
| Decision 0022: the weekly "mixed pieces" target counts metcon-format pieces (intervals, AMRAP, for time), including single-movement benchmarks such as Grace; no work is added to satisfy the label | Pending: apply when weekly targets are extracted |
| Decision 0022: a missed block test can be rescheduled to a later week of the same block, and the block's expected-test list must be updated with it | Pending: apply when block tests are modeled |
| Decision 0023: the body model has a hip-flexor group (primary in burpees, toes-to-bar, sit-ups, GHD; secondary in running, cycling, box jumps, step-ups, lunges, split squats, pistols), burpees also load the lower back, and a figure region whose group the athlete's map lacks renders neutral | Pending: apply when the body model is extracted |

The personal repository's coach-plan, catalog, and inventory changes since the baseline are athlete data and are not carried.

## Not yet done

The engine is connected to confirmed sessions and the profile in [evaluation.md](evaluation.md): a generic versioned catalog, a canonical-session adapter, and stored, manifest-linked evaluations with staleness. Still open:

- Consumer migration of the personal repository to a pinned release, a versioned engine artifact, and the compatibility adapter for the old read contract, as required by section 7 of the pilot plan.
- The other constraint handlers, volume and placement, and persisted manual-review overrides (intentional difference 7).
- Decisions 0022 and 0023 (see the table above), which stay pending.
