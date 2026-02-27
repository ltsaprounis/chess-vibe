import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { App } from './App'

describe('App', () => {
  it('renders the Play page by default', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    )
    expect(screen.getByRole('heading', { name: 'Play' })).toBeInTheDocument()
  })
})

describe('Routing', () => {
  it('renders Play page at /', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    )
    expect(screen.getByRole('heading', { name: 'Play' })).toBeInTheDocument()
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
