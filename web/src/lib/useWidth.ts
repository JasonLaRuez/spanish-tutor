import { useEffect, useRef, useState } from 'react'

/** The width of a container, kept current as it resizes (charts draw at real pixel size,
 *  so text never scales with the chart). */
export function useWidth<T extends HTMLElement>(fallback = 640) {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(fallback)
  useEffect(() => {
    const element = ref.current
    if (!element) return
    setWidth(element.clientWidth || fallback)
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width || fallback))
    observer.observe(element)
    return () => observer.disconnect()
  }, [fallback])
  return [ref, width] as const
}
