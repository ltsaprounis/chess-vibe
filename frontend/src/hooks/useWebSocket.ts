/**
 * Reusable WebSocket hook with automatic reconnection and exponential backoff.
 *
 * Manages the full lifecycle of a WebSocket connection — open, message
 * dispatch, error handling, automatic reconnection, and clean teardown
 * on unmount or URL change.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

export interface UseWebSocketOptions<T> {
  onMessage?: (data: T) => void
  onOpen?: () => void
  onClose?: () => void
  onError?: (event: Event) => void
  reconnect?: boolean
  maxRetries?: number
  reconnectInterval?: number
}

export interface UseWebSocketReturn<T> {
  sendMessage: (data: unknown) => void
  readyState: number
  lastMessage: T | null
}

export function useWebSocket<T = unknown>(
  url: string | null,
  options?: UseWebSocketOptions<T>,
): UseWebSocketReturn<T> {
  const {
    onMessage,
    onOpen,
    onClose,
    onError,
    reconnect = true,
    maxRetries = 5,
    reconnectInterval = 1000,
  } = options ?? {}

  const wsRef = useRef<WebSocket | null>(null)
  const retryCountRef = useRef(0)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const unmountedRef = useRef(false)

  const [readyState, setReadyState] = useState<number>(
    url === null ? WebSocket.CLOSED : WebSocket.CONNECTING,
  )
  const [lastMessage, setLastMessage] = useState<T | null>(null)

  // Store callbacks in refs to avoid re-triggering the connection effect
  const onMessageRef = useRef(onMessage)
  const onOpenRef = useRef(onOpen)
  const onCloseRef = useRef(onClose)
  const onErrorRef = useRef(onError)
  const connectRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    onMessageRef.current = onMessage
    onOpenRef.current = onOpen
    onCloseRef.current = onClose
    onErrorRef.current = onError
  })

  // Store reconnect options in refs so the connect function stays stable
  const reconnectRef = useRef(reconnect)
  const maxRetriesRef = useRef(maxRetries)
  const reconnectIntervalRef = useRef(reconnectInterval)

  useEffect(() => {
    reconnectRef.current = reconnect
    maxRetriesRef.current = maxRetries
    reconnectIntervalRef.current = reconnectInterval
  })

  useEffect(() => {
    unmountedRef.current = false
    retryCountRef.current = 0

    if (url === null) {
      return
    }

    const connect = (): void => {
      if (unmountedRef.current) return

      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        if (unmountedRef.current) return
        retryCountRef.current = 0
        setReadyState(WebSocket.OPEN)
        onOpenRef.current?.()
      }

      ws.onmessage = (event: MessageEvent) => {
        if (unmountedRef.current) return
        try {
          const data = JSON.parse(event.data as string) as T
          setLastMessage(data)
          onMessageRef.current?.(data)
        } catch {
          onErrorRef.current?.(new ErrorEvent('error', { message: 'Failed to parse message' }))
        }
      }

      ws.onerror = (event: Event) => {
        if (unmountedRef.current) return
        onErrorRef.current?.(event)
      }

      ws.onclose = () => {
        if (unmountedRef.current) return
        setReadyState(WebSocket.CLOSED)
        onCloseRef.current?.()

        if (reconnectRef.current && retryCountRef.current < maxRetriesRef.current) {
          const delay = reconnectIntervalRef.current * Math.pow(2, retryCountRef.current)
          retryCountRef.current += 1
          reconnectTimerRef.current = setTimeout(() => {
            if (!unmountedRef.current) {
              connect()
            }
          }, delay)
        }
      }
    }

    connectRef.current = connect
    connect()

    return () => {
      unmountedRef.current = true
      if (reconnectTimerRef.current !== null) {
        clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
      wsRef.current?.close()
    }
  }, [url])

  const sendMessage = useCallback((data: unknown) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data))
    }
  }, [])

  return {
    sendMessage,
    readyState: url === null ? WebSocket.CLOSED : readyState,
    lastMessage: url === null ? null : lastMessage,
  }
}
