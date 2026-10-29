# Authentication and initial extraction model

- Status: **partly decided.** Section 4 records the product owner's dated decisions; any row still marked Pending remains a proposal.
- Date: 2026-10-29.
- Scope: the minimum choices for the first vertical slice in [issue #3](https://github.com/mcastrucci/athlete-platform/issues/3). Frontend, reviewer availability, broader import formats, and external onboarding remain later gates.
- Constraints: Cloud Run + Neon with portability to Cloud SQL (owner decision, 2026-10-27); the existing `IdentityVerifier` contract (RS256 JWT, exact issuer and audience, HTTPS JWKS, users keyed by issuer and subject); the extraction rules in the [technical plan, section 4](pilot-plan.md#4-import-pipeline-and-model-responsibilities).
- This task creates no subscriptions, credentials, paid resources, or deployments.

## 1. Authentication provider

### Options

| | A. Firebase Authentication (Google Identity Platform) | B. Auth0 | C. Self-hosted OIDC (Keycloak, Zitadel) |
| --- | --- | --- | --- |
| Fit with the current verifier | RS256 ID tokens with a `kid`; issuer `https://securetoken.google.com/<project-id>` and audience `<project-id>` match the adapter. Google's verification guide publishes the keys as an x509 certificate map, not the JWKS document the adapter fetches, and requires `auth_time` in the past, which the adapter does not check. Needs adapter changes (see the decision below) | RS256 access tokens issued for a configured API audience; the cleanest match to an access-token contract | Configurable; RS256 and JWKS are standard |
| Operations | Managed; same Google Cloud project and billing as Cloud Run | Managed; separate vendor and account | We run, patch, back up, and secure it |
| Sign-in methods for the pilot | Google sign-in and passwordless email link available | Social and passwordless available | Available, but we build the flows |
| Cost at pilot scale (5 athletes) | Expected within the free allowance; confirm current Identity Platform pricing before enabling | The free plan lists 25,000 monthly active users; paid tiers start at a monthly fee | Hosting and maintenance time |
| Portability | Users export with the Firebase CLI. The issuer changes on migration, so (issuer, subject) needs an explicit mapping | Export is available. Same issuer caveat | Fully portable |
| Main risk | ID tokens rather than API access tokens: no per-API audience, scopes, or revocation inside the token | Another vendor's terms and data processing to review under D7 | Operational load disproportionate to a five-athlete pilot |

### Decision (2026-10-29)

**A. Firebase Authentication**, with Google sign-in and passwordless email link. No passwords are stored by this product.

- Configure `ATHLETE_OIDC_ISSUER=https://securetoken.google.com/<project-id>` and `ATHLETE_OIDC_AUDIENCE=<project-id>`.
- Before the adapter accepts Firebase tokens, it needs two changes, each with synthetic tests:
  - Key material: fetch the x509 certificate map that Google's ID-token guide documents (`https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com`), select the certificate by `kid`, and honor its cache headers. Alternatively, use a JWKS endpoint only after confirming Google documents and supports it. The key source stays configuration, never a token header.
  - Claims: require `auth_time` and reject tokens whose `auth_time` is in the future, in addition to the current `exp`, `iat`, `iss`, `aud`, and `sub` checks. Confirm the remaining claim rules and token lifetime against Google's guide when the project is created.
- Short token lifetime is the revocation boundary. A disabled user can keep using an already-issued token until it expires. If the pilot needs immediate revocation, add a revocation check in the adapter. Do not add provider calls to the domain.
- Keep the adapter provider-neutral. Firebase-specific claims (`firebase.sign_in_provider`, `email_verified`) may be read in infrastructure only. Authorization never relies on email.
- Local and test identities remain injected only under `environment=test`, as today.

Choose B instead if per-API access tokens or scopes become a requirement before onboarding.

## 2. Initial extraction model

### Task

Turn one pasted training text into a schema-validated interpretation: sessions, exercises, prescribed or performed sets, reps, loads, units, dates, and explicit ambiguities. The model extracts and proposes. Deterministic code validates, and only an athlete confirmation creates canonical data.

### Provider route

Options considered:

| | Claude through Vertex AI (Google managed) | Provider APIs with server-held API keys |
| --- | --- | --- |
| Credentials | Cloud Run service account and IAM; no long-lived API key | One API key per provider in Secret Manager, exposed only to the worker |
| Providers | Claude models only | Anthropic, OpenAI, or others through separate adapters |
| Billing | Same Google Cloud billing account | One account per provider |
| Data processing | Google Cloud terms | Each provider's terms |

**Decision (2026-10-29):** provider APIs with server-held API keys. The owner wants to choose between providers (for example Anthropic or OpenAI) depending on the objective. Vertex AI does not serve OpenAI models.

- Initial provider: the Anthropic API.
- Keys are stored in Secret Manager and mounted only into the worker job. The API service never holds model credentials, and keys never reach the client, logs, or prompts.
- Each provider has its own adapter behind one extraction contract: the same input, the same output schema, and the same validation. Provider-specific request fields stay inside the adapter.
- Routing is configuration per task, for example `extraction.text → anthropic:claude-haiku-5-5`. Changing a route requires a new corpus run on the target model and is recorded with the model identifier in every extraction run.
- The budget gate, quotas, and usage records are provider-neutral and apply to every route. Each adapter reports usage in a common form for spend reconciliation.
- Adding a second provider requires a review of its data-processing and retention terms (D7) and its own corpus run before any route uses it.

### Model

Anthropic list prices per million input/output tokens at the time of writing. Other providers' prices are recorded when their adapter is added.

| Model | ID | Input / output (USD per 1M tokens) |
| --- | --- | --- |
| Claude Opus 5.5 | `claude-opus-5-5` | 4.00 / 20.00 |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | 2.00 / 10.00 |
| Claude Haiku 5.5 | `claude-haiku-5-5` | 0.10 / 0.50 |

**Decision (2026-10-29):** start with **Claude Haiku 5.5** (`claude-haiku-5-5`) at `low` effort. Run the controlled synthetic corpus on it first. If it misses the acceptance bar below, compare Sonnet 5.5 and Opus 5.5 on the same corpus before changing the route. Record the model, effort, and prompt template version in every extraction run, as the technical plan requires.

Rough cost per extraction, assuming about 3,000 cacheable system and schema tokens, 1,500 tokens of training text, and 2,500 output tokens (thinking disabled on the Haiku route):

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

- The provider's native structured-output mode with the interpretation JSON schema (`output_config.format` on the Anthropic API); no tools of any kind. The response is untrusted data and is validated again with Pydantic.
- The training text is passed as data in the user turn, delimited from instructions. Only the minimum athlete context needed (time zone, catalog vocabulary) is included.
- Thinking: the initial Haiku 5.5 route sends `thinking: {"type": "disabled"}`. Haiku 5.5 thinks by default, including at `low` effort, and it accepts `disabled` at `high` effort or below. Enabling thinking is a route change: it needs a corpus run and a raised output cap.
- Thinking blocks are never persisted. Only validated output, usage, model identifier, and versions are stored.
- Prompt caching for the stable system prompt and schema where the provider supports it.

## 3. Initial limits and budget

Proposed defaults, all configurable and enforced in the application before any provider call:

| Limit | Proposed value | Notes |
| --- | --- | --- |
| Input text | 20,000 characters | Rejected before enqueue with a visible error |
| Input tokens per request | 16,000 | Checked with token counting before the call |
| Output tokens per request (`max_tokens`) | 8,000 | Covers thinking and visible output together. With thinking disabled it is all visible output. A route that enables thinking must raise this cap. Truncation is a failure, not a partial result |
| Attempts per job | 3 | Matches the queue's current `max_attempts` default |
| Extraction attempts per athlete per UTC day | 20 | Lower than the queue's current default of 50 |
| Global monthly model budget | USD 25 (decided 2026-10-29) | Across all providers. The application budget gate blocks new calls when exhausted; warnings at 50% and 80% |
| Concurrent provider calls | 2 | Bounded per worker execution |

Cloud billing alerts are notifications only. The application gate is the hard stop, and an uncertain timeout keeps its spend reservation until reconciled.

Real-model testing uses synthetic data only, under a separate development budget of USD 10. Nothing personal from the athlete repository enters a prompt.

## 4. Decision record

| Item | Proposal | Owner decision | Date |
| --- | --- | --- | --- |
| Authentication provider | A. Firebase Authentication, Google sign-in and email link | **Firebase Authentication** | 2026-10-29 |
| Model route | Vertex AI (Google managed) | **Provider APIs with server-held keys and per-task routing; Anthropic API first** | 2026-10-29 |
| Initial model | Opus 5.5 at `low` effort, compared on the corpus | **Claude Haiku 5.5** at `low` effort; escalate only after a corpus comparison | 2026-10-29 |
| Global monthly model budget | USD 25 | **USD 25 across all providers** | 2026-10-29 |
| Other limits (section 3) | Input size, tokens, attempts, daily quota, concurrency, development budget | Pending | — |
| Minimum field-level accuracy | Set after the first corpus run | Pending (deferred to the first run, as proposed) | — |
| Provider retention and data processing (part of D7) | Review Firebase/Identity Platform and Anthropic API terms before onboarding; each additional provider before use | Pending | — |

## 5. Still open after this decision

- Frontend technology and the assisted-operation interface (technical plan, section 6).
- Reviewer and operations availability (PRD D3).
- Import formats beyond pasted text (PRD D6).
- Retention, deletion, and provider processing for external participants (PRD D7).
- Row-level security as defense in depth (technical plan, section 2).
