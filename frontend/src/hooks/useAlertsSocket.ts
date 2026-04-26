import { useEffect, useMemo, useState } from 'react'
import { getSocketAuthToken, hasAnyAuthCredential } from '../lib/api'

export interface AlertMessage {
  id: string
  title: string
  level: 'INFO' | 'WARN' | 'CRITICAL'
  time: string
}

const SOCKET_URL = import.meta.env.VITE_ALERTS_WS_URL ?? 'ws://localhost:8000/ws/alerts/'
const MAX_RECONNECT_ATTEMPTS = 5

export function useAlertsSocket() {
  const [connected, setConnected] = useState(false)
  const [reconnecting, setReconnecting] = useState(false)
  const [messages, setMessages] = useState<AlertMessage[]>([])

  const wsUrl = useMemo(() => SOCKET_URL, [])

  useEffect(() => {
    let active = true
    let ws: WebSocket | null = null
    let reconnectTimer: number | undefined
    let attempt = 0

    const connect = async () => {
      const token = await getSocketAuthToken()
      const url = token ? `${wsUrl}?token=${encodeURIComponent(token)}` : wsUrl
      ws = new WebSocket(url)

      ws.onopen = () => {
        if (!active) {
          return
        }
        attempt = 0
        setConnected(true)
        setReconnecting(false)
      }

      ws.onclose = () => {
        if (!active) {
          return
        }
        setConnected(false)

        if (!hasAnyAuthCredential() || attempt >= MAX_RECONNECT_ATTEMPTS) {
          setReconnecting(false)
          return
        }

        setReconnecting(true)
        const delay = Math.min(1000 * 2 ** attempt, 15000)
        attempt += 1
        reconnectTimer = window.setTimeout(connect, delay)
      }

      ws.onerror = () => {
        if (!active) {
          return
        }
        setConnected(false)
      }

      ws.onmessage = (event) => {
        try {
          const parsed = JSON.parse(event.data)
          const msg: AlertMessage = {
            id: crypto.randomUUID(),
            title: parsed.title ?? parsed.message ?? 'New alert',
            level: parsed.level ?? 'INFO',
            time: parsed.time ?? new Date().toISOString(),
          }
          setMessages((prev) => [msg, ...prev].slice(0, 20))
        } catch {
          const fallback: AlertMessage = {
            id: crypto.randomUUID(),
            title: String(event.data),
            level: 'INFO',
            time: new Date().toISOString(),
          }
          setMessages((prev) => [fallback, ...prev].slice(0, 20))
        }
      }
    }

    connect()

    return () => {
      active = false
      if (reconnectTimer) {
        window.clearTimeout(reconnectTimer)
      }
      ws?.close()
    }
  }, [wsUrl])

  return { connected, reconnecting, messages }
}
