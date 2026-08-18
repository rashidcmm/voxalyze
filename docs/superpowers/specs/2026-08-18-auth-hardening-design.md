# Auth Hardening — Email Verification, Password Reset, Security Fixes — Design

**Status:** Approved for planning
**Date:** 2026-08-18

## Background

The app (`GD/Debate Speech Trainer`) currently has a minimal auth layer built on Day 1
(`backend/app/api/auth.py`, `backend/app/core/security.py`): signup/login/me, JWT
(HS256, 7-day expiry), bcrypt password hashing via passlib, duplicate-email rejection
on signup, `EmailStr` validation. There is no email verification, no password reset,
no rate limiting on any auth endpoint, and no account lockout.

This is the first of four sub-projects scoped out of a larger request (the others —
multi-party room expansion, personal analytics wired into rooms, and video/proctoring
— are queued separately and each will get their own spec). This spec covers only:
email verification, forgot/reset password via Brevo, and fixing the vulnerabilities
found while reviewing the existing auth code.

## Goals

- New users must verify their email before they can log in.
- Users who forget their password can reset it via an emailed link, without leaking
  whether a given email is registered.
- Email delivery via Brevo's transactional HTTP API.
- Close the vulnerabilities found in the current auth implementation (below).
- Rate limit auth endpoints using the Redis instance the app already runs for ARQ.

## Non-goals (this spec)

- OAuth/social login.
- Refresh-token rotation / short-lived access tokens with a refresh flow (current
  7-day single JWT is kept; password-reset now invalidates it early — see Flows).
- Full session management UI (list/revoke active sessions).
- CAPTCHA or bot-detection on signup.
- Fixing the login timing side-channel (documented as a known, low-severity
  limitation — see Security review).

## Security review of the existing implementation

| Finding | Severity | Fix in this spec |
|---|---|---|
| `jwt_secret` in `core/config.py` defaults to a literal placeholder string, which is now public (committed to this repo). If a deployment ever runs without overriding it, anyone can forge a valid token for any user. | High | Add `env: str` setting; fail fast at startup if `jwt_secret` is still the default and `env != "dev"`. |
| No rate limiting on any `/auth/*` endpoint — `/auth/login` is fully brute-forceable. | High | Redis-backed rate limiting (see Rate limiting). |
| Adding email sending introduces a new abuse surface: unlimited `/auth/signup` or a future `/auth/forgot-password` calls let an attacker email-bomb an arbitrary address using this app's Brevo quota/sender reputation. | High | Same rate limiting, keyed by IP *and* by target email. |
| A naive `/auth/forgot-password` response would reveal whether an email is registered (user enumeration). | Medium | Always return an identical generic response regardless of whether the email exists. |
| bcrypt only considers the first 72 bytes of a password; the current schema allows up to 128 chars, so two long passwords could silently collide on their first 72 bytes. | Low | Cap `password` at `max_length=72` in signup/reset schemas. |
| Login's `user is None or not verify_password(...)` short-circuits (skips bcrypt) when the email isn't found, creating a measurable timing difference between "no such user" and "wrong password." | Low | Documented, not fixed — real but low-severity for this app's threat model; revisit if it ever matters. |

Confirmed **not** an issue: password hashing (bcrypt via passlib) is solid as-is;
duplicate-email rejection on signup is fine (disclosing "already registered" at
signup time, unlike at login/reset, is standard practice); bearer-token-in-header
auth means no CSRF exposure.

## Data model

