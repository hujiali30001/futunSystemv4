import { useEffect, useRef } from 'react'
import type { LeaderboardRow } from '../api'

function buildWsUrl(direction: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws/leaderboard?direction=${direction}`
}

export function useWebSocket(
  direction: string,
  onData: (items: LeaderboardRow[]) => void,
  enabled: boolean,
) {
  const wsRef = useRef<WebSocket | null>(null)
  const onDataRef = useRef(onData)
  onDataRef.current = onData

  useEffect(() => {
    if (!enabled) {
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
      return
    }

    const url = buildWsUrl(direction)
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data)
        if (payload && Array.isArray(payload.items)) {
          onDataRef.current(payload.items)
        }
      } catch {
      }
    }

    ws.onerror = () => {
    }

    ws.onclose = () => {
      wsRef.current = null
    }

    return () => {
      ws.close()
      wsRef.current = null
    }
  }, [direction, enabled])
}
