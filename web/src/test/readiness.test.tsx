import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { Readiness } from '../api/client'
import { ReadinessRings } from '../components/charts/ReadinessRings'

const levels: Readiness[] = [
  { level: 'A1', words: 1000, recognized: 450, produced: 370, words_up_to: 1000, recognized_up_to: 450, produced_up_to: 370 },
  { level: 'A2', words: 1000, recognized: 150, produced: 90, words_up_to: 2000, recognized_up_to: 600, produced_up_to: 460 },
]

describe('The readiness rings', () => {
  it('draws one cumulative ring per level, recognize and can produce', () => {
    render(<ReadinessRings levels={levels} />)
    expect(screen.getByRole('img', { name: 'Up to A1: recognize 45%, can produce 37%' })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Up to A2: recognize 30%, can produce 23%' })).toBeInTheDocument()
  })

  it('never presents itself as a CEFR level', () => {
    render(<ReadinessRings levels={levels} />)
    expect(screen.getByText(/Vocabulary only, not a CEFR level/)).toBeInTheDocument()
  })

  it('has a table view with running totals and the words new at each level', () => {
    render(<ReadinessRings levels={levels} />)
    fireEvent.click(screen.getByRole('button', { name: 'table' }))
    const rows = screen.getAllByRole('row')
    expect(rows[2]).toHaveTextContent('A22,000600 (30%)460 (23%)1,000')
  })

  it('explains how to add levels when there are none', () => {
    render(<ReadinessRings levels={[]} />)
    expect(screen.getByText(/No words have levels yet/)).toHaveTextContent('spanish_tutor.ingest.elelex')
  })
})
