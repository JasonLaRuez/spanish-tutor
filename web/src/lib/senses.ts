import type { components } from '../api/schema'

export type Sense = components['schemas']['SenseOut']
export type Register = Sense['register']

/** The registers in the order they're offered: the ones most worth knowing first. */
export const REGISTERS: Register[] = ['vulgar', 'slang', 'regional', 'rare', 'technical', 'archaic']

/** What each register label means, shown on hover. */
export const REGISTER_HINTS: Record<Register, string> = {
  vulgar: 'Vulgar or offensive: good to recognize, best not to use.',
  slang: 'Slang or very informal.',
  regional: 'Used only in some countries or regions.',
  rare: 'Real, but uncommon.',
  technical: 'A specialist sense (law, science, a trade…).',
  archaic: 'Old-fashioned; met mostly in older texts.',
}