- `users` table gains two columns:
  - `is_verified: bool`, default `false`
  - `password_changed_at: datetime`, nullable (set on first successful reset;
    `NULL` means "never reset," treated as "any token issued after account
    creation is valid")
- New `email_tokens` table: `id, user_id, token_hash (SHA-256 hex), purpose
  ('verify' | 'reset'), expires_at, used_at (nullable), created_at`. One table
  serves both flows via `purpose`, following the existing project convention of
  reusing a shared shape (`sessions`/`transcripts` pattern) rather than two
  near-identical tables.
- No new table for rate limiting — that's Redis counters (`INCR` + `EXPIRE`),
  not persisted state.

Tokens are opaque, high-entropy random strings (`secrets.token_urlsafe(32)`),
never stored raw — only their SHA-256 hash is persisted, so a DB compromise
doesn't yield usable tokens (same principle as never storing raw passwords).

## Flows

**Signup → verify:**
1. `POST /auth/signup` creates the user (`is_verified=false`), issues a `verify`
   token (24h expiry), emails it via Brevo, and returns a success message —
   **no** access token this time (verification is required before login works).
2. `POST /auth/login` rejects with a clear error if `is_verified=false`
   (distinct from the generic "incorrect email or password" — this is fine to
   disclose to someone who already knows the correct credentials).
3. `POST /auth/verify-email {token}` looks up the token by hash, checks
   `purpose='verify'`, not expired, not used; on success sets
   `is_verified=true`, marks the token used, returns success.
4. `POST /auth/resend-verification {email}` (rate-limited): if the user exists
   and is unverified, invalidates any prior unused `verify` tokens for that
   user and issues a new one. Always returns the same generic response.

**Forgot → reset:**
1. `POST /auth/forgot-password {email}` always returns the same generic
   message ("if that email is registered, we've sent a reset link"). If the
   email exists, issues a `reset` token (1h expiry) and emails it.
2. `POST /auth/reset-password {token, new_password}` validates the token
   (hash lookup, `purpose='reset'`, not expired, not used), updates
   `password_hash`, sets `password_changed_at = now()`, marks the token used,
   and invalidates any other outstanding `reset` tokens for that user.
3. `get_current_user` (in `app/api/deps.py`) additionally checks the JWT's
   `iat` claim against the user's `password_changed_at`: a token issued
   before the last password change is rejected. This is the mechanism that
   makes a reset actually invalidate existing sessions, without needing a
   revocation list. `create_access_token` (in `core/security.py`) currently
   encodes only `sub` and `exp` — this spec requires adding `iat` to that
   payload as part of the same change.

## Rate limiting (Redis)

Using the app's existing Redis instance (`redis_url`, already used for ARQ),
a small `app/core/rate_limit.py` helper (`INCR` + `EXPIRE` per key) applied as
a FastAPI dependency:

- `/auth/login` — limited per email *and* per IP (e.g. 5 attempts / 5 min)
- `/auth/signup` — limited per IP (e.g. 5 / hour)
- `/auth/forgot-password` and `/auth/resend-verification` — limited per email
  (e.g. 1 / 60s) specifically to prevent email-bombing via Brevo

Exceeding a limit returns `429 Too Many Requests` with a `Retry-After` header.

## Brevo integration

New `app/core/email.py`: a single `send_email(to, template, context)` function
wrapping Brevo's transactional-email HTTP API via `httpx` (already a project
dependency) — matching the existing pattern of calling Azure/Anthropic as
plain HTTP calls rather than pulling in a provider SDK. Two templates:
verify-email and reset-password, each a simple string template (subject +
HTML body) built from `context` (name, link) — no external template engine
needed at this scale.

New settings in `core/config.py`: `brevo_api_key`, `brevo_sender_email`,
`brevo_sender_name`, `frontend_base_url` (used to build the verify/reset
links, e.g. `{frontend_base_url}/verify-email?token=...`).

Email send failures are logged and raise a `5xx` on signup/forgot-password
(the user needs to know the email didn't go out) but never leak *why* to the
client beyond a generic "couldn't send verification email, try again" —
matching the existing project's pattern of never leaking internal error detail.

## API surface

```
POST /auth/signup                 → creates user (unverified), sends verify email
POST /auth/login                  → JWT (rejects unverified users)
GET  /auth/me                     → unchanged
POST /auth/verify-email           {token} → marks verified
POST /auth/resend-verification    {email} → generic response, rate-limited
POST /auth/forgot-password        {email} → generic response, rate-limited
POST /auth/reset-password         {token, new_password} → updates password
```

## Frontend

New pages under `frontend/src/app/`:
- `/verify-email` — reads `token` from the URL query string, POSTs it (not a
  GET side-effect, so the token doesn't do anything just by being visited/
  linked), shows success/failure, links to login.
- `/forgot-password` — email input, submits, shows the generic confirmation.
- `/reset-password` — reads `token` from URL, new-password form, POSTs,
  redirects to login on success.

`login/page.tsx` gets a "resend verification email" link shown specifically
when the login error indicates an unverified account. `authStore.ts`/`api.ts`
get the new calls (`verifyEmail`, `resendVerification`, `forgotPassword`,
`resetPassword`); `signup` no longer sets a token on success — it now routes
to a "check your email" screen instead of the dashboard.

## Error handling

- Expired/used/invalid token on verify or reset → clear, generic error
  ("this link is invalid or has expired"), no distinction between the three
  cases (distinguishing them doesn't help a legitimate user and could help an
  attacker probe token validity).
- Rate limit exceeded → `429` with `Retry-After`; frontend shows a
  "try again in a moment" message.
- Brevo API failure → `5xx`, generic "couldn't send email, please try again"
  message; the user row/token is still created so a retry
  (resend-verification / forgot-password again) works without re-signing-up.
- Login with unverified account → distinct, actionable error message + a
  resend-verification affordance, as above.

## Testing plan

Backend already has pytest wired up (from the in-progress rooms work) —
extend it:
- Unit: token generation/hashing, expiry, single-use enforcement, invalidation
  of prior tokens on reissue, the `password_changed_at`-vs-`iat` check in
  `get_current_user`.
- Unit: rate limiter behavior (under limit passes, at limit blocks, resets
  after window).
- Integration (real test Postgres + Redis, mocked Brevo call): full
  signup → verify → login round trip; full forgot → reset → login round trip,
  including confirming a pre-reset JWT no longer authenticates after reset.
- Manual: confirm `/auth/forgot-password` returns an identical response for a
  registered vs. unregistered email (byte-for-byte, including timing — or at
  least no obviously distinguishing latency).

## Deferred (future work, not this spec)

- Refresh-token rotation / shorter-lived access tokens.
- Session management UI (view/revoke active sessions).
- Closing the login timing side-channel.
- CAPTCHA/bot-detection on signup.
- Production CORS origin configuration (currently locked to
  `localhost:3000` — a pre-deploy concern, not an auth-hardening one).
