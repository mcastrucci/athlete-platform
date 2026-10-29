# Authentication and initial extraction model

- Status: **proposed; awaiting the product owner's decision.** Nothing in this document is agreed until section 4 records a dated decision.
- Date: 2026-10-29.
- Scope: the minimum choices for the first vertical slice in [issue #3](https://github.com/mcastrucci/athlete-platform/issues/3). Frontend, reviewer availability, broader import formats, and external onboarding remain later gates.
- Constraints: Cloud Run + Neon with portability to Cloud SQL (owner decision, 2026-10-27); the existing `IdentityVerifier` contract (RS256 JWT, exact issuer and audience, HTTPS JWKS, users keyed by issuer and subject); the extraction rules in the [technical plan, section 4](pilot-plan.md#4-import-pipeline-and-model-responsibilities).
- This task creates no subscriptions, credentials, paid resources, or deployments.

## 1. Authentication provider

### Options

| | A. Firebase Authentication (Google Identity Platform) | B. Auth0 | C. Self-hosted OIDC (Keycloak, Zitadel) |
| --- | --- | --- | --- |
| Fit with the current verifier | RS256 ID tokens; issuer `https://securetoken.google.com/<project-id>`, audience `<project-id>`, public JWKS. Works with the adapter's existing configuration | RS256 access tokens issued for a configured API audience; the cleanest match to an access-token contract | Configurable; RS256 and JWKS are standard |
| Operations | Managed; same Google Cloud project and billing as Cloud Run | Managed; separate vendor and account | We run, patch, back up, and secure it |
| Sign-in methods for the pilot | Google sign-in and passwordless email link available | Social and passwordless available | Available, but we build the flows |
| Cost at pilot scale (5 athletes) | Expected within the free allowance; confirm current Identity Platform pricing before enabling | The free plan lists 25,000 monthly active users; paid tiers start at a monthly fee | Hosting and maintenance time |
| Portability | Users export with the Firebase CLI. The issuer changes on migration, so (issuer, subject) needs an explicit mapping | Export is available. Same issuer caveat | Fully portable |
| Main risk | ID tokens rather than API access tokens: no per-API audience, scopes, or revocation inside the token | Another vendor's terms and data processing to review under D7 | Operational load disproportionate to a five-athlete pilot |

### Proposal

**A. Firebase Authentication**, with Google sign-in and passwordless email link. No passwords are stored by this product.

- Configure `ATHLETE_OIDC_ISSUER=https://securetoken.google.com/<project-id>`, `ATHLETE_OIDC_AUDIENCE=<project-id>`, and the SecureToken JWKS URL. Confirm the exact JWKS URL, key rotation, and token lifetime against Google's documentation when the project is created.
- Short token lifetime is the revocation boundary. A disabled user can keep using an already-issued token until it expires. If the pilot needs immediate revocation, add a revocation check in the adapter. Do not add provider calls to the domain.
- Keep the adapter provider-neutral. Firebase-specific claims (`firebase.sign_in_provider`, `email_verified`) may be read in infrastructure only. Authorization never relies on email.
- Local and test identities remain injected only under `environment=test`, as today.

Choose B instead if per-API access tokens or scopes become a requirement before onboarding.

## 2. Initial extraction model

### Task

Turn one pasted training text into a schema-validated interpretation: sessions, exercises, prescribed or performed sets, reps, loads, units, dates, and explicit ambiguities. The model extracts and proposes. Deterministic code validates, and only an athlete confirmation creates canonical data.

### Provider route

| | Claude through Vertex AI (Google managed) | Claude through the Anthropic API |
| --- | --- | --- |
| Credentials | Cloud Run service account and IAM; no long-lived API key | API key held in Secret Manager |
| Billing | Same Google Cloud billing account | Separate account |
| Features this slice needs (Messages API, structured outputs, prompt caching, token counting) | Available | Available |
| Not available on this route | Message Batches, server-side refusal fallbacks, Models API | — |
| Data processing | Google Cloud terms | Anthropic terms |

**Proposal:** Vertex AI, for IAM-based credentials and a single cloud bill. The adapter must not depend on Vertex specifics, so switching to the Anthropic API stays a configuration change.

### Model

Prices are first-party list prices per million input/output tokens at the time of writing. Vertex pricing is separate and must be confirmed.

| Model | ID | Input / output (USD per 1M tokens) |
| --- | --- | --- |
| Claude Opus 5.5 | `claude-opus-5-5` | 4.00 / 20.00 |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | 2.00 / 10.00 |
| Claude Haiku 5.5 | `claude-haiku-5-5` | 0.10 / 0.50 |

**Proposal:** start the controlled synthetic corpus on **Claude Opus 5.5 at `low` effort**, and run the same corpus on Sonnet 5.5 and Haiku 5.5. Select the least expensive model that meets the corpus acceptance bar below. Record the selected model, effort, and prompt template version in every extraction run, as the technical plan requires.

Rough cost per extraction, assuming about 3,000 cacheable system and schema tokens, 1,500 tokens of training text, and 2,500 output and reasoning tokens:

- Opus 5.5: about USD 0.07.
- Haiku 5.5: well under USD 0.01.

At an assumed pilot volume of five athletes × four weeks × about six texts per week, including retries, total model spend stays in the tens of dollars or less. These are planning estimates, not measurements. Replace them with measured usage from the corpus runs.

### Corpus acceptance bar

The corpus is synthetic only and includes the cases already required by #6: strength work, a metcon, missing values, ambiguous dates, repeated sessions on different days, and an instruction-injection sample. Proposed thresholds, for the owner to accept or change:

- Every response validates against the schema; schema failures are retried at most once and then surface as a visible failure.
- Unknown dose, date, or exercise is reported as unknown or ambiguous, never as zero or a guess: no invented values in the corpus.
- The injection sample cannot mark work as completed, change the schema, or skip athlete confirmation.
- Field-level accuracy on known values is reported per model; the owner sets the minimum after the first run.

### Request contract

- Structured outputs (`output_config.format`) with the interpretation JSON schema; no tools of any kind. The response is untrusted data and is validated again with Pydantic.
- The training text is passed as data in the user turn, delimited from instructions. Only the minimum athlete context needed (time zone, catalog vocabulary) is included.
- No hidden reasoning is requested or stored. Only validated output, usage, model identifier, and versions are persisted.
- Prompt caching for the stable system prompt and schema.

## 3. Initial limits and budget

Proposed defaults, all configurable and enforced in the application before any provider call:

| Limit | Proposed value | Notes |
| --- | --- | --- |
| Input text | 20,000 characters | Rejected before enqueue with a visible error |
| Input tokens per request | 16,000 | Checked with token counting before the call |
| Output tokens per request (`max_tokens`) | 8,000 | Truncation is a failure, not a partial result |
| Attempts per job | 3 | Matches the queue's current `max_attempts` default |
| Extraction attempts per athlete per UTC day | 20 | Lower than the queue's current default of 50 |
| Global monthly model budget | USD 25 | The application budget gate blocks new calls when exhausted; warnings at 50% and 80% |
| Concurrent provider calls | 2 | Bounded per worker execution |

Cloud billing alerts are notifications only. The application gate is the hard stop, and an uncertain timeout keeps its spend reservation until reconciled.

Real-model testing uses synthetic data only, under a separate development budget of USD 10. Nothing personal from the athlete repository enters a prompt.

## 4. Decision record

| Item | Proposal | Owner decision | Date |
| --- | --- | --- | --- |
| Authentication provider | A. Firebase Authentication, Google sign-in and email link | Pending | — |
| Model route | Vertex AI (Google managed) | Pending | — |
| Initial model | Opus 5.5 at `low` effort, compared with Sonnet 5.5 and Haiku 5.5 on the corpus | Pending | — |
| Limits and budgets | Section 3 | Pending | — |
| Provider retention and data processing (part of D7) | Review Google Cloud terms for Identity Platform and Vertex AI before onboarding | Pending | — |

## 5. Still open after this decision

- Frontend technology and the assisted-operation interface (technical plan, section 6).
- Reviewer and operations availability (PRD D3).
- Import formats beyond pasted text (PRD D6).
- Retention, deletion, and provider processing for external participants (PRD D7).
- Row-level security as defense in depth (technical plan, section 2).
