import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { PromotionDialog } from './PromotionDialog'

describe('PromotionDialog', () => {
  it('renders four promotion piece buttons', () => {
    render(<PromotionDialog color="white" onSelect={vi.fn()} onCancel={vi.fn()} />)

    expect(screen.getByRole('dialog', { name: 'Choose promotion piece' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Queen' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rook' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Bishop' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Knight' })).toBeInTheDocument()
  })

  it('shows white piece symbols for white color', () => {
    render(<PromotionDialog color="white" onSelect={vi.fn()} onCancel={vi.fn()} />)

    expect(screen.getByRole('button', { name: 'Queen' })).toHaveTextContent('♕')
    expect(screen.getByRole('button', { name: 'Rook' })).toHaveTextContent('♖')
    expect(screen.getByRole('button', { name: 'Bishop' })).toHaveTextContent('♗')
    expect(screen.getByRole('button', { name: 'Knight' })).toHaveTextContent('♘')
  })

  it('shows black piece symbols for black color', () => {
    render(<PromotionDialog color="black" onSelect={vi.fn()} onCancel={vi.fn()} />)

    expect(screen.getByRole('button', { name: 'Queen' })).toHaveTextContent('♛')
    expect(screen.getByRole('button', { name: 'Rook' })).toHaveTextContent('♜')
    expect(screen.getByRole('button', { name: 'Bishop' })).toHaveTextContent('♝')
    expect(screen.getByRole('button', { name: 'Knight' })).toHaveTextContent('♞')
  })

  it('calls onSelect with "q" when queen is clicked', async () => {
    const onSelect = vi.fn()
    const user = userEvent.setup()
    render(<PromotionDialog color="white" onSelect={onSelect} onCancel={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: 'Queen' }))
    expect(onSelect).toHaveBeenCalledWith('q')
  })

  it('calls onSelect with "r" when rook is clicked', async () => {
    const onSelect = vi.fn()
    const user = userEvent.setup()
    render(<PromotionDialog color="white" onSelect={onSelect} onCancel={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: 'Rook' }))
    expect(onSelect).toHaveBeenCalledWith('r')
  })

  it('calls onSelect with "b" when bishop is clicked', async () => {
    const onSelect = vi.fn()
    const user = userEvent.setup()
    render(<PromotionDialog color="white" onSelect={onSelect} onCancel={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: 'Bishop' }))
    expect(onSelect).toHaveBeenCalledWith('b')
  })

  it('calls onSelect with "n" when knight is clicked', async () => {
    const onSelect = vi.fn()
    const user = userEvent.setup()
    render(<PromotionDialog color="white" onSelect={onSelect} onCancel={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: 'Knight' }))
    expect(onSelect).toHaveBeenCalledWith('n')
  })

  it('calls onCancel when backdrop is clicked', async () => {
    const onCancel = vi.fn()
    const user = userEvent.setup()
    render(<PromotionDialog color="white" onSelect={vi.fn()} onCancel={onCancel} />)

    await user.click(screen.getByTestId('promotion-backdrop'))
    expect(onCancel).toHaveBeenCalled()
  })

  it('does not call onCancel when dialog content is clicked', async () => {
    const onCancel = vi.fn()
    const user = userEvent.setup()
    render(<PromotionDialog color="white" onSelect={vi.fn()} onCancel={onCancel} />)

    await user.click(screen.getByRole('dialog'))
    expect(onCancel).not.toHaveBeenCalled()
  })

  it('calls onCancel when Escape key is pressed', async () => {
    const onCancel = vi.fn()
    const user = userEvent.setup()
    render(<PromotionDialog color="white" onSelect={vi.fn()} onCancel={onCancel} />)

    await user.keyboard('{Escape}')
    expect(onCancel).toHaveBeenCalled()
  })
})
