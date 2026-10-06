import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { api, type Progress } from '../api/client'
import { StatTiles } from '../components/StatTiles'

const SKILLS = [
  {
    name: 'Conversation',
    description: 'Chat about a topic, inside the words you know. New words are taught as they come up.',
    to: '/new',
  },
  {
    name: 'What next?',
    description: 'The next song, story or chapter that needs the fewest new words.',
    to: '/next',
  },
  {
    name: 'Songs',
    description: 'Natural and literal translations side by side, with the idioms explained.',
    phase: 'Phase 4',
  },
  {
    name: 'Books',
    description: 'Stories and books chapter by chapter, in order, at your level.',
    phase: 'Phase 4',
  },
]

export function Home() {
  const [progress, setProgress] = useState<Progress | null>(null)

  useEffect(() => {
    api.progress().then(setProgress).catch(() => {})
  }, [])

  return (
    <div className="mx-auto max-w-5xl space-y-8 px-6 py-10">
      <div>
        <h1 className="text-3xl font-semibold text-ink">¡Hola!</h1>
        <p className="mt-1 text-ink-2">What would you like to practice?</p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {SKILLS.map((skill) =>
          skill.to ? (
            <Link
              key={skill.name}
              to={skill.to}
              className="group rounded-xl border border-line bg-surface p-5 hover:border-accent"
            >
              <h2 className="font-semibold text-ink group-hover:text-accent-text">{skill.name} →</h2>
              <p className="mt-1 text-sm text-ink-2">{skill.description}</p>
            </Link>
          ) : (
            <div key={skill.name} className="rounded-xl border border-dashed border-line p-5" aria-disabled="true">
              <h2 className="flex items-center justify-between font-semibold text-ink-2">
                {skill.name}
                <span className="text-xs font-normal text-muted">{skill.phase}</span>
              </h2>
              <p className="mt-1 text-sm text-muted">{skill.description}</p>
            </div>
          ),
        )}
      </div>

      {progress && (
        <section className="space-y-3">
          <div className="flex items-baseline justify-between">
            <h2 className="font-semibold text-ink">Your vocabulary</h2>
            <Link to="/progress" className="text-sm text-accent-text hover:underline">
              Details →
            </Link>
          </div>
          <StatTiles progress={progress} />
        </section>
      )}
    </div>
  )
}
