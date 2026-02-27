import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { GameReplayPage } from './GameReplayPage'

describe('GameReplayPage', () => {
  it('renders the heading', () => {
    render(<GameReplayPage />)
    expect(screen.getByRole('heading', { name: 'Game Replay' })).toBeInTheDocument()
  })
})
