import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useWebSocket } from '../useWebSocket'

// ---------------------------------------------------------------------------
// Mock WebSocket
// ---------------------------------------------------------------------------

type WSListener = (event: { data?: string }) => void

class MockWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3

  readonly CONNECTING = 0
  readonly OPEN = 1
  readonly CLOSING = 2
  readonly CLOSED = 3

  url: string
  readyState: number = MockWebSocket.CONNECTING
  onopen: WSListener | null = null
  onclose: WSListener | null = null
  onmessage: WSListener | null = null
  onerror: WSListener | null = null
  send = vi.fn()
  close = vi.fn(() => {
    this.readyState = MockWebSocket.CLOSED
    if (this.onclose) this.onclose({})
  })

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }

  /** Simulate the server accepting the connection. */
  simulateOpen(): void {
    this.readyState = MockWebSocket.OPEN
    if (this.onopen) this.onopen({})
  }

  /** Simulate receiving a message from the server. */
  simulateMessage(data: unknown): void {
    if (this.onmessage) this.onmessage({ data: JSON.stringify(data) })
  }

  /** Simulate a connection error followed by close. */
  simulateError(): void {
    if (this.onerror) this.onerror({})
    this.readyState = MockWebSocket.CLOSED
    if (this.onclose) this.onclose({})
  }

  static instances: MockWebSocket[] = []
  static clear(): void {
    MockWebSocket.instances = []
  }
  static latest(): MockWebSocket {
    return MockWebSocket.instances[MockWebSocket.instances.length - 1]
  }
}

