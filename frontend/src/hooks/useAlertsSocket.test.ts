import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'

import { useAlertsSocket } from './useAlertsSocket'

class FakeWebSocket {
  static instances: FakeWebSocket[] = []

  url: string
  closeCode: number | null = null
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  close() {
    this.closeCode = 1000
  }

  emitOpen() {
    this.onopen?.()
  }

  emitClose() {
    this.onclose?.()
  }
}

const latest = () => FakeWebSocket.instances[FakeWebSocket.instances.length - 1]

describe('useAlertsSocket', () => {
  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('opens the alerts socket with no credential in the URL', () => {
    const { result } = renderHook(() => useAlertsSocket())

    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(latest().url).toMatch(/\/ws\/alerts\/$/)
    expect(latest().url).not.toContain('?')

    act(() => latest().emitOpen())

    expect(result.current.connected).toBe(true)
    expect(result.current.reconnecting).toBe(false)
  })

  it('reconnects after a close, which the removed credential check used to suppress', () => {
    renderHook(() => useAlertsSocket())

    act(() => latest().emitClose())
    expect(FakeWebSocket.instances).toHaveLength(1)

    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(FakeWebSocket.instances).toHaveLength(2)

    act(() => latest().emitOpen())
    expect(FakeWebSocket.instances).toHaveLength(2)
  })

  it('caps the backoff and stops after MAX_RECONNECT_ATTEMPTS closes', () => {
    const { result } = renderHook(() => useAlertsSocket())

    for (const delay of [1000, 2000, 4000, 8000, 15000]) {
      act(() => latest().emitClose())
      act(() => {
        vi.advanceTimersByTime(delay)
      })
    }
    expect(FakeWebSocket.instances).toHaveLength(6)

    act(() => latest().emitClose())

    expect(FakeWebSocket.instances).toHaveLength(6)
    expect(result.current.reconnecting).toBe(false)
    expect(result.current.connected).toBe(false)
  })
})
