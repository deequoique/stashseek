# Cross-platform Channel Identity Linking

## 1. Scope / Trigger

Use this contract when changing `/link`, `ChannelIdentity`,
`ChannelLinkToken`, trusted channel envelopes, or tenant ownership merges.
The flow is deterministic and runs before conversation persistence or Agent
execution.

## 2. Signatures

```python
LINKABLE_CHANNELS = frozenset({"telegram", "wechat", "web"})
classify_link_argument(value: str) -> tuple[Literal["channel", "token"], str]
create_link_token(db, tenant, *, target_channel, ttl, now) -> str
consume_link_token(db, envelope, raw_token, *, now) -> TenantContext
```

Tokens are generated with `secrets.token_urlsafe(32)`, persisted only as a
SHA-256 hash, expire at `expires_at`, and are consumed at most once.

## 3. Contracts

- `/link telegram`, `/link wechat` or `/link web` is generation syntax. The target must be
  supported and different from the current source channel.
- A value matching the generated 43-character token shape is consumption
  syntax. Unknown channel-like values fail as unsupported channels; malformed
  values fail as invalid tokens.
- Consumption validates token existence, expiry, replay state, target channel,
  source/target enabled state, then merges in one database transaction.
- The token creator's `AppUser` survives. All target identities, conversations,
  turns, pending actions, tokens and non-duplicate content move to it; channel
  conversation history remains separate.
- Duplicate content keeps one row. Merge `saved_at` using the earliest value,
  preserve distinct non-empty `why_saved` values, keep the greatest watch
  position and the more complete watch state, and prefer ready/more complete
  ingestion data. Do not interleave incompatible segment sets.
- A target `running` ingestion, or a running duplicate that would be retired,
  returns `link_merge_busy` without consuming the token. Pending/enqueued
  duplicate deliveries become no-ops after retirement.
- Website-provider mappings move with the absorbed user's exact email anchors;
  their deferred composite ownership FK must agree before commit, and absorbed
  provider sessions remain revoked. Direct CLI rebind of an externally anchored
  identity is rejected. Ordinary provider login never invokes this merge; see
  [External Website Login](./external-login.md).
- Production diagnostics and smoke evidence never contain raw tokens, message
  bodies, names, external sender IDs, URLs or complete identity mappings.

## 4. Validation & Error Matrix

| Condition | Result |
| --- | --- |
| unsupported/current target channel | `link_channel_unsupported` / `link_channel_current` |
| malformed, unknown, expired or replayed token | fixed invalid/expired/used result; no ownership change |
| target-channel mismatch | `link_channel_mismatch`; token remains unconsumed |
| disabled source, target or identity | `link_account_disabled`; no merge |
| same tenant already linked | idempotent success; valid token is consumed |
| different registered target | atomic tenant merge |
| running target ingestion | `link_merge_busy`; retry with the same token |
| uniqueness or ownership race | transaction rollback and `link_merge_conflict` |

## 5. Good / Base / Bad Cases

- Good: an already-used target account presents a valid code and both `/whoami`
  commands return the source internal number while histories remain separate.
- Base: a fresh target account presents a valid, channel-bound code and is
  linked without invoking the model.
- Bad: treat every `/link` argument as a target channel, use display names for
  ownership, consume a code before merge commit, or move only the target
  `ChannelIdentity` while leaving tenant content behind.

## 6. Tests Required

- Unit-test argument classification, supported/current channels, token TTL,
  replay, mismatch and disabled-account fail-closed behavior.
- PostgreSQL-test registered-target merge, duplicate metadata/segment rules,
  conversation isolation, pending/token ownership and unique-key conflicts.
- PostgreSQL concurrency-test token replay, registration-vs-consumption and
  running-ingestion retry; assert at most one success and no partial move.
- ChannelService-test `/link` generation/consumption with zero Agent calls and
  stable `/whoami`; bridge smoke must verify Telegram and WeChat in both
  directions without recording secrets.

## 7. Wrong vs Correct

### Wrong

```python
# Every argument becomes a new target channel, so a code is generated again.
token = create_link_token(db, tenant, target_channel=argument)
```

### Correct

```python
kind, value = classify_link_argument(argument)
if kind == "token":
    consume_link_token(db, envelope, value)
else:
    create_link_token(db, tenant, target_channel=value, ttl=configured_ttl)
```
