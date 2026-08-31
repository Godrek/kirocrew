// SettingsSelect wraps Radix Select, which needs pointer APIs jsdom lacks —
// use the same lightweight mock the SettingsSelect unit tests use so options
// are real role="option" nodes.
vi.mock('@radix-ui/react-select', async () => await import('./__mocks__/@radix-ui/react-select'))

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'

const { patchConfigMock, kirocrewConfigMock, modelsMock, modelCapabilitiesMock } = vi.hoisted(() => ({
  patchConfigMock: vi.fn(() => Promise.resolve({})),
  // Settings asks about a BACKEND (it configures a harness that may have no
  // session at all), unlike the composer which asks about a slot. Default is
  // "no vocabulary": that is what an unmocked adapted harness looks like, and
  // it is the state the truthful "selects its own model" copy exists for.
  modelCapabilitiesMock: vi.fn(() =>
    Promise.resolve({
      backend: '', catalog: 'none', registry_provider: '',
      selectable: false, runtime_switch: false, switch_scope: 'none', reasoning_effort: false,
    })
  ),
  kirocrewConfigMock: vi.fn(() =>
    Promise.resolve({ agent: { model: 'auto', reasoning_effort: '' } })
  ),
  modelsMock: vi.fn(() =>
    Promise.resolve([
      { model_name: 'auto', description: 'Default' },
      { model_name: 'claude-opus-4.8', description: 'Opus' },
      { model_name: 'claude-haiku-4.5', description: 'Haiku' },
    ])
  ),
}))

vi.mock('../api/client', () => ({
  api: {
    dashboardConfig: () => Promise.resolve({ restore_sessions: false, restore_window_minutes: 30, merge_queued_messages: false, widget_density: 'more' }),
    kirocrewConfig: kirocrewConfigMock,
    models: modelsMock,
    modelCapabilities: modelCapabilitiesMock,
    patchConfig: patchConfigMock,
    updateDashboardConfig: () => Promise.resolve({}),
    tipsStatus: () => Promise.resolve({ enabled_config: true, opted_out: false }),
    tipsFeedback: () => Promise.resolve({ ok: true }),
  },
}))

import { ChatPanel } from '../pages/settings/ChatPanel'

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

const seed = (agent: Record<string, unknown>) =>
  kirocrewConfigMock.mockImplementation(() => Promise.resolve({ agent }) as never)

/** Open a SettingsSelect by label and return its option nodes.
 *  Waits for the control to leave its loading-disabled state first — the
 *  trigger exists (inert) while the config query is still in flight. */
async function openSelect(label: string) {
  const trigger = await screen.findByRole('combobox', { name: label })
  await waitFor(() => expect(trigger).not.toHaveAttribute('data-disabled'))
  fireEvent.click(trigger)
  return screen.getAllByRole('option')
}

/** Assert a SettingsSelect is inert: it stays closed when clicked. */
async function expectSelectInert(label: string) {
  const trigger = await screen.findByRole('combobox', { name: label })
  await waitFor(() => expect(trigger).toHaveAttribute('data-disabled'))
  fireEvent.click(trigger)
  expect(screen.queryAllByRole('option')).toHaveLength(0)
  return trigger
}

