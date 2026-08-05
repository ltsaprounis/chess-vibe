/**
 * TypeScript type definitions mirroring the backend's Pydantic response models.
 */

export interface TimeControl {
  type: string
  movetime_ms: number | null
  wtime_ms: number | null
  btime_ms: number | null
  winc_ms: number | null
  binc_ms: number | null
  moves_to_go: number | null
  depth: number | null
  nodes: number | null
}

export interface Move {
  uci: string
  san: string
  fen_after: string
  score_cp: number | null
  score_mate: number | null
  depth: number | null
  seldepth: number | null
  pv: string[]
  nodes: number | null
  time_ms: number | null
  clock_white_ms: number | null
  clock_black_ms: number | null
}

export interface GameDetail {
  id: string
  white_engine: string
  black_engine: string
  result: string
  moves: Move[]
  created_at: string
  opening_name: string | null
  sprt_test_id: string | null
  start_fen: string | null
  time_control: TimeControl | null
}

export interface GameSummary {
  id: string
  white_engine: string
  black_engine: string
  result: string
  move_count: number
  created_at: string
  opening_name: string | null
  sprt_test_id: string | null
}

export interface SPRTTestCreateRequest {
  engine_a: string
  engine_b: string
  time_control: string
  elo0?: number
  elo1?: number
  alpha?: number
  beta?: number
  book_id?: string | null
  concurrency?: number
}

export interface SPRTTestCreated {
  id: string
  status: string
}

export interface SPRTTest {
  id: string
  engine_a: string
  engine_b: string
  time_control: TimeControl
  elo0: number
  elo1: number
  alpha: number
  beta: number
  created_at: string
  status: string
  wins: number
  losses: number
  draws: number
  llr: number
  result: string | null
  completed_at: string | null
}

export interface Engine {
  id: string
  name: string
}

export interface OpeningBook {
  id: string
  name: string
  format: string
}

export interface GameFilters {
  sprt_test_id?: string
  engine_id?: string
  result?: string
  opening_name?: string
}
