import { Library } from '../components/Library'

/** The library of songs and poems: songs under each of their artists (a duet appears under
 *  both), poems by collection; fewest new words first, each marked if it's over the
 *  difficulty ceiling. Opening one starts the lyrics skill (study, try, compare). */
export function Songs() {
  return (
    <Library
      kinds={['song', 'poem']}
      title="Songs & poems"
      intro="Translate a few lines yourself, then compare natural and literal translations. Fewest new words first."
      loose="Songs"
      kindLabel={(item) => (item.kind === 'poem' ? 'poem' : 'song')}
      groupsOf={(item) => (item.kind === 'song' && item.author ? item.author.split(', ') : null)}
      empty={
        <>
          No songs or poems yet. Add your own with <code>content add-song</code>, or a poetry
          collection with <code>ingest.gutenberg</code> and <code>content add-poems</code>.
        </>
      }
    />
  )
}