describe('ChatPanel — default model', () => {
  beforeEach(() => {
    patchConfigMock.mockClear()
    seed({ model: 'auto', reasoning_effort: '' })
  })

  it('renders the Model section with both controls', async () => {
    wrap(<ChatPanel />)
    expect(await screen.findByText('Model')).toBeInTheDocument()
    expect(await screen.findByRole('combobox', { name: 'Default Model' })).toBeInTheDocument()
    expect(
      await screen.findByRole('combobox', { name: 'Default Reasoning Effort' })
    ).toBeInTheDocument()
  })

  it('lists the models the backend advertises', async () => {
    wrap(<ChatPanel />)
    await waitFor(() => expect(modelsMock).toHaveBeenCalled())
    const opts = await openSelect('Default Model')
    const labels = opts.map(o => o.textContent)
    expect(labels).toContain('Default (auto)')
    expect(labels).toContain('claude-opus-4.8')
  })

  it('PATCHes agent.model on selection', async () => {
    wrap(<ChatPanel />)
    await waitFor(() => expect(modelsMock).toHaveBeenCalled())
    await openSelect('Default Model')
    fireEvent.click(screen.getByRole('option', { name: 'claude-opus-4.8' }))
    await waitFor(() =>
      expect(patchConfigMock).toHaveBeenCalledWith('agent.model', 'claude-opus-4.8')
    )
  })

  it('shows the stored model in the trigger', async () => {
    seed({ model: 'claude-opus-4.8', reasoning_effort: '' })
    wrap(<ChatPanel />)
    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: 'Default Model' })).toHaveTextContent(
        'claude-opus-4.8'
      )
    )
  })

  it('keeps a stored model selectable when the backend no longer lists it', async () => {
    // A model that dropped off /api/models must stay in the option list —
    // otherwise the select shows a foreign value and a stray change event
    // would silently overwrite the user's stored choice.
    seed({ model: 'claude-opus-4.7-retired', reasoning_effort: '' })
    wrap(<ChatPanel />)
    await waitFor(() => expect(modelsMock).toHaveBeenCalled())
    const opts = await openSelect('Default Model')
    expect(opts.map(o => o.textContent)).toContain('claude-opus-4.7-retired')
    expect(patchConfigMock).not.toHaveBeenCalled()
  })

  it('treats an empty stored model as the auto default', async () => {
    seed({ model: '', reasoning_effort: '' })
    wrap(<ChatPanel />)
    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: 'Default Model' })).toHaveTextContent(
        'Default (auto)'
      )
    )
  })

  it('surfaces an error banner when the write fails', async () => {
    patchConfigMock.mockImplementationOnce(() => Promise.reject(new Error('boom')) as never)
    wrap(<ChatPanel />)
    await waitFor(() => expect(modelsMock).toHaveBeenCalled())
    await openSelect('Default Model')
    fireEvent.click(screen.getByRole('option', { name: 'claude-opus-4.8' }))
    expect(await screen.findByText(/Failed to save default model/)).toBeInTheDocument()
  })
})

describe('ChatPanel — ACP backend', () => {
  beforeEach(() => {
    patchConfigMock.mockClear()
  })

  it('renders Codex as selected and hides Kiro model controls', async () => {
    seed({ acp_backend: 'codex', model: 'claude-opus-4.8' })
    wrap(<ChatPanel />)
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Agent backend' })).toHaveTextContent('Codex'))
    expect(screen.queryByRole('combobox', { name: 'Default Model' })).not.toBeInTheDocument()
    expect(screen.queryByText('claude-opus-4.8')).not.toBeInTheDocument()
  })

  it('renders Claude Code as selected and hides Kiro model controls', async () => {
    seed({ acp_backend: 'claude', model: 'claude-opus-4.8' })
    wrap(<ChatPanel />)
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Agent backend' })).toHaveTextContent('Claude Code'))
    expect(screen.queryByRole('combobox', { name: 'Default Model' })).not.toBeInTheDocument()
  })

  it('preserves Kiro model selection for the Kiro backend', async () => {
    seed({ acp_backend: '', model: 'claude-opus-4.8', reasoning_effort: '' })
    wrap(<ChatPanel />)
    expect(await screen.findByRole('combobox', { name: 'Agent backend' })).toHaveTextContent('Kiro CLI')
    expect(await screen.findByRole('combobox', { name: 'Default Model' })).toHaveTextContent('claude-opus-4.8')
  })

  it('renders an unsupported persisted backend truthfully and hides Kiro models', async () => {
    seed({ acp_backend: 'some-byo-harness', model: 'claude-opus-4.8' })
    wrap(<ChatPanel />)
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Agent backend' })).toHaveTextContent('External backend (some-byo-harness)'))
    expect(screen.queryByRole('combobox', { name: 'Default Model' })).not.toBeInTheDocument()
  })

  it('treats KAS as the kiro model family, not as an adapted harness', async () => {
    // KAS is kiro-cli behind a relay: the server's `model_for_backend` resolves
    // it to `agent.model`, so a frontend testing `backend === ''` would send a
    // KAS user down the adapted branch — where the config key is unknown and the
    // pick is written to `''`, a save that reports success and changes nothing.
    seed({ acp_backend: 'kas', model: 'claude-opus-4.8' })
    wrap(<ChatPanel />)
    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: 'Default Model' })).toHaveTextContent('claude-opus-4.8'))
    // And never the adapted branch's own card, which would write to a
    // `backend_models.kas` key the server does not read.
    expect(screen.queryByText(/selects its own model/)).not.toBeInTheDocument()
  })

  it('writes the selected backend through agent.acp_backend', async () => {
    seed({ acp_backend: '', model: 'auto' })
    wrap(<ChatPanel />)
    await openSelect('Agent backend')
    fireEvent.click(screen.getByRole('option', { name: 'Codex' }))
    await waitFor(() => expect(patchConfigMock).toHaveBeenCalledWith('agent.acp_backend', 'codex'))
  })

  it('states the truth when a backend has no model vocabulary', async () => {
    // Not an empty dropdown, and above all not another backend's list.
    seed({ acp_backend: 'codex', model: 'claude-opus-4.8' })
    wrap(<ChatPanel />)
    expect(await screen.findByText(/Codex selects its own model/)).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Default Model' })).not.toBeInTheDocument()
  })
})

