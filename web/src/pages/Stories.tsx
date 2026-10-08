import { Library } from '../components/Library'

/** The library of stories (tales, folk tales, short readings), by collection: fewest new
 *  words first. Opening one starts the reading skill (study the new words, read, talk). */
export function Stories() {
  return (
    <Library
      kinds={['story']}
      title="Stories"
      intro="Short stories, folk tales and readings, each read on its own. Fewest new words first."
      loose="Single stories"
      empty={
        <>
          No stories yet. Add a collection with <code>ingest.gutenberg</code> and{' '}
          <code>content add-stories</code>, or one story with <code>content add-story</code>.
        </>
      }
    />
  )
}
