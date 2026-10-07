import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { EvalSummary } from '../api/client'
import { EvalPanel } from '../components/EvalPanel'

const rate = (k: number, n: number, low = 0.87, high = 1) => ({ k, n, value: n ? k / n : null, low: n ? low : null, high: n ? high : null })

const summary = (): EvalSummary => ({
  replies: 25,
  within_limit: rate(25, 25),
  first_draft_within_limit: rate(24, 25),
  complete: rate(5, 5, 0.57, 1),
  studied_before_finishing: rate(0, 0),
  new_word_precision: rate(8, 13, 0.36, 0.82),
})

describe('The evaluation panel', () => {
  it('shows each rate with what it rests on and its interval', () => {
    render(<EvalPanel summary={summary()} />)
    const panel = screen.getByRole('region', { name: 'How the tutor is doing' })
    expect(panel).toHaveTextContent('Replies within your vocabulary100%25 of 25 · 95% CI 87%–100%')
    expect(panel).toHaveTextContent('First drafts: 96%')
    expect(panel).toHaveTextContent('Words taught that were really new62%8 of 13 · 95% CI 36%–82%')
  })

  it('says when there is no data yet instead of showing 0%', () => {
    render(<EvalPanel summary={summary()} />)
    const tile = screen.getByText('New words studied before finishing').parentElement!
    expect(tile).toHaveTextContent('–No data yet')
  })
})
