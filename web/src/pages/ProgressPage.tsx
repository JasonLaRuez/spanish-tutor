import { useEffect, useState } from 'react'
import { api, type Progress } from '../api/client'
import { BandChart } from '../components/charts/BandChart'
import { GrowthChart } from '../components/charts/GrowthChart'
import { StatTiles } from '../components/StatTiles'
import { posName } from '../lib/text'

export function ProgressPage() {
  const [progress, setProgress] = useState<Progress | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.progress().then(setProgress).catch((err) => setError(String(err)))
  }, [])

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-6 py-10">
      <div>
        <h1 className="text-2xl font-semibold text-ink">Progress</h1>
        <p className="mt-1 text-ink-2">
          Vocabulary only: how much of everyday Spanish you can follow and use.
        </p>
      </div>
      {error && <p className="text-danger">{error}</p>}
      {progress && (
        <>
          <StatTiles progress={progress} />
          <BandChart bands={progress.bands} />
          <GrowthChart growth={progress.growth} />
          <section className="rounded-xl border border-line bg-surface p-5">
            <h2 className="font-semibold text-ink">Try using these</h2>
            <p className="text-sm text-ink-2">
              Words you recognize but haven’t used yet, most common first.
            </p>
            <ul className="mt-4 grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
              {progress.try_using.map((word) => (
                <li key={`${word.lemma}|${word.pos}`} className="text-sm">
                  <span className="es font-medium text-ink">{word.lemma}</span>{' '}
                  <span className="text-xs text-muted">{posName(word.pos)}</span>
                  <span className="block truncate text-ink-2" title={word.definition_en ?? ''}>
                    {word.definition_en ?? ''}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  )
}
