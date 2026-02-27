import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { Layout } from './Layout'

describe('Layout', () => {
  it('renders the navigation bar with all links', () => {
    render(
      <MemoryRouter>
        <Layout />
      </MemoryRouter>,
    )

    expect(screen.getByText('Chess Vibe')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Play' })).toHaveAttribute('href', '/')
    expect(screen.getByRole('link', { name: 'SPRT Tests' })).toHaveAttribute('href', '/sprt')
    expect(screen.getByRole('link', { name: 'Game Replay' })).toHaveAttribute('href', '/games')
  })
})
