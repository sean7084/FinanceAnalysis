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
 * Time-to-data for the page currently on screen.
 *
 * A view is one pathname plus the resource keys mounted under it. It opens when
 * the first resource mounts and is stamped once no key is still waiting for its
 * first value. Nothing renders from any of this, so it is all unobserved, which
 * is why three attributes rather than one:
 *
 *   data-tti-open  milliseconds from navigationStart to the view opening, so the
 *                  bundle, route chunk and pre-fetch render,
 *   data-tti       milliseconds from the view opening to its last first value,
 *                  which therefore *includes* the API wait,
 *   data-tti-path  the route these describe.
 *
 * scripts/measure_page_api_latency.sh charges FRONTEND_OVERHEAD_SECONDS from
 * `data-tti-open` plus the part of `data-tti` that is not the request itself;
 * reading only `data-tti` would bill the same round trip twice. A test without a
 * document, or an engine whose UserTiming throws, loses the mark but never the
 * attributes.
 */
interface TimingView {
  pathname: string
  startedAt: number
  pending: Set<string>
  stamped: boolean
}

const NO_VIEW: TimingView = { pathname: '', startedAt: 0, pending: new Set(), stamped: true }

let view: TimingView = NO_VIEW

function stampView(): void {
  if (view.stamped || view.pending.size > 0) return
  view.stamped = true
  const duration = performance.now() - view.startedAt
  // The attributes are the record and the mark is the convenience: write them
  // first, in their own try, because an environment whose UserTiming is missing
  // (jsdom) or rejects the options form must not take the numbers the latency
  // harness reads down with it.
  try {
    document.body.dataset.ttiOpen = view.startedAt.toFixed(0)
    document.body.dataset.tti = duration.toFixed(0)
    document.body.dataset.ttiPath = view.pathname
  } catch {
    // Diagnostic only; a missing attribute is not worth failing a render over.
  }
  try {
    if (typeof performance.measure === 'function') {
      performance.measure('page-data-ready', {
        start: view.startedAt,
        end: duration,
        detail: view.pathname,
      })
    }
  } catch {
    // Some engines throw on an unsupported measure form rather than ignoring it.
  }
}

/** Register `key` as awaiting its first value, and return its release. */
function trackResource(pathname: string, key: string): () => void {
  if (typeof document === 'undefined' || typeof performance === 'undefined') {
    return () => undefined
  }
  if (view.pathname !== pathname) {
    view = { pathname, startedAt: performance.now(), pending: new Set(), stamped: false }
  }
  view.pending.add(key)
  let released = false
  return () => {
    if (released) return
    released = true
    view.pending.delete(key)
    stampView()
  }
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
    const release = trackResource(
      typeof window === 'undefined' ? '' : window.location.pathname,
      key,
    )

    const publish = () => {
      if (!alive) return
      const snapshot = cacheSnapshot<T>(key)
      if (snapshot.hasValue) {
        setData(snapshot.value)
        setLoading(false)
        release()
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
        release()
      })
      .catch(() => {
        // Only reachable when the cache holds no value at all for this key.
        // Release regardless: a page that failed to load data is not waiting for
        // it, and leaving the key pending would stall the stamp forever.
        release()
        if (alive && !cacheSnapshot<T>(key).hasValue) {
          setError('request failed')
        }
      })

    publish()

    return () => {
      alive = false
      unsubscribe()
      release()
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
