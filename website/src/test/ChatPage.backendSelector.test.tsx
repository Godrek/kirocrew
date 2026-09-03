/**
 * The composer's harness control: which ACP backend THIS chat runs on.
 *
 * The three properties worth pinning are the ones that fail silently:
 *
 * 1. **The chip answers for the SLOT, not for the configuration.**
 *    `agent.acp_backend` is the default for a slot that has never been bound;
 *    a slot with its own binding outranks it. Reading it the other way round
 *    names a harness the session is not on, and the model vocabulary rendered
 *    beside it is then another harness's.
 * 2. **A conversation is never migrated silently.** The switch discards the
 *    native session and continues by replay, so a slot holding turns is asked
 *    FIRST — after the fact there is nothing left to decline. A slot with no
 *    turns loses nothing and is switched with no dialog.
 * 3. **A dropped model pin is reported, never substituted.** `model_cleared`
 *    is the server's own answer; the chip falls back to "inherit whatever this
 *    harness serves" rather than being re-pointed at some other id.
 *
 * Plus the disabled states, which mirror the server's 409 guards so a user
 * reads the reason instead of discovering it as a failed request.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act, waitFor, fireEvent } from '@testing-library/react'
import { Provider } from 'react-redux'
import { MemoryRouter } from 'react-router-dom'
import { configureStore } from '@reduxjs/toolkit'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import chatReducer from '../store/chatSlice'
import dashboardReducer from '../store/dashboardSlice'
import notificationsReducer from '../store/notificationsSlice'
import { ThemeProvider } from '../hooks/useTheme'
import type { RootState } from '../store'
import type { ChatSlot } from '../types'

const KIRO_CAPS = {
  backend: '', catalog: 'kiro_cli', registry_provider: 'acp',
  selectable: true, runtime_switch: true, switch_scope: 'live_session', reasoning_effort: true,
}
// Repeated as a literal inside the `vi.mock` factory below, which is hoisted
// above this const.
const PINNED_MODEL = 'claude-opus-5'

const {
  kirocrewConfigMock, modelCapabilitiesMock, modelsMock, chatSlotBackendMock,
} = vi.hoisted(() => ({
  kirocrewConfigMock: vi.fn(),
  modelCapabilitiesMock: vi.fn(),
  modelsMock: vi.fn(),
  chatSlotBackendMock: vi.fn(),
}))

interface VirtuosoMockProps {
  data?: unknown[]
  itemContent: (index: number, item: unknown) => ReactNode
}
vi.mock('react-virtuoso', () => ({ Virtuoso: ({ data, itemContent }: VirtuosoMockProps) => <div data-testid="virtuoso">{data?.map((d: unknown, i: number) => <div key={i}>{itemContent(i, d)}</div>)}</div> }))
vi.mock('../api/client', () => ({
  api: {
    chatSlots: vi.fn().mockResolvedValue([]),
    kirocrewConfig: kirocrewConfigMock,
    modelCapabilities: modelCapabilitiesMock,
    chatSlotDetail: vi.fn().mockResolvedValue({ messages: [], running: false, has_more: false, total: 0 }),
    chatHistory: vi.fn().mockResolvedValue({ sessions: [] }),
    models: modelsMock,
    agents: vi.fn().mockResolvedValue([]),
    agentDetail: vi.fn().mockResolvedValue({ model: 'claude-opus-5' }),
    agentResolvedModel: vi.fn().mockResolvedValue({ model: 'claude-opus-5' }),
    chatSlotModel: vi.fn().mockResolvedValue({ ok: true }),
    chatSlotBackend: chatSlotBackendMock,
    workspaces: vi.fn().mockResolvedValue({ workspaces: [] }),
    slackChannels: vi.fn().mockResolvedValue([]),
    spawnList: vi.fn().mockResolvedValue({ agents: [] }),
  },
  SEARCH_MIN_CHARS: 2,
}))
vi.mock('../hooks/useVoiceInput', () => ({ useVoiceInput: () => ({ recording: false, transcribing: false, toggle: vi.fn() }), voiceInputSupported: false }))
vi.mock('../hooks/useBranding', () => ({ useBranding: () => ({ botName: 'Test', avatar: '' }) }))
vi.mock('../hooks/useAgents', () => ({ useAgents: () => ({ agents: [{ name: 'kirocrew' }], defaultAgent: 'kirocrew' }) }))
vi.mock('../components/MarkdownRenderer', () => ({ default: ({ content }: { content: string }) => <span>{content}</span> }))
vi.mock('../components/WelcomeView', () => ({ default: () => null }))
vi.mock('../components/MarkdownPanel', () => ({ default: () => null }))
vi.mock('../pages/chat/ActivityViewer', () => ({ default: () => null }))
vi.mock('../components/DetailPanel', () => ({ default: () => null }))
vi.mock('../hooks/useWebSocket', () => ({ useWebSocket: () => ({ subscribeLogs: () => {} }) }))

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
})

import ChatPage from '../pages/ChatPage'

function makeStore(slot: Partial<ChatSlot>) {
  return configureStore({
    reducer: { dashboard: dashboardReducer, chat: chatReducer, notifications: notificationsReducer },
    preloadedState: {
      dashboard: {
        status: null,
        slots: [{
          key: 'slot-a', messages: 0, running: false, mode: '', agent: 'kirocrew',
          pending_approval: false, waiting_for_input: false, last_activity_ts: undefined,
          ...slot,
        }],
        unreadSlots: [], refreshTrigger: 0, approvalMode: 'normal',
        subagentRunning: {}, subagentDetails: {}, subagentText: {},
      } as unknown as RootState['dashboard'],
      chat: {
        activeSlot: 'slot-a', messages: [],
        // `slotRunning` / `slotStopping` are the LIVE turn state, separate from
        // the coalesced slot payload; the control has to read both, so tests
        // that exercise one leave the other alone.
        slotRunning: false, slotStopping: false, slotState: 'idle',
        history: [], historyHasMore: false, pendingInput: null,
        subagents: {}, toolLog: [], activityOpen: false, activityTab: 'tools',
        slotHasMore: false, slotOldestIndex: 0, loadingOlder: false,
        slotStatusDetail: {}, slotContextPct: {}, slotActivity: {}, slotHistory: [],
        historyOffset: 0, _wsChunkedDuringFetch: false,
        slotMessages: {}, slotLoading: false,
      } as unknown as RootState['chat'],
      notifications: { items: [] } as unknown as RootState['notifications'],
    },
  })
}

/** Returns the store, because the "pin was dropped" report is dispatched as
 *  the shared switch notice and RENDERED by `App` — a ChatPage-only tree has
 *  no toast to read it off. */
