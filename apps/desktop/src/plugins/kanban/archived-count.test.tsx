/**
 * Drained-board visibility (t_61bd3b39): the board header must surface the
 * archived count even while the archived lane is filtered out, and the Show
 * archived toggle must persist across restarts.
 *
 * Binding depth: FilterMenu + KanbanBoardPage run through the REAL
 * DropdownMenu and the real $showArchived store — only the SDK doors
 * (storage/rest/host) are stubbed, so both the persistence and the
 * header-chip contract are proven end to end.
 */
import type * as HermesSdk from '@hermes/plugin-sdk'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { KanbanBoardPage } from './board'

// ---------------------------------------------------------------------------
// SDK doors. In-memory storage records every set() so persistence and
// restart-hydration are observable; rest() is a programmable fake.
// ---------------------------------------------------------------------------

const store = new Map<string, unknown>()
const storage = {
  get: <T,>(key: string, fallback: T): T => (store.has(key) ? (store.get(key) as T) : fallback),
  set: (key: string, value: unknown) => void store.set(key, value),
  remove: (key: string) => void store.delete(key)
}

const restMock = vi.fn((_path: string) => Promise.resolve<Record<string, unknown>>({}))
const socketMock = vi.fn(() => () => undefined)

vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const sdk = await importOriginal<typeof HermesSdk>()

  return {
    ...sdk,
    host: {
      ...sdk.host,
      notify: vi.fn()
    }
  }
})

type ApiModule = typeof import('./api')

/** Bind the plugin's doors against the fakes. Simulates a FRESH plugin load:
 *  atoms reset to import defaults, but plugin STORAGE is left exactly as the
 *  previous bind left it — that's the restart-hydration surface under test. */
const bindApi = async (): Promise<{ mod: ApiModule; dispose: () => void }> => {
  const mod = await import('./api')
  mod.$showArchived.set(false)
  mod.$boardSlug.set('')
  const dispose = mod.bindApi(
    restMock as unknown as Parameters<ApiModule['bindApi']>[0],
    storage as unknown as Parameters<ApiModule['bindApi']>[1],
    socketMock as unknown as Parameters<ApiModule['bindApi']>[2]
  )

  return { mod, dispose }
}

/** The plugin registers its locale bundles at load; the test harness mounts
 *  components outside that lifecycle, so register the real English bundle
 *  against the same id ('kanban') the plugin uses. */
const registerKanbanStrings = async () => {
  const { registerPluginLocales } = await import('@/i18n/plugin-i18n')
  const { KANBAN_LOCALES } = await import('./i18n')

  registerPluginLocales('kanban', { en: KANBAN_LOCALES.en as never })
}

const renderWithProviders = async (ui: React.ReactElement) => {
  await registerKanbanStrings()
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } }
  })

  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

/** A minimal board payload shaped like the plugin backend's GET /board. */
const boardPayload = (archivedCount: number) => ({
  columns: [
    { name: 'todo', tasks: [] },
    { name: 'running', tasks: [] }
  ],
  tenants: [],
  assignees: [],
  archived_count: archivedCount,
  latest_event_id: 0,
  now: Math.floor(Date.now() / 1000)
})

const boardsPayload = {
  boards: [{ slug: 'default', default_workspace_kind: 'scratch', default_workdir: '' }],
  current: 'default'
}

/** Shaped like GET /orchestration — `useDefaultAssignee` reads
 *  `default_assignee.trim()` off this, so it must not be a bare object. */
const orchestrationPayload = {
  default_assignee: '',
  auto_decompose: false,
  orchestrator_profile: '',
  resolved_orchestrator_profile: '',
  resolved_default_assignee: ''
}

const mockBoard = (archivedCount: number) =>
  restMock.mockImplementation((path: string) => {
    // /boards must be matched BEFORE /board — the prefix would otherwise
    // swallow the boards-list call.
    if (path.startsWith('/boards')) {
      return Promise.resolve(boardsPayload)
    }

    if (path.startsWith('/board')) {
      return Promise.resolve(boardPayload(archivedCount))
    }

    if (path.startsWith('/orchestration')) {
      return Promise.resolve(orchestrationPayload)
    }

    return Promise.resolve({})
  })

