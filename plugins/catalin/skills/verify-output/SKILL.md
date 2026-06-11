---
name: verify-output
description: Force a verification loop before declaring any non-trivial task done — actually run tests/build/app or check real output, then produce a table of every claim and whether it was verified. Use when finishing a code change, bug fix, deployment, or any task where correctness matters, or when asked to "verify", "double-check", "prove it works", "confirm the fix".
---

# verify-output

> Author: Catalin.

> Verification is the single highest-impact practice. A write → run → check → repeat loop reliably beats one-shot output. Never declare done on unverified claims.

When invoked:

1. **Pick a domain-appropriate feedback loop and actually run it** (don't reason about it — execute it):
   - Code/logic → write or run tests (prefer TDD: write a failing test first), then run the code and read the output.
   - Backend → run the test suite AND confirm the app boots.
   - Frontend → lint touched files AND confirm the build succeeds; load the page (Playwright/MCP or `/chrome`) if behavior matters.
   - CLI/interactive → drive it and capture the output.
   - Infra/deploy → curl the live endpoint, check the process/health, hit it from outside.
2. **Re-read every claim you are about to make** and check it against the actual observed output — not against what you intended to do.
3. **Output a verification table:**

   | Claim | How verified | Result |
   |---|---|---|
   | … | ran `npm test` (42 pass) | ✅ verified |
   | … | curled prod URL → 200 | ✅ verified |
   | … | not run | ❌ unverified |

4. If anything is ❌ or ⚠️, fix it or state the limitation explicitly. Do not present unverified work as done.
