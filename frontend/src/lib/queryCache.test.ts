import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  cacheSnapshot,
  cachedGet,
  invalidate,
  resetQueryCache,
  seed,
  subscribeToCache,
} from './queryCache'

describe('queryCache', () => {
  beforeEach(() => {
    resetQueryCache()
  })

  it('shares one request between concurrent callers of the same key', async () => {
    const loader = vi.fn(() => new Promise<number>((resolve) => setTimeout(() => resolve(7), 5)))

    const [first, second] = await Promise.all([cachedGet('k', loader), cachedGet('k', loader)])

    expect(loader).toHaveBeenCalledTimes(1)
    expect(first).toBe(7)
    expect(second).toBe(7)
  })

  it('serves a hit inside the TTL without touching the network', async () => {
    const loader = vi.fn(async () => 'a')

    await cachedGet('k', loader, { ttlMs: 60_000 })
    const again = await cachedGet('k', loader, { ttlMs: 60_000 })

    expect(loader).toHaveBeenCalledTimes(1)
    expect(again).toBe('a')
  })

  it('returns the cached value immediately and revalidates in the background', async () => {
    let current = 'first'
    const loader = vi.fn(async () => current)

    await cachedGet('k', loader, { ttlMs: 0, staleMs: 60_000 })
    current = 'second'
    const subscribers = vi.fn()
    subscribeToCache('k', subscribers)

    const staleHit = await cachedGet('k', loader, { ttlMs: 0, staleMs: 60_000 })

    // The caller gets the old value straight away; the newer copy arrives through
    // the subscription once the background request settles.
    expect(staleHit).toBe('first')
    await vi.waitFor(() => {
      expect(cacheSnapshot<string>('k').value).toBe('second')
    })
    expect(subscribers).toHaveBeenCalled()
    expect(loader).toHaveBeenCalledTimes(2)
  })

  it('keeps the stale value instead of throwing when the revalidation fails', async () => {
    let shouldFail = false
    const loader = vi.fn(async () => {
      if (shouldFail) throw new Error('boom')
      return 'value'
    })

    await cachedGet('k', loader, { ttlMs: 0, staleMs: 60_000 })
    shouldFail = true

    await expect(cachedGet('k', loader, { ttlMs: 0, staleMs: 60_000 })).resolves.toBe('value')
    await vi.waitFor(() => {
      expect(cacheSnapshot('k').error).toBeTruthy()
    })
    expect(cacheSnapshot('k').hasValue).toBe(true)
  })

  it('rejects only when a fetch fails and nothing is cached', async () => {
    const loader = vi.fn(async () => {
      throw new Error('no data')
    })

    await expect(cachedGet('k', loader)).rejects.toThrow('no data')
  })

  it('re-fetches past the stale window', async () => {
    const loader = vi.fn(async () => 1)

    await cachedGet('k', loader, { ttlMs: 0, staleMs: 0 })
    await cachedGet('k', loader, { ttlMs: 0, staleMs: 0 })

    // staleMs 0 pins the window open, so both calls are blocking fetches rather
    // than a stale hit plus a background revalidation.
    expect(loader).toHaveBeenCalledTimes(2)
  })

  it('bypasses the TTL when a caller forces a refresh', async () => {
    const loader = vi.fn(async () => 1)

    await cachedGet('k', loader, { ttlMs: 60_000 })
    await cachedGet('k', loader, { ttlMs: 60_000, force: true })

    expect(loader).toHaveBeenCalledTimes(2)
  })

  it('seeds a key so a wider response can prime a narrower one', async () => {
    const loader = vi.fn(async () => 'not called')
    seed('k', 'seeded')

    const value = await cachedGet('k', loader, { ttlMs: 60_000 })

    expect(value).toBe('seeded')
    expect(loader).not.toHaveBeenCalled()
  })

  it('invalidate drops only the keys under a prefix', async () => {
    await cachedGet('dashboard/summary', async () => 1)
    await cachedGet('dashboard/stocks?7/120/', async () => 2)
    await cachedGet('macro/contexts?limit=30', async () => 3)

    invalidate('dashboard/')

    expect(cacheSnapshot('dashboard/summary').hasValue).toBe(false)
    expect(cacheSnapshot('dashboard/stocks?7/120/').hasValue).toBe(false)
    expect(cacheSnapshot('macro/contexts?limit=30').hasValue).toBe(true)
  })

  it('invalidate without a prefix clears every cached resource', async () => {
    await cachedGet('dashboard/summary', async () => 1)
    await cachedGet('alerts/history?limit=20', async () => 2)

    invalidate()

    expect(cacheSnapshot('dashboard/summary').hasValue).toBe(false)
    expect(cacheSnapshot('alerts/history?limit=20').hasValue).toBe(false)
  })
})
