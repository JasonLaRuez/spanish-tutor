import type { Lesson } from '../api/client'
import { posName } from '../lib/text'
import { OtherSenses } from './Senses'
import { SpeakButton } from './SpeakButton'

/** A taught word: its definition (and any labeled rare, regional, slang or vulgar senses)
 * and a readable example, with Tatoeba's author credit.
 * Words no dictionary has carry a "model-written" label (their definition is Claude's);
 * known words shown before a conversation to practice using carry a "practice" label. */
export function LessonCard({ lesson }: { lesson: Lesson }) {
  const example = lesson.example
  const tatoebaId = example?.source?.startsWith('tatoeba:') ? example.source.slice(8) : null
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2.5">
      <div className="flex items-baseline gap-2">
        <span className="es font-semibold text-ink">{lesson.lemma}</span>
        <SpeakButton text={lesson.lemma} label={`Listen: ${lesson.lemma}`} />
        <span className="text-xs text-muted">{posName(lesson.pos)}</span>
        {lesson.model_written && (
          <span
            className="rounded border border-line px-1 text-[0.65rem] uppercase tracking-wide text-muted"
            title="This word isn't in the dictionaries: Claude wrote its definition and translated the example."
          >
            model-written
          </span>
        )}
        {lesson.practice && (
          <span
            className="rounded border border-line px-1 text-[0.65rem] uppercase tracking-wide text-muted"
            title="You already understand this word but haven't used it yet: try it in a reply."
          >
            practice
          </span>
        )}
      </div>
      <p className="mt-0.5 text-sm text-ink-2">{lesson.definition_en ?? '(no definition)'}</p>
      <OtherSenses senses={lesson.other_senses} />
      {example && (
        <div className="mt-2 border-l-2 border-line pl-2.5">
          <p className="es flex items-start gap-1 text-ink">
            <span className="min-w-0 flex-1">{example.es}</span>
            <SpeakButton text={example.es} label="Listen to the example" />
          </p>
          {example.en && <p className="text-sm text-ink-2">{example.en}</p>}
          {example.glosses.length > 0 && (
            <p className="mt-1 text-xs text-muted">
              {example.glosses.map(([word, gloss]) => `${word}: ${gloss}`).join(' · ')}
            </p>
          )}
          {tatoebaId && (
            <p className="mt-1 text-xs text-muted">
              <a
                className="underline decoration-line underline-offset-2 hover:text-accent-text"
                href={`https://tatoeba.org/en/sentences/show/${tatoebaId}`}
                target="_blank"
                rel="noreferrer"
              >
                Tatoeba #{tatoebaId}
              </a>
              {example.author ? `, ${example.author}` : ''}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

export function LessonList({ lessons, title }: { lessons: Lesson[]; title: string }) {
  return (
    <section aria-label={title} className="space-y-2">
      <h3 className="text-xs font-medium uppercase tracking-wide text-muted">{title}</h3>
      <div className="grid gap-2 sm:grid-cols-2">
        {lessons.map((lesson) => (
          <LessonCard key={lesson.lexeme_id} lesson={lesson} />
        ))}
      </div>
    </section>
  )
}
