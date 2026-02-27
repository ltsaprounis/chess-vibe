#!/usr/bin/env bash
# Create GitHub issues for non-blocking observations from PR #38 validation.
# Run with: ./scripts/create-pr38-issues.sh
# Requires: gh CLI authenticated with repo access.
set -euo pipefail

REPO="ltsaprounis/chess-vibe"

echo "Creating 5 issues for PR #38 non-blocking observations..."

# Issue 1: WebSocket integration tests
gh issue create \
  --repo "$REPO" \
  --label "copilot" \
  --title "Add WebSocket integration tests for /ws/play and /ws/sprt/{id}" \
  --body "## Description

The WebSocket endpoints \`/ws/play\` and \`/ws/sprt/{id}\` are only verified for route registration in \`test_main.py\` but lack integration tests using \`TestClient.websocket_connect()\`. The acceptance criteria in #9 specify \"WebSocket sessions\" testing.

Identified during validation of PR #38.

## Acceptance Criteria

- [ ] Add integration tests for \`WS /ws/play\` using \`TestClient.websocket_connect()\` covering:
  - Start a game session (send \`{\"type\": \"start\", ...}\`)
  - Receive \`{\"type\": \"started\", ...}\` acknowledgement
  - Send a player move and receive engine move + evaluation
  - Handle game-over scenarios
  - Handle player resignation
  - Handle invalid messages / error cases
  - Verify cleanup on disconnect
- [ ] Add integration tests for \`WS /ws/sprt/{id}\` using \`TestClient.websocket_connect()\` covering:
  - Subscribe to a running test and receive progress updates
  - Handle subscription to a non-existent test
  - Handle client disconnect / unsubscribe cleanup
- [ ] All new tests pass under pyright strict mode
- [ ] All existing tests continue to pass

## Implementation Details

- Component: \`backend/tests/\`
- Files: \`backend/tests/test_ws_play.py\`, \`backend/tests/test_ws_sprt.py\`
- Engine and subprocess interactions should be mocked
- Reference: #9, #38"

echo "✓ Issue 1 created"

# Issue 2: Route-level test for POST /sprt/tests
gh issue create \
  --repo "$REPO" \
  --label "copilot" \
  --title "Add route-level test for POST /sprt/tests" \
  --body "## Description

The \`POST /sprt/tests\` endpoint is tested at the service layer (\`test_sprt_service.py\`) but has no HTTP-level test exercising the route through \`TestClient\`. This leaves a gap in route-level test coverage.

Identified during validation of PR #38.

## Acceptance Criteria

- [ ] Add HTTP-level test for \`POST /sprt/tests\` in \`backend/tests/test_routes_sprt.py\` covering:
  - Successful test creation returns 201 with \`{\"id\": \"...\", \"status\": \"running\"}\`
  - Validation errors return appropriate 4xx status codes
  - Internal errors (subprocess launch failure) return 500
- [ ] SPRT runner subprocess should be mocked to avoid real subprocess invocation
- [ ] All new tests pass under pyright strict mode
- [ ] All existing tests continue to pass

## Implementation Details

- Component: \`backend/tests/test_routes_sprt.py\`
- Mock \`SPRTService.start_test\` to avoid real subprocess invocation
- Reference: #9, #38"

echo "✓ Issue 2 created"

# Issue 3: Inline imports
gh issue create \
  --repo "$REPO" \
  --label "copilot" \
  --title "Hoist inline imports to module level in backend production code" \
  --body "## Description

Two files in the backend use inline imports inside function bodies instead of module-level imports:
- \`backend/src/backend/ws/play.py:167\` — \`from shared.storage.models import GameResult\`
- \`backend/src/backend/services/sprt_service.py:262\` — \`from shared.storage.models import SPRTTestFilter\`

These should be hoisted to module-level imports for consistency with the rest of the codebase.

Identified during validation of PR #38.

## Acceptance Criteria

- [ ] Move \`from shared.storage.models import GameResult\` to module-level imports in \`backend/src/backend/ws/play.py\`
- [ ] Move \`from shared.storage.models import SPRTTestFilter\` to module-level imports in \`backend/src/backend/services/sprt_service.py\`
- [ ] No circular import issues introduced
- [ ] Pyright strict passes
- [ ] All existing tests continue to pass

## Implementation Details

- Component: \`backend/src/backend/\`
- Files: \`backend/src/backend/ws/play.py\`, \`backend/src/backend/services/sprt_service.py\`
- Reference: #9, #38"

echo "✓ Issue 3 created"

# Issue 4: OpeningBookRepository ABC
gh issue create \
  --repo "$REPO" \
  --label "copilot" \
  --title "Add OpeningBookRepository ABC for opening book storage" \
  --body "## Description

The opening books endpoints in \`backend/src/backend/routes/openings.py\` access the filesystem directly via \`data_dir / \"openings\"\` instead of going through a repository ABC. This bypasses the storage abstraction pattern used by \`GameRepository\` and \`SPRTTestRepository\`, making it harder to swap storage backends (e.g., \`FileStore\` → \`SQLiteStore\`).

Identified during validation of PR #38.

## Acceptance Criteria

- [ ] Add \`OpeningBookRepository\` ABC in \`shared/src/shared/storage/repository.py\` with methods:
  - \`list_opening_books() -> list[OpeningBook]\`
  - \`get_opening_book(book_id: str) -> OpeningBook | None\`
  - \`save_opening_book(book: OpeningBook, content: bytes) -> None\`
- [ ] Add \`FileOpeningBookRepository\` implementation in \`shared/src/shared/storage/file_store.py\`
- [ ] Update \`backend/src/backend/routes/openings.py\` to use the repository ABC instead of direct filesystem access
- [ ] Add tests for the new repository implementation
- [ ] Pyright strict passes for both \`shared/\` and \`backend/\`
- [ ] All existing tests continue to pass

## Implementation Details

- Components: \`shared/src/shared/storage/\`, \`backend/src/backend/routes/openings.py\`
- Follow existing patterns from \`GameRepository\` / \`FileGameRepository\`
- Reference: #9, #38"

echo "✓ Issue 4 created"

# Issue 5: SPRT recovery semantics
gh issue create \
  --repo "$REPO" \
  --label "copilot" \
  --title "Clarify SPRT recovery semantics — CANCELLED vs failed" \
  --body "## Description

Issue #9 specifies that on startup, stale \`RUNNING\` SPRT tests should be marked as \`failed\`. The PR #38 implementation marks them as \`CANCELLED\` instead. While \`CANCELLED\` is arguably more semantically appropriate (subprocess handle lost ≠ test failure), this diverges from the original acceptance criteria.

The issue description and implementation should be aligned on the correct status.

Identified during validation of PR #38.

## Acceptance Criteria

- [ ] Decide on the correct recovery status: \`CANCELLED\` or a new \`FAILED\` status
- [ ] If keeping \`CANCELLED\`: update the acceptance criteria in #9 to reflect the actual behavior
- [ ] If changing to \`FAILED\`: add \`FAILED\` to \`SPRTStatus\` enum and update \`sprt_service.py\` recovery logic
- [ ] Ensure recovery tests match the chosen semantics
- [ ] Pyright strict passes
- [ ] All existing tests continue to pass

## Implementation Details

- Components: \`shared/src/shared/storage/models.py\` (if adding \`FAILED\`), \`backend/src/backend/services/sprt_service.py\`
- Reference: #9, #38"

echo "✓ Issue 5 created"
echo ""
echo "All 5 issues created successfully!"
