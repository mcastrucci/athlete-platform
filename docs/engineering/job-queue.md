# Durable extraction queue

Date: 2026-10-29. Implements prerequisite issue #11 for the text-import journey in #6.

## Scope and boundaries

This slice provides a Python queue service and a bounded runner. It does not add upload APIs, source documents, interpretations, confirmation, canonical sessions, or an extraction provider. No real model is called. The existing CLI still exits without connecting because no production handler has been configured.

The next #6 slice must connect immutable sources and schema-validated extraction to this queue. Payload JSON is bounded transport data, not an authoritative training representation. A completed queue job alone does not authorize or confirm training. Same-content/new-request detection also belongs to the source workflow; queue idempotency only handles the same request key.

## Queue contract

PostgresJobs exposes ownership-scoped enqueue, get, history, and retry operations. It identifies the caller by verified issuer/subject, not payload identifiers. A composite athlete/owner foreign key prevents cross-owner jobs, and attempts reference the matching job/athlete pair.

Trusted worker operations claim, heartbeat, complete, and fail use opaque lease capabilities and are not HTTP endpoints. Never expose the global worker queue or lease tokens to athlete clients. RLS remains deferred under the profile decision.

States are queued, running, deferred, succeeded, and failed. Jobs preserve the original input, its canonical JSON hash, athlete-scoped idempotency key, attempt budget, next eligible time, sanitized failure code, and result. Attempts retain their number, lease token, timestamps, outcome, and error code. Normal service operations never rewrite input/history.

Same key and input returns the existing job; the same key with different input conflicts. A successful completion repeated with the same token/result acknowledges the existing result. Different output or an obsolete token cannot overwrite it. Failed jobs may be retried explicitly against the expected attempt count, receiving another bounded attempt allowance without erasing history or bypassing quotas.

## Transactions and recovery

Claim uses row locks and SKIP LOCKED. The transaction reserves an attempt and commits its lease before the handler runs. Heartbeats can extend only a live lease. All heartbeat/completion/failure writes lock the job and check its current token, state, and expiry against PostgreSQL's clock; expired or replaced leases cannot publish.

An abandoned running job becomes eligible when its lease expires. Reclaim records the previous attempt as expired and issues a fresh token. Exhausted work becomes visibly failed. Output, success state, and attempt outcome commit together. Deleted jobs cannot be resurrected by late workers.

Delivery is at least once. External provider calls may repeat after crashes/timeouts; this queue does not guarantee exactly-once billing. Future provider integration must reserve/reconcile spend separately and prevent unreviewed results from becoming canonical data.

## Bounds and operational behavior

QueueLimits currently defaults to a 120-second lease, three attempts per retry cycle, 100 active jobs per athlete, and 50 attempts per athlete per UTC day. These are configurable infrastructure defaults for the synthetic/local slice, not a promised pilot budget. Attempts count even when a worker later crashes.

Active capacity is checked under an athlete lock for both enqueue and manual retry. Daily reservations serialize across different jobs for the same athlete. A daily-quota block defers the job until the next UTC midnight; retry cannot bypass that delay. Reaching active capacity raises QueueFull and retains existing work; a future API must expose a visible retryable response.

Retries use bounded exponential backoff with jitter. Terminal errors persist a controlled code without exception text or private payloads. Input and result JSON are limited to 64 KiB and reject non-finite values. This does not define file-upload limits or model token budgets.

The runner drains at most max_jobs and checks a monotonic runtime budget between jobs, then exits without idle polling. Handlers receive a lease-renewal callback, must check renewal success, bound their own calls, and renew before expiry. The runner cannot interrupt an in-flight Python function. The default CLI deliberately has no handler; it does not claim work and leave it stranded.

Cloud Run launch and scheduled recovery are not provisioned here. A later integration must arrange wakeups for due retries and expired leases; otherwise persisted work simply waits. Claim bounds housekeeping to 100 deferred/exhausted candidates per call, so large backlogs may require another worker execution.

## Verification

PostgreSQL integration tests cover ownership, composite foreign keys, same-key conflicts, simultaneous claims, explicitly locked rows, lease renewal and expiry, crash/reclaim, stale-token writes, duplicate completion, retry history, concurrent quotas, deletion, connection release during handlers, and worker budgets. Tests use only a disposable database and synthetic jobs.

Before connecting a real model, finish #3 and the remaining #6 source/schema/correction/confirmation flow, configure token/attempt/spend limits, and run the controlled extraction corpus including instruction-injection and content-duplicate cases.
