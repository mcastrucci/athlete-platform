# Pilot PRD: the pursuit of consistency

- Status: draft for review; proposed scope, not an implementation commitment.
- Date: 2026-10-27.
- Product owner: Maximiliano Castrucci.
- Internal name: athlete-platform. Branding does not block development.
- Background: [product vision in athlete, PR #10](https://github.com/mcastrucci/athlete/pull/10). This document is the scope reference for the new product; subsequent changes are reviewed here.

## 1. Purpose

Help athletes sustain a coherent process of individual development. Consistency connects goals, blocks, weeks, sessions, and athlete response.

> Train with a development plan: clear goals, organized volume, and progression reviewed against your results.

The core product unit is the development block: priorities, training dose, progression, assessments, and review criteria. Each session serves an intention within that block.

Our hypothesis is that coordinating the athlete's complete training and explaining adjustments provides value to someone who already receives programming. We do not assume these capabilities are unique or that a gym's programming lacks structure.

## 2. User and problem

An advanced or committed athlete who already trains CrossFit and may combine gym classes, Olympic weightlifting, strength, skills, and accessory work. They have concrete goals, but their programming sources may not account for their complete training.

They need to understand what they are developing, whether the distribution of work supports their priorities, and what to keep, add, reduce, substitute, or reschedule.

Actual experience differs from the plan because of attendance, scaling, loads used, additional sessions, and individual response. The product must assess that experience.

The initial buyer and user is the athlete. Managing coaches, athletes, and gyms is a possible expansion. Max's personal case is an initial reference, not a universal template or the only evidence of usefulness.

## 3. Hypothesis and pilot outcome

Main hypothesis: an athlete with existing programming understands their training better, applies reasoned adjustments, and returns to review progress each week.

The pilot must complete this loop:

1. Profile and goals.
2. Programming upload and confirmation.
3. Assessment against the athlete's context.
4. Proposed adjustments and explicit acceptance.
5. Execution and response logging.
6. Weekly review that informs the following week.

Improvements in athletic benchmarks are exploratory. A small, short pilot does not establish physiological efficacy or superiority over another method.

## 4. Proposed scope

| Area | Pilot | After the pilot |
| --- | --- | --- |
| User | Adult athlete who already trains; Spanish-language experience | Beginners and gym/coach management |
| Journey | CrossFit with existing Olympic weightlifting, strength, conditioning, and accessory work | Hybrid/HYROX with its own assessments and rules |
| Planning | Assess and adjust existing programming within an explicit block | Generate complete programs from scratch |
| Skills | Record level, goal, and existing practice; identify incompatibilities | Library of assessments and adaptive skill progressions |
| Input | Text, XLSX, PDF, and JPG/PNG photos; manual correction | Additional formats and automatic integrations |
| Tracking | Sessions, partial execution, response, and weekly review | Wearables and continuous adaptation |
| Experience | Usable from a phone; interface format to be decided | Native apps and social features |

The initial block may be proposed by the product or supplied by the coach. If the external program's intention is unknown, request it or mark it as unknown.

We propose using the previous two weeks, the following week's plan when available, and a four-week block horizon. Two weeks support describing what was observed; they do not establish the quality of an entire periodization approach.

The gym's next four weeks do not need to be known: preserve the block's intention and adjust as new information arrives, without inventing sessions.

Repository and code language is English. The proposed Spanish-language pilot experience is a separate product decision; localized content must remain separate from domain identifiers and internal documentation.

## 5. Journey and requirements

### P1. Profile, priorities, and constraints

Capture experience, goals ordered by priority, dated baseline metrics, actual availability, equipment, fixed and flexible commitments, and reported constraints.

Record relevant skills, distinguishing isolated execution, consistency, sets, and integration into a metcon; do not require an exhaustive assessment to get started.

The athlete identifies which programming to preserve and what may change. A goal without a benchmark may be provisional and must state which assessment is missing.

**Acceptance:** two athletes with different availability and commitments can use the flow without inheriting each other's data or personal rules; missing fields remain visible.

### P2. Upload, interpretation, and confirmation

Accept pasted text, XLSX, text-based or scanned PDFs, and legible photos of a sheet or whiteboard. Preserve the source and present an editable interpretation before assessment.

Extract dates, sessions, and blocks; exercises/variations, sets, repetitions, load, intensity, rest periods, and metcon format when present. Distinguish units, percentages, and load references.

Do not fill missing fields as facts. Flag ambiguous text, illegible images, unknown exercises, or unsupported formats, and allow manual correction or another upload. Show file size and page limits before upload; their values will be defined in the technical plan.

Do not silently duplicate sessions when a file is uploaded again. Changing confirmed data marks the affected assessment as outdated until recalculated.

**Acceptance:** assessments use an athlete-confirmed version; each relevant datum can be traced to its source or manual correction. Acceptance examples cover every format, including an ambiguous photo and a repeated upload.

### P3. Assessment of complete training

Compare observations with goals, availability, constraints, and block context. Include:

- Distribution of work across relevant capacities and movement patterns.
- Dose per exercise and repeated exposure across sessions, including additional work.
- Proximity of potentially incompatible stimuli and fixed commitments.
- Opportunities for development, maintenance, and assessment.
- Missing data and interpretation limits.

Separate planned and completed volume. Preserve original units: repetitions, kilograms, time, distance, and calories are not interchangeable. If an equivalence is used, identify the model, assumptions, and limits; do not present it as a direct measure of recovery.

Avoid an arbitrary overall good/bad score. Findings must be specific, prioritized, and verifiable. Do not attribute a periodization intention that is absent from the source and has not been confirmed.

**Acceptance:** the report distinguishes data, estimates, and hypotheses; unknown data do not become zero or evidence that no problem exists.

### P4. Adjustments and exercise explanations

Propose a modified version that can be compared with the original plan. Each change includes dose, placement, goal, and its effect on time and total training load. Respect commitments declared immutable; if no compatible proposal exists, explain the conflict and request a decision.

A brief explanation must be available for each prescribed or assessed exercise, with additional detail:

- Contribution to the goal: development, maintenance, transfer, or technical practice.
- Dose and timing: intensity, frequency, progression, and placement.
- Cost and compatibility with the rest of the training.
- Constraints, missing information, and alternatives.
- Which result would justify progressing, maintaining, reducing, or replacing it.

Explanations must match the data, rules, and calculations actually applied. Distinguish available evidence, coaching judgment, and hypotheses; do not fabricate references or precision.

The athlete accepts, modifies, or rejects the proposal. Preserve the original, alternatives, and accepted version. No adjustment silently changes the active plan.

**Acceptance:** every modification is traceable and can be rejected; explicit constraints are met or block the proposal. Explanations change when context or dose changes.

### P5. Execution and response

Allow logging a complete, partial, modified, or skipped session, with loads/repetitions/results when known. Request a brief check-in covering effort, sleep, energy, discomfort, and notes, without requiring every field.

Distinguish not logged, not performed, and performed with unknown dose. Show the date and age of data used for decisions; do not turn yesterday's response into today's state.

**Acceptance:** partial sessions are not counted as complete; zero and unknown produce different results; corrections update affected reports without erasing traceability.

### P6. Weekly review and block continuity

Compare intention, execution, and response; show what changed, what remains pending, and what deserves review. Propose the following week using newly available programming.

Preserve priorities, progression criteria, and block assessment across weeks. Do not rebuild the block because of isolated variations without justification or automatically add work to compensate for everything missed.

**Acceptance:** the review explains which decision is maintained or changed and which observation supports it. The athlete confirms the next version before activation.

## 6. Recommendation limits and trust

The product informs and supports training planning; it does not diagnose injuries or prescribe rehabilitation. It respects reported constraints and may request human review when data are missing or a proposal is incompatible.

Personal thresholds from the athlete repository do not become universal limits. Training rules and their explanations require review by someone with relevant professional experience before use with external participants.

Estimated loads do not certify that someone has recovered. A recommendation must be able to state that information is insufficient.

Athletes retain control over their data: individual access, export, and deletion are available from the pilot, even if initially assisted. Personal files, photos, and logs are not stored as fixtures or published on GitHub. They are not used for model training without separate consent.

## 7. Pilot evaluation

Proposal to agree on: five athletes, including Max and at least three people with a different training mix, for four weeks after onboarding. Assisted follow-up and expert review of proposals; do not promise full autonomy.

Record numerators, denominators, and dropout reasons. Separate manual assistance from product functionality to avoid attributing human capabilities to the product.

| Signal | Definition | Proposed threshold |
| --- | --- | --- |
| Activation | Profile + confirmed programming + first assessment viewed | At least 4 of 5 |
| Applied value | Applies at least one adjustment and explains its purpose | At least 3 of 5 |
| Continuity | Completes at least 3 of 4 weekly reviews | At least 3 of 5 |
| Understanding | Can explain the reason for two plan decisions in a brief interview | At least 4 of 5 |
| Quality | Expert reviews every proposed plan and a sample of explanations | No unresolved critical constraint conflicts when delivering a plan |

Also measure onboarding time, corrections by format, assistance minutes per athlete/week, rejected recommendations, and reasons. These indicate operating cost and friction; session logging counts are not substitutes.

Meeting thresholds supports another iteration, not proof of product-market fit. If usefulness fails, revisit the proposition and scope; if interpretation or prescription quality fails, fix it before expanding the cohort.

## 8. Out of scope

- Programming from scratch for people who do not train.
- A universal optimizer that guarantees the best plan or results.
- Gym business management, coach permissions, and group programming.
- A complete Hybrid/HYROX journey and automatic skill progressions.
- Diagnosis, rehabilitation, nutrition, and supplement recommendations.
- Wearables, app synchronization, video analysis, and social features.
- Payments, commercial plans, and native apps.

Reserving space for these in a future model does not mean they are implemented.

## 9. Decisions before the technical plan

| ID | Decision | Initial proposal |
| --- | --- | --- |
| D1 | Cohort, duration, and recruitment | Five athletes, four weeks; validate access to participants |
| D2 | Prescription scope | Adjust existing plans; complete generation and new skill progressions later |
| D3 | Operations and training review | Assisted pilot; identify reviewer and availability before onboarding third parties |
| D4 | Interface and acceptable friction | Mobile use; compare a dedicated web experience with an assisted experience during planning |
| D5 | Metrics and thresholds | Use section 7 as hypotheses; agree before measuring |
| D6 | Import capabilities | Four input types in P2; define limits and a representative corpus |
| D7 | Data and providers | Agree on storage, access, retention, deletion, and provider processing |

The commercial name, monetization model, and gym management do not block the pilot.

## 10. Repository tracking

This PRD holds scope and acceptance criteria. The future `docs/engineering/pilot-plan.md` will contain stages with checkboxes, validation, and PR links; architecture and stack are not defined yet.

- [x] Record the first PRD draft.
- [ ] Review scope and resolve D1–D7 with dated decisions.
- [ ] Prepare the technical plan and decide what to reuse from the existing engine.
- [ ] Verify P1–P6 and expert review before onboarding third parties.
- [ ] Run the pilot and record results against the hypotheses.

Scope changes go through PRs. Each technical stage must link to a requirement in this document and an observable check. Personal data from athlete are not transferred into the product; any engine reuse is defined in the technical plan.
