/**
 * Small in-memory query cache shared by every page.
 *
 * The dashboard's expensive endpoint is already cached server-side, but a
 * second browser-side layer is what actually removes the round trip from the
 * navigation path: a remount inside the TTL renders instantly, and two
 * components that ask for the same resource share one request.
 *
 * Deliberately has no dependency on `api.ts` so `api.ts` can import it without
 * creating a cycle.
 */

const DEFAULT_TTL_MS = 60_000
const DEFAULT_STALE_MS = 5 * 60_000

/**
 * Entries are keyed by resource, and every key embeds its own query string, so
 * one bounded map is enough; pages never accumulate keys across filter changes
 * beyond the eviction window.
 */
const MAX_ENTRIES = 200

export interface CachedGetOptions {
  ttlMs?: number
  staleMs?: number
  /** Ignore the freshness window and always fetch. Used after a mutation. */
  force?: boolean
}

interface CacheEntry {
  /** Kept on the entry so an async completion can name what it resolved. */
  key: string
  value: unknown
  hasValue: boolean
  fetchedAt: number
  ttlMs: number
  staleMs: number
  inflight: Promise<unknown> | null
  error: unknown
  listeners: Set<() => void>
}

const store = new Map<string, CacheEntry>()

/** Where a resolved resource came from. Distinguishing these is the whole point
 * of the marks: a "page load" served from cache is not evidence about the
 * server, and a cold navigation that hits the network is. */
type ResolveSource = 'network' | 'cache' | 'deduped'

/**
 * Emit `performance` marks for one resolution.
 *
 * scripts/measure_page_api_latency.sh budgets the browser term of page load as
 * `FRONTEND_OVERHEAD_SECONDS`, and that constant has to come from somewhere other
 * than a guess. The marks say how long each resource took and whether it was
 * served from memory, which is what separates "the app is slow" from "the API is
 * slow". Marking is diagnostic only, so nothing here may throw into a render.
 */
function recordResolve(key: string, source: ResolveSource, startedAt: number): void {
  try {
    if (typeof performance === 'undefined' || typeof performance.mark !== 'function') return
    performance.mark(`api:${key}:${source}`)
    if (typeof performance.measure === 'function') {
      performance.measure(`api-duration:${key}`, {
        start: startedAt,
        end: performance.now(),
        detail: source,
      })
    }
  } catch {
    // Marking must never be the reason a page fails to load.
  }
}

function ensureEntry(key: string, options: CachedGetOptions): CacheEntry {
  const ttlMs = options.ttlMs ?? DEFAULT_TTL_MS
  const staleMs = Math.max(options.staleMs ?? DEFAULT_STALE_MS, ttlMs)

  let entry = store.get(key)
  if (!entry) {
    entry = {
      key,
      value: null,
      hasValue: false,
      fetchedAt: 0,
      ttlMs,
      staleMs,
      inflight: null,
      error: null,
      listeners: new Set(),
    }
    store.set(key, entry)
  } else {
    entry.ttlMs = ttlMs
    entry.staleMs = staleMs
  }
  return entry
}

function evictOldest(): void {
  if (store.size <= MAX_ENTRIES) return
  let oldestKey: string | null = null
  let oldestAt = Number.POSITIVE_INFINITY
  for (const [key, entry] of store) {
    if (entry.fetchedAt < oldestAt) {
      oldestAt = entry.fetchedAt
      oldestKey = key
    }
  }
  if (oldestKey !== null) {
    store.delete(oldestKey)
  }
}

function notify(entry: CacheEntry): void {
  for (const listener of [...entry.listeners]) {
    listener()
  }
}

function runFetch<T>(entry: CacheEntry, loader: () => Promise<T>): Promise<T> {
  if (entry.inflight) {
    return entry.inflight as Promise<T>
  }

  const startedAt = performance?.now?.() ?? 0
  const task = (async () => {
    try {
      const value = await loader()
      entry.value = value
      entry.hasValue = true
      entry.fetchedAt = Date.now()
      entry.error = null
      evictOldest()
      recordResolve(entry.key, 'network', startedAt)
      return value
    } catch (error) {
      entry.error = error
      // A previously loaded value is still better than an error state: the UI
      // keeps showing the last known good rows and reads as "updating".
      if (entry.hasValue) {
        return entry.value as T
      }
      throw error
    } finally {
      entry.inflight = null
      notify(entry)
    }
  })()

  entry.inflight = task
  notify(entry)
  return task
}

/**
 * Resolve `loader` through the cache for `key`.
 *
 * - fresh (inside `ttlMs`): the cached value, no request.
 * - stale but inside `staleMs`: the cached value immediately, with a background
 *   revalidation whose result is pushed to subscribers.
 * - older than `staleMs` (or no value): one request, deduplicated across
 *   concurrent callers.
 */
export async function cachedGet<T>(
  key: string,
  loader: () => Promise<T>,
  options: CachedGetOptions = {},
): Promise<T> {
  const startedAt = performance?.now?.() ?? 0
  const entry = ensureEntry(key, options)

  if (entry.inflight && !options.force) {
    recordResolve(key, 'deduped', startedAt)
    return entry.inflight as Promise<T>
  }

  if (!options.force && entry.hasValue) {
    const age = Date.now() - entry.fetchedAt
    if (age < entry.ttlMs) {
      recordResolve(key, 'cache', startedAt)
      return entry.value as T
    }
    if (age < entry.staleMs) {
      void runFetch(entry, loader).catch(() => undefined)
      recordResolve(key, 'cache', startedAt)
      return entry.value as T
    }
  }

  return runFetch(entry, loader)
}

/** Snapshot of a key's cache state, for rendering "updating" affordances. */
export interface CacheSnapshot<T = unknown> {
  hasValue: boolean
  value: T | null
  revalidating: boolean
  error: unknown
}

export function cacheSnapshot<T>(key: string): CacheSnapshot<T> {
  const entry = store.get(key)
  if (!entry) {
    return { hasValue: false, value: null, revalidating: false, error: null }
  }
  return {
    hasValue: entry.hasValue,
    value: (entry.hasValue ? entry.value : null) as T | null,
    revalidating: entry.inflight !== null,
    error: entry.error,
  }
}

/** Subscribe to cache-state changes for `key`. Returns an unsubscribe function. */
export function subscribeToCache(key: string, listener: () => void): () => void {
  const entry = ensureEntry(key, {})
  entry.listeners.add(listener)
  return () => {
    entry.listeners.delete(listener)
  }
}

/**
 * Drop cached entries. With a prefix, drops every key that starts with it;
 * without, drops everything.
 *
 * Nothing calls this in production right now: the only reason to force a drop
 * was signing out, and rows used to be owner-scoped. It stays because it is the
 * cache's eviction API and the primitive a "refresh" control would call.
 */
export function invalidate(prefix?: string): void {
  if (!prefix) {
    store.clear()
    return
  }
  for (const key of [...store.keys()]) {
    if (key.startsWith(prefix)) {
      store.delete(key)
    }
  }
}

/** Pre-populate a key, used when one endpoint's response contains another's. */
export function seed<T>(key: string, data: T): void {
  const entry = ensureEntry(key, {})
  entry.value = data
  entry.hasValue = true
  entry.fetchedAt = Date.now()
  entry.error = null
  notify(entry)
}

/** Test-only: forget everything the module has cached. */
export function resetQueryCache(): void {
  store.clear()
}
