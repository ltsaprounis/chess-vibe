import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { PlayPage } from './PlayPage'

describe('PlayPage', () => {
  it('renders the heading', () => {
    render(<PlayPage />)
    expect(screen.getByRole('heading', { name: 'Play' })).toBeInTheDocument()
  })
})