beforeEach(() => {
  restMock.mockImplementation((path: string) => {
    if (path.startsWith('/orchestration')) {
      return Promise.resolve(orchestrationPayload)
    }

    return Promise.resolve({ config: {} })
  })
})

afterEach(() => {
  cleanup()
  store.clear()
  restMock.mockReset()
  vi.clearAllMocks()
})

describe('showArchived persistence (bindApi)', () => {
  it('writes the toggle choice to plugin storage', async () => {
    const { mod, dispose } = await bindApi()

    mod.$showArchived.set(true)
    expect(store.get('showArchived')).toBe(true)

    mod.$showArchived.set(false)
    expect(store.get('showArchived')).toBe(false)
    dispose()
  })

  it('hydrates the stored choice on rebind (restart survival)', async () => {
    const first = await bindApi()
    first.mod.$showArchived.set(true)
    first.dispose()

    // Rebind = a fresh plugin load after an app restart: the stored value
    // must come back through storage.get, not reset to false.
    const second = await bindApi()
    expect(second.mod.$showArchived.get()).toBe(true)
    second.dispose()
  })

  it('seeds include_archived_by_default from /config ONLY on first run', async () => {
    // The REAL /config contract is FLAT (get_config in plugin_api.py returns
    // the knob at the top level) — mock exactly that shape, not a fabricated
    // {config: {...}} envelope (review round 1: the old mock masked a seed
    // that could never fire in production).
    restMock.mockImplementation((path: string) =>
      path === '/config'
        ? Promise.resolve({ include_archived_by_default: true })
        : Promise.resolve({})
    )

    // First bind, no stored choice: the knob seeds ON.
    const first = await bindApi()
    await waitFor(() => expect(first.mod.$showArchived.get()).toBe(true))
    first.dispose()

    // The operator flips it off — the explicit choice is stored.
    store.set('showArchived', false)
    expect(store.get('showArchived')).toBe(false)

    // Rebind with the knob still true: the stored choice must win.
    const third = await bindApi()
    expect(third.mod.$showArchived.get()).toBe(false)
    third.dispose()
  })

  it('does NOT seed when /config reports the knob off (flat falsy shape)', async () => {
    restMock.mockImplementation((path: string) =>
      path === '/config'
        ? Promise.resolve({ include_archived_by_default: false })
        : Promise.resolve({})
    )

    const { mod, dispose } = await bindApi()
    // Let any pending /config promise settle before asserting.
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(mod.$showArchived.get()).toBe(false)
    expect(store.has('showArchived')).toBe(false)
    dispose()
  })
})

describe('archived-count chip on the board header', () => {
  it('renders "N archived" when the lane is hidden and count > 0', async () => {
    const { dispose } = await bindApi()
    mockBoard(20)

    await renderWithProviders(<KanbanBoardPage />)

    const chip = await screen.findByTestId('archived-count-chip')
    expect(chip.textContent).toContain('20')
    expect(chip.getAttribute('aria-label')).toBe('20 archived')
    dispose()
  })

  it('hides the chip when the archived count is zero', async () => {
    const { dispose } = await bindApi()
    mockBoard(0)

    await renderWithProviders(<KanbanBoardPage />)

    await screen.findByText('Kanban')
    expect(screen.queryByTestId('archived-count-chip')).toBeNull()
    dispose()
  })

  it('clicking the chip flips Show archived on and persists the choice', async () => {
    mockBoard(20)
    const { mod, dispose } = await bindApi()
    expect(mod.$showArchived.get()).toBe(false)

    await renderWithProviders(<KanbanBoardPage />)

    const chip = await screen.findByTestId('archived-count-chip')
    fireEvent.click(chip)

    await waitFor(() => expect(mod.$showArchived.get()).toBe(true))
    expect(store.get('showArchived')).toBe(true)
    // Lane now visible → the chip hands over to the archived column.
    await waitFor(() => expect(screen.queryByTestId('archived-count-chip')).toBeNull())
    dispose()
  })
})
