/**
 * React context for the active chess game session.
 *
 * Wraps the Play page so that nested components (board, move list,
 * eval bar) can access game state without prop drilling.
 */

import { createContext, useContext } from 'react'
import { useChessGame } from '../hooks/useChessGame'
import type { UseChessGameReturn } from '../hooks/useChessGame'

const GameContext = createContext<UseChessGameReturn | null>(null)

export interface GameProviderProps {
  startFen?: string
  children: React.ReactNode
}

export function GameProvider({ startFen, children }: GameProviderProps): React.JSX.Element {
  const game = useChessGame({ startFen })

  return <GameContext.Provider value={game}>{children}</GameContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useGameContext(): UseChessGameReturn {
  const ctx = useContext(GameContext)
  if (ctx === null) {
    throw new Error('useGameContext must be used within a GameProvider')
  }
  return ctx
}