async function renderChat(slot: Partial<ChatSlot> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const store = makeStore(slot)
  await act(async () => {
    render(
      <QueryClientProvider client={qc}>
        <Provider store={store}>
          <ThemeProvider>
            <MemoryRouter><ChatPage /></MemoryRouter>
          </ThemeProvider>
        </Provider>
      </QueryClientProvider>,
    )
  })
  await waitFor(() => expect(screen.getByLabelText('Message input')).toBeTruthy())
  return store
}

/** The composer's harness chip. Matched on its tooltip because the visible
 *  label collapses away on a narrow shelf. */
function backendChip(): HTMLElement {
  return screen.getByTitle(/^Backend: /)
}

async function openBackendPicker() {
  await act(async () => { fireEvent.click(backendChip()) })
}

async function pickBackend(label: string) {
  await openBackendPicker()
  const option = await waitFor(() => screen.getByRole('option', { name: label }))
  await act(async () => { fireEvent.click(option) })
}

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
  vi.clearAllMocks()
  kirocrewConfigMock.mockResolvedValue({ agent: { acp_backend: '' } })
  modelCapabilitiesMock.mockResolvedValue({ ...KIRO_CAPS })
  modelsMock.mockResolvedValue([
    { model_name: 'auto', description: 'Models chosen by task' },
    { model_name: PINNED_MODEL, description: 'Claude Opus 5' },
  ])
  chatSlotBackendMock.mockResolvedValue({
    ok: true, slot: 'slot-a', backend: 'codex',
    changed: true, reset: false, model_cleared: false, model: '',
  })
})

