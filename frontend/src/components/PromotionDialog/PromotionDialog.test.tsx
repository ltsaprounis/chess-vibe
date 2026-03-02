import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { PromotionDialog } from './PromotionDialog'

describe('PromotionDialog', () => {
  it('renders all four promotion options', () => {
    render(<PromotionDialog color="white" onSelect={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.getByRole('dialog', { name: /promotion/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Queen' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rook' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Bishop' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Knight' })).toBeInTheDocument()
  })

  it('shows white pieces when color is white', () => {
    render(<PromotionDialog color="white" onSelect={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Queen' })).toHaveTextContent('♕')
  })

  it('shows black pieces when color is black', () => {
    render(<PromotionDialog color="black" onSelect={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Queen' })).toHaveTextContent('♛')
  })

  it('calls onSelect with correct piece when clicked', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<PromotionDialog color="white" onSelect={onSelect} onCancel={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: 'Knight' }))
    expect(onSelect).toHaveBeenCalledWith('n')
  })

  it('calls onCancel when backdrop is clicked', async () => {
    const user = userEvent.setup()
    const onCancel = vi.fn()
    render(<PromotionDialog color="white" onSelect={vi.fn()} onCancel={onCancel} />)

    await user.click(screen.getByRole('dialog'))
    expect(onCancel).toHaveBeenCalled()
  })

  it('does not call onCancel when dialog content is clicked', async () => {
    const user = userEvent.setup()
    const onCancel = vi.fn()
    render(<PromotionDialog color="white" onSelect={vi.fn()} onCancel={onCancel} />)

    await user.click(screen.getByRole('button', { name: 'Queen' }))
    expect(onCancel).not.toHaveBeenCalled()
  })
})