/** Wait until the panel has committed to a backend before reading its controls.
 *
 *  While the config query is in flight `acp_backend` reads as '' — the KIRO
 *  backend — so the panel renders the kiro card first and swaps to the adapted
 *  one when the response lands. A bare findByRole resolves against that first
 *  render and asserts on the wrong card. */
async function awaitBackend(label: string) {
  await waitFor(() =>
    expect(screen.getByRole('combobox', { name: 'Agent backend' })).toHaveTextContent(label))
}

describe('ChatPanel — adapted-harness default model', () => {
  beforeEach(() => {
    patchConfigMock.mockClear()
    modelCapabilitiesMock.mockImplementation(() =>
      Promise.resolve({
        backend: 'claude', catalog: 'registry', registry_provider: 'claude_code',
        selectable: true, runtime_switch: true, switch_scope: 'live_session',
        reasoning_effort: true,
      }) as never,
    )
    modelsMock.mockImplementation(() =>
      Promise.resolve([
        { model_name: 'auto', description: 'Default' },
        { model_name: 'opus-4.8-1m', description: 'Opus 4.8' },
      ]) as never,
    )
  })

  it('offers a picker built from THAT backend, not kiro', async () => {
    seed({ acp_backend: 'claude', model: 'claude-opus-4.8' })
    wrap(<ChatPanel />)
    await awaitBackend('Claude Code')
    const names = (await openSelect('Default Model')).map(o => o.textContent)
    expect(names).toContain('opus-4.8-1m')
    // `agent.model` is the KIRO default and must not leak into this namespace.
    expect(names).not.toContain('claude-opus-4.8')
  })

  it('fetches the list under the selected backend key', async () => {
    seed({ acp_backend: 'claude', model: 'claude-opus-4.8' })
    wrap(<ChatPanel />)
    await waitFor(() => expect(modelsMock).toHaveBeenCalledWith('claude'))
  })

  it("writes to that backend's own namespace, never agent.model", async () => {
    seed({ acp_backend: 'claude', model: 'claude-opus-4.8' })
    wrap(<ChatPanel />)
    await awaitBackend('Claude Code')
    await openSelect('Default Model')
    fireEvent.click(screen.getByRole('option', { name: 'opus-4.8-1m' }))
    await waitFor(() =>
      expect(patchConfigMock).toHaveBeenCalledWith('agent.backend_models.claude', 'opus-4.8-1m'))
    expect(patchConfigMock).not.toHaveBeenCalledWith('agent.model', expect.anything())
  })

  it("shows this backend's own saved pick, not the kiro one", async () => {
    seed({
      acp_backend: 'claude',
      model: 'claude-opus-4.8',
      backend_models: { claude: 'opus-4.8-1m' },
    })
    wrap(<ChatPanel />)
    await awaitBackend('Claude Code')
    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: 'Default Model' }))
        .toHaveTextContent('opus-4.8-1m'))
  })

  it('hides the effort row for a backend with no effort control', async () => {
    // Kiro's reasoning-effort semantics are not portable by assumption.
    modelCapabilitiesMock.mockImplementation(() =>
      Promise.resolve({
        backend: 'codex', catalog: 'advertised', registry_provider: '',
        selectable: true, runtime_switch: true, switch_scope: 'live_session',
        reasoning_effort: false,
      }) as never,
    )
    seed({ acp_backend: 'codex', model: 'claude-opus-4.8' })
    wrap(<ChatPanel />)
    await awaitBackend('Codex')
    await waitFor(() =>
      expect(screen.queryByRole('combobox', { name: 'Default Reasoning Effort' }))
        .not.toBeInTheDocument())
  })
})