describe('ChatPage — composer backend control', { timeout: 15_000 }, () => {
  it('names the slot\'s OWN backend, not the configured default', async () => {
    kirocrewConfigMock.mockResolvedValue({ agent: { acp_backend: '' } })
    await renderChat({ acp_backend: 'codex' })
    expect(await waitFor(() => screen.getByTitle('Backend: Codex'))).toBeTruthy()
  })

  it('falls back to the configured default only for a slot with no binding', async () => {
    kirocrewConfigMock.mockResolvedValue({ agent: { acp_backend: 'claude' } })
    // `acp_backend` absent — never bound. Deliberately not `''`, which IS the
    // kiro harness and must NOT read as "unbound".
    await renderChat({})
    expect(await waitFor(() => screen.getByTitle('Backend: Claude Code'))).toBeTruthy()
  })

  it('treats an explicit kiro binding as a binding, not as unset', async () => {
    kirocrewConfigMock.mockResolvedValue({ agent: { acp_backend: 'codex' } })
    await renderChat({ acp_backend: '' })
    expect(await waitFor(() => screen.getByTitle('Backend: Kiro CLI'))).toBeTruthy()
  })

  it('offers the dashboard-selectable harnesses and checks the active one', async () => {
    await renderChat({ acp_backend: '' })
    await openBackendPicker()
    const options = await waitFor(() => screen.getAllByRole('option'))
    expect(options.map(o => o.textContent)).toEqual(['Kiro CLI', 'Claude Code', 'Codex'])
    expect(options[0].getAttribute('aria-selected')).toBe('true')
  })

  it('switches a slot with no messages without asking', async () => {
    await renderChat({ acp_backend: '', messages: 0 })
    await pickBackend('Codex')

    expect(chatSlotBackendMock).toHaveBeenCalledWith('slot-a', 'codex')
    // Nothing exists on the old harness, so there is nothing to confirm.
    expect(screen.queryByText(/Switch this chat to/)).toBeNull()
  })

  it('sends "" for kiro rather than omitting the harness', async () => {
    // `''` IS the kiro harness. The server reads the body by PRESENCE, so a
    // truthiness-driven client would send nothing and be refused.
    await renderChat({ acp_backend: 'codex', messages: 0 })
    await pickBackend('Kiro CLI')
    expect(chatSlotBackendMock).toHaveBeenCalledWith('slot-a', '')
  })

  it('does nothing when the picked harness is the one already in force', async () => {
    await renderChat({ acp_backend: 'codex', messages: 0 })
    await pickBackend('Codex')
    expect(chatSlotBackendMock).not.toHaveBeenCalled()
  })

  it('asks before switching a slot that holds a conversation, and says why', async () => {
    await renderChat({ acp_backend: '', messages: 4, model: PINNED_MODEL })
    await pickBackend('Codex')

    // Stated BEFORE the call: the session is discarded and replayed, and the
    // pinned model does not exist on the target.
    const body = await waitFor(() => screen.getByText(/This starts a new session on Codex\./))
    expect(body.textContent).toContain('replayed')
    expect(body.textContent).toContain(PINNED_MODEL)
    expect(chatSlotBackendMock).not.toHaveBeenCalled()
  })

  it('switches only after the dialog is confirmed', async () => {
    await renderChat({ acp_backend: '', messages: 4 })
    await pickBackend('Codex')

    const confirm = await waitFor(() => screen.getByRole('button', { name: 'Switch to Codex' }))
    await act(async () => { fireEvent.click(confirm) })
    expect(chatSlotBackendMock).toHaveBeenCalledWith('slot-a', 'codex')
  })

  it('leaves the harness alone when the dialog is cancelled', async () => {
    await renderChat({ acp_backend: '', messages: 4 })
    await pickBackend('Codex')

    const cancel = await waitFor(() => screen.getByRole('button', { name: 'Cancel' }))
    await act(async () => { fireEvent.click(cancel) })
    expect(chatSlotBackendMock).not.toHaveBeenCalled()
    expect(screen.getByTitle('Backend: Kiro CLI')).toBeTruthy()
  })

  it('omits the model clause when nothing but the inherit default is pinned', async () => {
    // `auto` is the absence of a pick and every harness honours it, so there is
    // no pin to warn about losing.
    await renderChat({ acp_backend: '', messages: 4, model: 'auto' })
    await pickBackend('Codex')
    const body = await waitFor(() => screen.getByText(/This starts a new session on Codex\./))
    expect(body.textContent).not.toContain('is not available there')
  })

  it('clears the shown model when the server reports the pin was dropped', async () => {
    chatSlotBackendMock.mockResolvedValue({
      ok: true, slot: 'slot-a', backend: 'codex',
      changed: true, reset: true, model_cleared: true, model: '',
    })
    // The Codex session advertises its own vocabulary; the kiro pin is in
    // neither list, so the chip must fall back to "inherit", never to some
    // other id this UI picked on the user's behalf.
    modelCapabilitiesMock.mockResolvedValue({
      ...KIRO_CAPS, backend: 'codex', catalog: 'advertised', registry_provider: '',
      reasoning_effort: false,
    })
    const store = await renderChat({ acp_backend: '', messages: 0, model: PINNED_MODEL })
    expect(await waitFor(() => screen.getByTitle(`Model: ${PINNED_MODEL}`))).toBeTruthy()

    await pickBackend('Codex')
    await waitFor(() => expect(screen.queryByTitle(`Model: ${PINNED_MODEL}`)).toBeNull())
    // Reported rather than silently swapped: the drop is named, and no other
    // model id is put in its place.
    await waitFor(() => expect(store.getState().chat.agentSwitchNotice?.message)
      .toContain(`The model ${PINNED_MODEL} is not available on Codex`))
    expect(store.getState().dashboard.slots[0].model).toBe('')
  })

  it('asks for the new harness\'s model vocabulary after a switch', async () => {
    await renderChat({ acp_backend: '', messages: 0 })
    await waitFor(() => expect(modelsMock).toHaveBeenCalledWith(''))
    modelsMock.mockClear()
    modelCapabilitiesMock.mockClear()

    await pickBackend('Codex')
    await waitFor(() => expect(modelsMock).toHaveBeenCalledWith('codex'))
    await waitFor(() => expect(modelCapabilitiesMock).toHaveBeenCalledWith(
      expect.objectContaining({ slot: 'slot-a' }),
    ))
  })

  it.each([
    ['a turn in flight', { running: true }, 'Stop the current response to switch backend'],
    ['a stop in progress', { stopping: true }, 'Wait for the stop to finish to switch backend'],
    ['a pending approval', { pending_approval: true }, 'Answer the pending approval to switch backend'],
    ['an orchestrating plan', { orchestrating: true }, 'Wait for the plan to finish to switch backend'],
    ['running sub-agents', { subagents_running: true }, 'Wait for sub-agents to finish to switch backend'],
  ])('refuses the switch during %s, and says so', async (_case, slot, reason) => {
    await renderChat({ acp_backend: '', ...slot })
    const chip = await waitFor(() => screen.getByTitle(reason))
    expect((chip as HTMLButtonElement).disabled).toBe(true)
    await act(async () => { fireEvent.click(chip) })
    expect(screen.queryAllByRole('option')).toHaveLength(0)
  })
})
