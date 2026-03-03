import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { App } from './App'

vi.mock('./services/api', () => ({
  fetchEngines: vi.fn().mockResolvedValue([]),
}))

describe('App', () => {
  it('renders the Play page by default', async () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    )
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Play' })).toBeInTheDocument()
    })
  })
})

describe('Routing', () => {
  it('renders Play page at /', async () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    )
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Play' })).toBeInTheDocument()
    })
  })

  it('renders SPRT Tests page at /sprt', () => {
    render(
      <MemoryRouter initialEntries={['/sprt']}>
        <App />
      </MemoryRouter>,
    )
    expect(screen.getByRole('heading', { name: 'SPRT Tests' })).toBeInTheDocument()
  })

  it('renders Game Replay page at /games', () => {
    render(
      <MemoryRouter initialEntries={['/games']}>
        <App />
      </MemoryRouter>,
    )
    expect(screen.getByRole('heading', { name: 'Game Replay' })).toBeInTheDocument()
  })

  it('renders Game Replay page at /games/:id', () => {
    render(
      <MemoryRouter initialEntries={['/games/abc123']}>
        <App />
      </MemoryRouter>,
    )
    expect(screen.getByRole('heading', { name: 'Game Replay' })).toBeInTheDocument()
  })
})
