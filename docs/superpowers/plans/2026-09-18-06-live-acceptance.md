# Live Multi-User Acceptance

This document is a manual gate. It is not part of the offline test suite and must not run automatically.

## Preconditions

1. All offline release gates pass.
2. User A and User B give explicit user approval before any live Feishu check.
3. Each user authenticates with their own `lark-cli` account.
4. The target Feishu Wiki is the only shared authority.

## Feishu acceptance

1. User A runs `preflight`.
2. User A completes `prepare`, `publish`, and `verify`.
3. User B runs `preflight`.
4. User B edits one target Wiki page.
5. User A reruns sync; the human edit survives.
6. Both users load knowledge and confirm the same `revision_id`.
7. User B attempts a concurrent publish; only one lease may win.
8. Record page links, revision IDs, timestamps, and any conflict decisions.

## Optional image acceptance

1. The user explicitly confirms one prompt, output directory, size, and quality.
2. The user supplies the API key for that command only.
3. The generated image and metadata are inspected locally.
4. Confirm that no key is printed, logged, committed, or exported.

## Stop conditions

- A human edit is lost.
- Two publishes change the same page without a lease decision.
- Users receive different authoritative `revision_id` values without an explanation.
- Any credential appears in output.

The release may proceed only after all blockers are resolved and acceptance results are recorded.