describe('ChatPanel — default reasoning effort', () => {
  beforeEach(() => {
    patchConfigMock.mockClear()
    seed({ model: 'claude-opus-4.8', reasoning_effort: '' })
  })

  it('offers the model-default sentinel plus every concrete level', async () => {
    wrap(<ChatPanel />)
    const opts = await openSelect('Default Reasoning Effort')
    expect(opts.map(o => o.textContent)).toEqual([
      'Model default',
      'Low',
      'Medium',
      'High',
      'Extra High',
      'Max',
    ])
  })

  it('PATCHes agent.reasoning_effort on selection', async () => {
    wrap(<ChatPanel />)
    await openSelect('Default Reasoning Effort')
    fireEvent.click(screen.getByRole('option', { name: 'Extra High' }))
    await waitFor(() =>
      expect(patchConfigMock).toHaveBeenCalledWith('agent.reasoning_effort', 'xhigh')
    )
  })

  it('clears back to the model default with an empty value, not a sentinel', async () => {
    seed({ model: 'claude-opus-4.8', reasoning_effort: 'high' })
    wrap(<ChatPanel />)
    await openSelect('Default Reasoning Effort')
    fireEvent.click(screen.getByRole('option', { name: 'Model default' }))
    await waitFor(() => expect(patchConfigMock).toHaveBeenCalledWith('agent.reasoning_effort', ''))
  })

  it('is inert when the default model cannot reason', async () => {
    // kiro-cli rejects effort on 'auto' and Haiku. The row stays visible but
    // inert with an explanatory hint, rather than vanishing.
    seed({ model: 'auto', reasoning_effort: '' })
    wrap(<ChatPanel />)
    await expectSelectInert('Default Reasoning Effort')
    expect(screen.getAllByTitle(/reasoning-capable/).length).toBeGreaterThan(0)
    expect(patchConfigMock).not.toHaveBeenCalled()
  })

  it('is inert for a non-reasoning concrete model too', async () => {
    seed({ model: 'claude-haiku-4.5', reasoning_effort: '' })
    wrap(<ChatPanel />)
    await expectSelectInert('Default Reasoning Effort')
    expect(screen.getAllByTitle(/reasoning-capable/).length).toBeGreaterThan(0)
  })

  it('surfaces an error banner when the write fails', async () => {
    patchConfigMock.mockImplementationOnce(() => Promise.reject(new Error('boom')) as never)
    wrap(<ChatPanel />)
    await openSelect('Default Reasoning Effort')
    fireEvent.click(screen.getByRole('option', { name: 'High' }))
    expect(await screen.findByText(/Failed to save default reasoning effort/)).toBeInTheDocument()
  })
})