beforeEach(() => {
  MockWebSocket.clear()
  vi.stubGlobal('WebSocket', MockWebSocket)
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

// ---------------------------------------------------------------------------
// Connection lifecycle
// ---------------------------------------------------------------------------

describe('connection lifecycle', () => {
  it('opens a WebSocket connection to the given URL', () => {
    renderHook(() => useWebSocket('ws://localhost:8000/ws/play'))

    expect(MockWebSocket.instances).toHaveLength(1)
    expect(MockWebSocket.latest().url).toBe('ws://localhost:8000/ws/play')
  })

  it('reports readyState as CONNECTING initially', () => {
    const { result } = renderHook(() => useWebSocket('ws://localhost:8000/ws/play'))

    expect(result.current.readyState).toBe(MockWebSocket.CONNECTING)
  })

  it('updates readyState to OPEN when connection opens', () => {
    const { result } = renderHook(() => useWebSocket('ws://localhost:8000/ws/play'))

    act(() => {
      MockWebSocket.latest().simulateOpen()
    })

    expect(result.current.readyState).toBe(MockWebSocket.OPEN)
  })

  it('calls onOpen callback when connection opens', () => {
    const onOpen = vi.fn()
    renderHook(() => useWebSocket('ws://localhost:8000/ws/play', { onOpen }))

    act(() => {
      MockWebSocket.latest().simulateOpen()
    })

    expect(onOpen).toHaveBeenCalledOnce()
  })

  it('calls onClose callback when connection closes', () => {
    const onClose = vi.fn()
    renderHook(() => useWebSocket('ws://localhost:8000/ws/play', { onClose, reconnect: false }))

    act(() => {
      MockWebSocket.latest().simulateOpen()
    })
    act(() => {
      MockWebSocket.latest().close()
    })

    expect(onClose).toHaveBeenCalledOnce()
  })

  it('calls onError callback on error', () => {
    const onError = vi.fn()
    renderHook(() => useWebSocket('ws://localhost:8000/ws/play', { onError, reconnect: false }))

    act(() => {
      MockWebSocket.latest().simulateError()
    })

    expect(onError).toHaveBeenCalledOnce()
  })

  it('closes the WebSocket on unmount', () => {
    const { unmount } = renderHook(() =>
      useWebSocket('ws://localhost:8000/ws/play', { reconnect: false }),
    )

    const ws = MockWebSocket.latest()
    act(() => {
      ws.simulateOpen()
    })

    unmount()

    expect(ws.close).toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// Conditional connection (url = null)
// ---------------------------------------------------------------------------

describe('url = null', () => {
  it('does not open a connection when url is null', () => {
    renderHook(() => useWebSocket(null))

    expect(MockWebSocket.instances).toHaveLength(0)
  })

  it('returns readyState CLOSED when url is null', () => {
    const { result } = renderHook(() => useWebSocket(null))

    expect(result.current.readyState).toBe(MockWebSocket.CLOSED)
  })

  it('returns null lastMessage when url is null', () => {
    const { result } = renderHook(() => useWebSocket(null))

    expect(result.current.lastMessage).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Sending and receiving messages
// ---------------------------------------------------------------------------

describe('messages', () => {
  it('sendMessage sends JSON-serialised data', () => {
    const { result } = renderHook(() => useWebSocket('ws://localhost:8000/ws/play'))

    act(() => {
      MockWebSocket.latest().simulateOpen()
    })

    act(() => {
      result.current.sendMessage({ type: 'move', uci: 'e2e4' })
    })

    expect(MockWebSocket.latest().send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'move', uci: 'e2e4' }),
    )
  })

  it('updates lastMessage on incoming message', () => {
    const { result } = renderHook(() =>
      useWebSocket<{ type: string }>('ws://localhost:8000/ws/play'),
    )

    act(() => {
      MockWebSocket.latest().simulateOpen()
    })
    act(() => {
      MockWebSocket.latest().simulateMessage({ type: 'board_update' })
    })

    expect(result.current.lastMessage).toEqual({ type: 'board_update' })
  })

  it('calls onMessage callback with parsed data', () => {
    const onMessage = vi.fn()
    renderHook(() => useWebSocket<{ n: number }>('ws://localhost:8000/ws/play', { onMessage }))

    act(() => {
      MockWebSocket.latest().simulateOpen()
    })
    act(() => {
      MockWebSocket.latest().simulateMessage({ n: 42 })
    })

    expect(onMessage).toHaveBeenCalledWith({ n: 42 })
  })
})

// ---------------------------------------------------------------------------
// Reconnection with exponential backoff
// ---------------------------------------------------------------------------

describe('reconnection', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  it('reconnects after connection error with exponential backoff', () => {
    renderHook(() =>
      useWebSocket('ws://localhost:8000/ws/play', {
        reconnect: true,
        reconnectInterval: 1000,
        maxRetries: 3,
      }),
    )

    expect(MockWebSocket.instances).toHaveLength(1)

    // First error → retry after 1000ms (1000 * 2^0)
    act(() => {
      MockWebSocket.latest().simulateError()
    })
    act(() => {
      vi.advanceTimersByTime(999)
    })
    expect(MockWebSocket.instances).toHaveLength(1)
    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(MockWebSocket.instances).toHaveLength(2)

    // Second error → retry after 2000ms (1000 * 2^1)
    act(() => {
      MockWebSocket.latest().simulateError()
    })
    act(() => {
      vi.advanceTimersByTime(1999)
    })
    expect(MockWebSocket.instances).toHaveLength(2)
    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(MockWebSocket.instances).toHaveLength(3)

    // Third error → retry after 4000ms (1000 * 2^2)
    act(() => {
      MockWebSocket.latest().simulateError()
    })
    act(() => {
      vi.advanceTimersByTime(3999)
    })
    expect(MockWebSocket.instances).toHaveLength(3)
    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(MockWebSocket.instances).toHaveLength(4)
  })

  it('stops retrying after maxRetries is reached', () => {
    renderHook(() =>
      useWebSocket('ws://localhost:8000/ws/play', {
        reconnect: true,
        reconnectInterval: 1000,
        maxRetries: 2,
      }),
    )

    // First error → retry 1
    act(() => {
      MockWebSocket.latest().simulateError()
    })
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(MockWebSocket.instances).toHaveLength(2)

    // Second error → retry 2
    act(() => {
      MockWebSocket.latest().simulateError()
    })
    act(() => {
      vi.advanceTimersByTime(2000)
    })
    expect(MockWebSocket.instances).toHaveLength(3)

    // Third error → no more retries (maxRetries = 2, already retried twice)
    act(() => {
      MockWebSocket.latest().simulateError()
    })
    act(() => {
      vi.advanceTimersByTime(10000)
    })
    expect(MockWebSocket.instances).toHaveLength(3)
  })

  it('resets retry counter on successful connection', () => {
    renderHook(() =>
      useWebSocket('ws://localhost:8000/ws/play', {
        reconnect: true,
        reconnectInterval: 1000,
        maxRetries: 2,
      }),
    )

    // First error and retry
    act(() => {
      MockWebSocket.latest().simulateError()
    })
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(MockWebSocket.instances).toHaveLength(2)

    // Successful connection → resets counter
    act(() => {
      MockWebSocket.latest().simulateOpen()
    })

    // Another error → should retry from attempt 0 again
    act(() => {
      MockWebSocket.latest().simulateError()
    })
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(MockWebSocket.instances).toHaveLength(3)
  })

  it('does not reconnect when reconnect option is false', () => {
    renderHook(() => useWebSocket('ws://localhost:8000/ws/play', { reconnect: false }))

    act(() => {
      MockWebSocket.latest().simulateError()
    })
    act(() => {
      vi.advanceTimersByTime(10000)
    })

    expect(MockWebSocket.instances).toHaveLength(1)
  })

  it('clears reconnect timer on unmount', () => {
    const { unmount } = renderHook(() =>
      useWebSocket('ws://localhost:8000/ws/play', {
        reconnect: true,
        reconnectInterval: 1000,
        maxRetries: 5,
      }),
    )

    act(() => {
      MockWebSocket.latest().simulateError()
    })

    unmount()

    act(() => {
      vi.advanceTimersByTime(5000)
    })

    // Only the initial connection, no reconnect after unmount
    expect(MockWebSocket.instances).toHaveLength(1)
  })
})

// ---------------------------------------------------------------------------
// URL change
// ---------------------------------------------------------------------------

describe('url change', () => {
  it('closes old connection and opens new one when url changes', () => {
    const { rerender } = renderHook(({ url }: { url: string }) => useWebSocket(url), {
      initialProps: { url: 'ws://localhost:8000/ws/play' },
    })

    const firstWs = MockWebSocket.latest()
    act(() => {
      firstWs.simulateOpen()
    })

    rerender({ url: 'ws://localhost:8000/ws/sprt' })

    expect(firstWs.close).toHaveBeenCalled()
    expect(MockWebSocket.instances).toHaveLength(2)
    expect(MockWebSocket.latest().url).toBe('ws://localhost:8000/ws/sprt')
  })
})
