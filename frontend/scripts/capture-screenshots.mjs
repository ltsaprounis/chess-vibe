/**
 * Capture UI screenshots into docs/screenshots/.
 *
 *   npm run screenshots            # from frontend/
 *
 * Self-contained on purpose — no dev server and no backend need to be
 * running, and nothing has to be seeded into data/:
 *
 *   * Vite is booted in-process on an ephemeral port via its programmatic
 *     API, so the script owns the server's lifetime and never collides
 *     with a `make dev` you already have open.
 *   * `/api/**` is intercepted in the browser and fulfilled from the
 *     fixture below, so the screenshots are deterministic and do not
 *     depend on whatever games happen to be in data/. The Vite proxy to
 *     the backend is never reached.
 *   * The game itself is generated with chess.js (already a dependency)
 *     from a SAN move list, so the FENs are real rather than hand-copied.
 *
 * Uses `playwright-core` plus the locally installed Chrome rather than the
 * `playwright` package. `playwright` downloads a private browser bundle in
 * a postinstall hook, and since lockfiles are gitignored here and CI runs a
 * plain `npm install` with no cache, that download would be paid on every
 * single CI run for a tool CI never invokes. Set CHROME_PATH to override
 * the browser binary.
 */

import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { Chess } from 'chess.js'
import { chromium } from 'playwright-core'
import { createServer } from 'vite'

const FRONTEND_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const OUT_DIR = resolve(FRONTEND_DIR, '..', 'docs', 'screenshots')

/**
 * Morphy vs Duke Karl / Count Isouard, Paris 1858 — the Opera Game.
 *
 * Written as one string rather than an array literal so the move list stays
 * readable: Prettier only packs arrays onto shared lines when every element
 * is a number, and would otherwise spread these one per line.
 */
const OPERA_GAME_SAN = (
  'e4 e5 Nf3 d6 d4 Bg4 dxe5 Bxf3 Qxf3 dxe5 Bc4 Nf6 Qb3 Qe7 Nc3 c6 Bg5 b5 Nxb5 cxb5 ' +
  'Bxb5+ Nbd7 O-O-O Rd8 Rxd7 Rxd7 Rd1 Qe6 Bxd7+ Nxd7 Qb8+ Nxb8 Rd8#'
).split(' ')

/**
 * Centipawn scores as the engines would report them: relative to the side
 * that just moved, so they alternate sign as White's advantage grows.
 *
 * `_` marks a move with no recorded evaluation — index 12 is deliberately
 * blank so the screenshot documents how a gap in the eval data renders (the
 * graph bridges it rather than emitting a NaN coordinate). The final entry is
 * blank too because that move is reported as mate instead.
 */
const OPERA_GAME_SCORES = (
  '20 -15 35 -40 60 -55 90 -70 120 -95 150 -120 _ -140 210 -160 ' +
  '260 -200 330 -260 420 -350 500 -430 640 -560 780 -690 950 -880 1400 -1300 _'
)
  .split(' ')
  .map((token) => (token === '_' ? null : Number(token)))

/** Position after 1. e4 — Black to move, as an odd-ply opening book line leaves it. */
const BOOK_START_FEN = 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1'

/**
 * Build a GameDetail payload matching the backend's response shape.
 *
 * @param {object} options
 * @param {string} options.id
 * @param {string[]} options.san Move list in SAN.
 * @param {(number|null)[]} options.scores Mover-relative centipawn scores.
 * @param {string|null} options.startFen Custom start FEN, or null for the standard one.
 * @param {boolean} options.mateOnLastMove Report the final move as mate-in-1 rather
 *   than a centipawn score. Only true for a game that actually ends in mate.
 * @param {object} options.meta Remaining GameDetail fields.
 */
function buildGame({ id, san, scores, startFen, mateOnLastMove, meta }) {
  const board = startFen ? new Chess(startFen) : new Chess()
  const moves = san.map((notation, i) => {
    const played = board.move(notation)
    const isMate = mateOnLastMove && i === san.length - 1
    // Look a few moves ahead for a plausible principal variation.
    const pv = []
    const probe = new Chess(board.fen())
    for (const next of san.slice(i + 1, i + 4)) {
      const pvMove = probe.move(next)
      if (!pvMove) break
      pv.push(pvMove.from + pvMove.to)
    }
    return {
      uci: played.from + played.to,
      san: played.san,
      fen_after: board.fen(),
      score_cp: isMate ? null : (scores[i] ?? null),
      score_mate: isMate ? 1 : null,
      depth: 18 + (i % 4),
      seldepth: 24 + (i % 5),
      pv,
      nodes: 850_000 + i * 12_345,
      time_ms: 950 + (i % 7) * 30,
      clock_white_ms: 60_000 - i * 900,
      clock_black_ms: 60_000 - i * 870,
    }
  })

  return {
    id,
    moves,
    created_at: '2026-08-05T14:30:00Z',
    sprt_test_id: null,
    start_fen: startFen,
    time_control: {
      type: 'fixed_time',
      movetime_ms: 1000,
      wtime_ms: null,
      btime_ms: null,
      winc_ms: null,
      binc_ms: null,
      moves_to_go: null,
      depth: null,
      nodes: null,
    },
    ...meta,
  }
}

const OPERA_GAME = buildGame({
  id: 'opera-game-demo',
  san: OPERA_GAME_SAN,
  scores: OPERA_GAME_SCORES,
  startFen: null,
  mateOnLastMove: true,
  meta: {
    white_engine: 'random-engine',
    black_engine: 'stockfish',
    result: '1-0',
    opening_name: 'Philidor Defence (Opera Game)',
  },
})

