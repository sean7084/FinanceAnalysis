import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { cacheSnapshot, cachedGet, subscribeToCache, type CachedGetOptions } from '../lib/queryCache'

export interface ApiResource<T> {
  /** Last known value, retained while a background revalidation is running. */
  data: T | null
  /** True only until the first value for this key arrives. */
  loading: boolean
  /** True while a revalidation runs on top of an existing value. */
  isRefreshing: boolean
  /** Non-null only when nothing could be loaded; pages substitute their own localised copy. */
  error: string | null
  refresh: () => void
}

/**
 * Fetch one API resource through the shared query cache.
 *
 * Replaces the per-page `let alive = true` / `setLoading` / `setError` block,
 * and separates "we have nothing yet" from "we have something and are fetching
 * a newer copy", so a page can render the part of itself that is ready instead
 * of hiding everything behind one spinner.
 *
 * `key` identifies the cache entry and must encode the query, so changing a
 * filter is changing the key. `deps` carries values the loader closes over that
 * the key does not already describe.
 */
export function useApiResource<T>(
  key: string,
  loader: () => Promise<T>,
  deps: readonly unknown[] = [],
  options: CachedGetOptions = {},
): ApiResource<T> {
  const loaderRef = useRef(loader)
  const optionsRef = useRef(options)

  // Declared before the fetch effect so the effect always sees the current
  // closure, without writing a ref during render.
  useEffect(() => {
    loaderRef.current = loader
    optionsRef.current = options
  })

  const depSignature = useMemo(() => JSON.stringify(deps), [deps])
  const initial = useMemo(() => cacheSnapshot<T>(key), [key])

  const [data, setData] = useState<T | null>(initial.value)
  const [loading, setLoading] = useState(!initial.hasValue)
  const [error, setError] = useState<string | null>(null)
  const [revalidating, setRevalidating] = useState(initial.revalidating)

  useEffect(() => {
    let alive = true

    const publish = () => {
      if (!alive) return
      const snapshot = cacheSnapshot<T>(key)
      if (snapshot.hasValue) {
        setData(snapshot.value)
        setLoading(false)
      }
      setRevalidating(snapshot.revalidating)
      setError(snapshot.hasValue || !snapshot.error ? null : 'request failed')
    }

    const unsubscribe = subscribeToCache(key, publish)

    void cachedGet<T>(key, () => loaderRef.current(), { ...optionsRef.current })
      .then((value) => {
        if (!alive) return
        setData(value)
        setLoading(false)
        setError(null)
      })
      .catch(() => {
        // Only reachable when the cache holds no value at all for this key.
        if (alive && !cacheSnapshot<T>(key).hasValue) {
          setError('request failed')
        }
      })

    publish()

    return () => {
      alive = false
      unsubscribe()
    }
  }, [key, depSignature])

  const refresh = useCallback(() => {
    void cachedGet<T>(key, () => loaderRef.current(), { ...optionsRef.current, force: true })
      .then((value) => {
        setData(value)
        setLoading(false)
        setError(null)
      })
      .catch(() => {
        if (!cacheSnapshot<T>(key).hasValue) {
          setError('request failed')
        }
      })
  }, [key])

  return { data, loading, isRefreshing: revalidating, error, refresh }
}
