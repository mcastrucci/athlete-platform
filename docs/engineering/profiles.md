# Profile access and versioning

Date: 2026-10-28. Implements the profile/ownership slice in issue #5; provider selection remains issue #3.

## Identity boundary

The API accepts bearer credentials through an IdentityVerifier contract. The initial production-capable adapter verifies RS256 JWT signatures using a configured HTTPS JWKS endpoint and checks exact issuer, audience, expiry, issued-at, and a nonempty subject. No token header selects a verification URL or algorithm. Users are keyed by (issuer, subject), not email or a caller-supplied user ID.

Configure ATHLETE_OIDC_ISSUER, ATHLETE_OIDC_AUDIENCE, and ATHLETE_OIDC_JWKS_URL together. The issuer remains exact; URL normalization does not add a trailing slash. Missing configuration fails closed. No local header, token string, or environment flag can select a fake identity in the running API.

Tests may inject a verifier through create_app only when environment=test and Cloud Run service/job markers are absent. The deployed entry point does not pass this argument. Synthetic tests also verify real JWT signatures and negative claims without reaching a live identity provider.

This adapter does not provide login, refresh, revocation, account linking, or provider-specific token-type validation. Confirm the selected provider's issuer, API audience, signing algorithm, key rotation, and access-token contract in #3 before external onboarding. JWKS failures return 503; invalid credentials return 401 without reflecting tokens.

## Data and API contracts

The initial scope is one athlete per identity. Shared coaches, delegated access, and ownership transfer are not implemented.

| Endpoint | Behavior |
| --- | --- |
| POST /v1/athletes | Create the caller's athlete and revision 1; duplicate creation returns 409 |
| GET /v1/athletes/{id}/profile | Read the caller's latest revision |
| GET /v1/athletes/{id}/profile?version=1 | Read a specific owned historical revision |
| PUT /v1/athletes/{id}/profile | Replace the complete profile with expected_version and content; stale versions return 409 |

Creation accepts ProfileContent directly. Example:

```json
{
  "timezone": "America/Argentina/Buenos_Aires",
  "equipment": ["barbell"],
  "availability": [{"weekday": 0, "minutes": 60}],
  "commitments": [{"description": "Coach session", "flexibility": "fixed", "weekdays": [0]}]
}
```

Updates wrap the full content as {"expected_version": 1, "content": {...}}. This is replacement, not PATCH: omitting an optional field sets it to unknown in the new revision; an explicit empty list means none reported. Earlier revisions are preserved. Weekdays are 0 (Monday) through 6 (Sunday).

Optional context includes experience, prioritized goals, availability, equipment, fixed/flexible commitments, dated baseline metrics with units and source, reported skills, and hard constraints/preferences. Unknown values are null; no personal defaults are copied. A programming_reference is descriptive text at this stage, not a foreign key or authorization grant. Real plan relationships must use scoped identifiers when plans exist.

Each response contains athlete_id, version, created_at, and content. UTC-aware recording times are separate from local dates on baseline observations and the athlete's IANA timezone. Revision payloads use schema-versioned JSONB; ownership, identity, versions, and timestamps are relational columns. Future training calculations consume validated typed context, not arbitrary unvalidated JSON.

## Transactions and isolation

Every read first selects the athlete by verified issuer/subject and athlete ID. Other users and absent resources both return 404. Historical reads use that same scope. Request contracts forbid owner_user_id and athlete_id fields in profile content.

The database uniquely identifies users, allows one athlete per owner, and enforces a composite foreign key from (athlete_id, owner_user_id) in revisions to the matching athlete. A revision cannot point at one athlete while naming another owner.

An update atomically compares the expected version and increments the athlete counter. Revision insertion and counter change commit together. Two simultaneous requests with the same expected version yield one success and one conflict; failure rolls back the transaction. Normal API operations only append history; privileged direct SQL can still alter records and must not be exposed to athletes.

RLS decision for this slice: deferred. Application authorization, scoped repositories, database ownership constraints, and two-user tests are required now. This is not defense against privileged database access. Before adding broader data access, revisit transaction-local RLS using a non-owner/non-bypass role and test it with Neon's transaction pooling. No session-persistent tenant context is assumed.

## Validation

PostgreSQL tests cover two distinct profiles, unauthorized and forged requests, historical reads, cross-owner foreign keys, duplicate creation, and concurrent expected-version updates using two database connections synchronized before UPDATE. Tests also verify rollback/migrations and loaded ORM attributes after session close.

No deployment, credentials, provider accounts, personal fixtures, or expert assessment are included in this slice. The remaining P1 onboarding and review requirements remain in the pilot plan.
