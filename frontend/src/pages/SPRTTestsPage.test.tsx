import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { SPRTTestsPage } from './SPRTTestsPage'

describe('SPRTTestsPage', () => {
  it('renders the heading', () => {
    render(<SPRTTestsPage />)
    expect(screen.getByRole('heading', { name: 'SPRT Tests' })).toBeInTheDocument()
  })
})
