/**
 * Game state management hook powered by chess.js.
 *
 * Encapsulates position tracking, move validation, move history,
 * and game-over detection. Designed for the Play page — all state
 * lives in React; no external store is required.
 */

import { useCallback, useState } from 'react'
import { Chess, type Square } from 'chess.js'

export interface MoveRecord {
  san: string
  uci: string
  scoreCp?: number
  scoreMate?: number
  depth?: number
  pv?: string[]
}

export interface EvalData {
  scoreCp?: number
  scoreMate?: number
  depth?: number
  pv?: string[]
}

export interface LegalMove {
  to: string
  isCapture: boolean
}

export interface UseChessGameOptions {
  startFen?: string
}

export interface UseChessGameReturn {
  fen: string
  moves: MoveRecord[]
  isGameOver: boolean
  result: string | null
  turn: 'w' | 'b'
  makeMove: (uci: string) => boolean
  addEngineMove: (uci: string, evalData?: EvalData) => boolean
  reset: (fen?: string) => void
  getLegalMoves: (square: string) => LegalMove[]
  isPromotionMove: (from: string, to: string) => boolean
  pgn: string
}

function getGameResult(chess: Chess): string | null {
  if (!chess.isGameOver()) return null
  if (chess.isCheckmate()) {
    return chess.turn() === 'b' ? '1-0' : '0-1'
  }
  return '1/2-1/2'
}

function buildEvalFields(evalData?: EvalData): Partial<MoveRecord> {
  if (!evalData) return {}
  const fields: Partial<MoveRecord> = {}
  if (evalData.scoreCp !== undefined) fields.scoreCp = evalData.scoreCp
  if (evalData.scoreMate !== undefined) fields.scoreMate = evalData.scoreMate
  if (evalData.depth !== undefined) fields.depth = evalData.depth
  if (evalData.pv !== undefined) fields.pv = evalData.pv
  return fields
}

export function useChessGame(options?: UseChessGameOptions): UseChessGameReturn {
  const startFen = options?.startFen

  // Hold the Chess instance in useState (initialiser runs once).
  // We mutate it in callbacks and track derived state separately.
  const [chess] = useState(() => new Chess(startFen))

  const [fen, setFen] = useState(() => chess.fen())
  const [moves, setMoves] = useState<MoveRecord[]>([])
  const [isGameOver, setIsGameOver] = useState(() => chess.isGameOver())
  const [result, setResult] = useState<string | null>(() => getGameResult(chess))
  const [turn, setTurn] = useState<'w' | 'b'>(() => chess.turn())
  const [pgn, setPgn] = useState(() => chess.pgn())

  const syncState = useCallback(() => {
    setFen(chess.fen())
    setTurn(chess.turn())
    setIsGameOver(chess.isGameOver())
    setResult(getGameResult(chess))
    setPgn(chess.pgn())
  }, [chess])

  const applyMove = useCallback(
    (uci: string, evalData?: EvalData): boolean => {
      const from = uci.slice(0, 2)
      const to = uci.slice(2, 4)
      const promotion = uci.length > 4 ? uci[4] : undefined

      try {
        const moveResult = chess.move({ from, to, promotion })
        if (!moveResult) return false

        const record: MoveRecord = {
          san: moveResult.san,
          uci,
          ...buildEvalFields(evalData),
        }

        setMoves((prev) => [...prev, record])
        syncState()
        return true
      } catch {
        return false
      }
    },
    [chess, syncState],
  )

  const makeMove = useCallback(
    (uci: string): boolean => {
      return applyMove(uci)
    },
    [applyMove],
  )

  const addEngineMove = useCallback(
    (uci: string, evalData?: EvalData): boolean => {
      return applyMove(uci, evalData)
    },
    [applyMove],
  )

  const getLegalMoves = useCallback(
    (square: string): LegalMove[] => {
      try {
        const verboseMoves = chess.moves({
          square: square as Square,
          verbose: true,
        })
        return verboseMoves.map((m) => ({
          to: m.to,
          isCapture: m.isCapture(),
        }))
      } catch {
        // Invalid square strings intentionally return empty — no legal moves
        return []
      }
    },
    [chess],
  )

  const isPromotionMove = useCallback(
    (from: string, to: string): boolean => {
      try {
        const verboseMoves = chess.moves({
          square: from as Square,
          verbose: true,
        })
        return verboseMoves.some((m) => m.to === to && m.promotion)
      } catch {
        return false
      }
    },
    [chess],
  )

  const reset = useCallback(
    (fen?: string) => {
      if (fen) {
        chess.load(fen)
      } else if (startFen) {
        chess.load(startFen)
      } else {
        chess.reset()
      }
      setMoves([])
      syncState()
    },
    [chess, startFen, syncState],
  )

  return {
    fen,
    moves,
    isGameOver,
    result,
    turn,
    makeMove,
    addEngineMove,
    reset,
    getLegalMoves,
    isPromotionMove,
    pgn,
  }
}