const BOOK_START_GAME = buildGame({
  id: 'book-start-demo',
  san: ['e5', 'Nf3', 'Nc6', 'Bb5'],
  // Every score is +ve for whoever moved, so White-perspective display must
  // alternate sign starting with a NEGATIVE value (Black moves first here).
  scores: [300, 150, 250, 120],
  startFen: BOOK_START_FEN,
  mateOnLastMove: false,
  meta: {
    white_engine: 'white-engine',
    black_engine: 'black-engine',
    result: '1/2-1/2',
    opening_name: 'Book line, Black to move first',
  },
})

/** @type {{name: string, game: object, advanceBy: number, description: string}[]} */
const SHOTS = [
  {
    name: 'game-replay.png',
    game: OPERA_GAME,
    advanceBy: 20,
    description: 'Game Replay at move 20 — board, eval bar, move list, PV and eval graph',
  },
  {
    name: 'game-replay-book-start.png',
    game: BOOK_START_GAME,
    advanceBy: 1,
    description: 'Book-start game (Black to move first) — evals shown from White’s perspective',
  },
]

// Height is just the floor for `fullPage`, which expands to the document.
// Kept close to the page's natural height so the images aren't mostly padding.
const VIEWPORT = { width: 1280, height: 820 }

/** Kill CSS motion so repeated runs produce identical pixels. */
const FREEZE_CSS = `*, *::before, *::after {
  transition: none !important;
  animation: none !important;
  scroll-behavior: auto !important;
  caret-color: transparent !important;
}`

/**
 * Screenshot repeatedly until two consecutive captures are byte-identical.
 *
 * The board animates piece movement in JavaScript, which the CSS above
 * cannot freeze, so a capture taken immediately after clicking through 20
 * moves can catch a half-finished frame. Without this the committed PNG
 * changes on every regeneration and shows up as a spurious diff.
 *
 * @param {import('playwright-core').Page} page
 * @param {object} options Passed through to `page.screenshot`.
 * @returns {Promise<Buffer>}
 */
async function screenshotWhenStable(page, options) {
  let previous = await page.screenshot(options)
  for (let attempt = 0; attempt < 20; attempt++) {
    await page.waitForTimeout(150)
    const current = await page.screenshot(options)
    if (current.equals(previous)) return current
    previous = current
  }
  throw new Error('Page never settled: screenshots kept changing after 20 attempts')
}

async function launchBrowser() {
  const executablePath = process.env.CHROME_PATH
  try {
    return await chromium.launch(executablePath ? { executablePath } : { channel: 'chrome' })
  } catch (cause) {
    throw new Error(
      'Could not launch Chrome. playwright-core uses the browser already installed on ' +
        'this machine rather than downloading one. Install Google Chrome, or point ' +
        'CHROME_PATH at a Chromium-based binary.',
      { cause },
    )
  }
}

async function main() {
  await mkdir(OUT_DIR, { recursive: true })

  const server = await createServer({
    root: FRONTEND_DIR,
    configFile: resolve(FRONTEND_DIR, 'vite.config.ts'),
    // Port 0 picks a free port, so this never fights an already-running dev server.
    server: { port: 0, strictPort: false },
    logLevel: 'warn',
  })
  await server.listen()
  const baseUrl = server.resolvedUrls?.local?.[0]
  if (!baseUrl) throw new Error('Vite did not report a local URL')

  const browser = await launchBrowser()
  try {
    for (const shot of SHOTS) {
      const page = await browser.newPage({ viewport: VIEWPORT, deviceScaleFactor: 2 })

      // Serve the fixture instead of the real backend.
      await page.route('**/api/games/**', (route) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(shot.game),
        }),
      )

      const failures = []
      page.on('pageerror', (err) => failures.push(String(err)))
      page.on('console', (msg) => {
        if (msg.type() === 'error') failures.push(msg.text())
      })

      await page.goto(new URL(`/games/${shot.game.id}`, baseUrl).href, {
        waitUntil: 'networkidle',
      })
      await page.addStyleTag({ content: FREEZE_CSS })

      // Wait for the page to have actually rendered the game, not a spinner.
      await page.getByRole('button', { name: 'Next' }).waitFor()

      for (let i = 0; i < shot.advanceBy; i++) {
        await page.getByRole('button', { name: 'Next' }).click()
      }
      // The eval graph marks the current move; make sure it caught up.
      await page.locator('[data-current="true"]').first().waitFor()

      // Web fonts shift text metrics if they land after the capture.
      await page.evaluate(() => document.fonts.ready)

      // The move list scrolls, and which offset it settles on after clicking
      // through the game is timing-dependent — enough to make the committed
      // PNG differ between runs. Pin every scroll container to the top.
      await page.evaluate(() => {
        for (const el of document.querySelectorAll('*')) {
          if (el.scrollTop !== 0) el.scrollTop = 0
        }
        window.scrollTo(0, 0)
      })

      const outPath = resolve(OUT_DIR, shot.name)
      const image = await screenshotWhenStable(page, { fullPage: true })
      await writeFile(outPath, image)

      if (failures.length > 0) {
        throw new Error(`Console/page errors while capturing ${shot.name}:\n${failures.join('\n')}`)
      }

      console.log(`wrote ${outPath}`)
      console.log(`      ${shot.description}`)
      await page.close()
    }
  } finally {
    await browser.close()
    await server.close()
  }
}

main().catch((err) => {
  console.error(err)
  process.exitCode = 1
})
