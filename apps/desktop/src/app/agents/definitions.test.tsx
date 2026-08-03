import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const requestGatewayMock = vi.hoisted(() => vi.fn())

vi.mock('@/app/gateway/hooks/use-gateway-request', () => ({
  useGatewayRequest: () => ({ requestGateway: requestGatewayMock })
}))

vi.mock('@/app/chat/session-view', async () => {
  const { atom } = await import('nanostores')
  return { useSessionView: () => ({ $runtimeId: atom('session-1') }) }
})

import { AgentDefinitions } from './definitions'

afterEach(cleanup)

describe('AgentDefinitions', () => {
  beforeEach(() => requestGatewayMock.mockReset())

  it('renders the allowlisted catalog projection', async () => {
    requestGatewayMock.mockResolvedValue({
      version: 1,
      revision: 'abc',
      definitions: [
        {
          definition_id: 'definition-1',
          name: 'researcher',
          description: 'Verify sources',
          identity: 'replace',
          provider: 'provider-a',
          model: 'model-a',
          fallback_count: 1,
          relative_path: 'research/researcher.md',
          digest: 'a'.repeat(64)
        }
      ]
    })

    render(<AgentDefinitions />)

    expect(await screen.findByText('researcher')).toBeTruthy()
    expect(screen.getByText('Verify sources')).toBeTruthy()
    expect(screen.getByText(/provider-a \/ model-a/)).toBeTruthy()
    expect(requestGatewayMock).toHaveBeenCalledWith('agent_definitions.list', { session_id: 'session-1' })
  })

  it('launches through the typed session-bound RPC', async () => {
    requestGatewayMock.mockResolvedValueOnce({
      version: 1,
      revision: 'abc',
      definitions: [
        {
          definition_id: 'definition-1',
          name: 'researcher',
          description: 'Verify sources',
          identity: 'replace',
          provider: null,
          model: null,
          fallback_count: 0,
          relative_path: 'researcher.md',
          digest: 'a'.repeat(64)
        }
      ]
    }).mockResolvedValueOnce({ status: 'dispatched' })

    render(<AgentDefinitions />)
    const taskInput = await screen.findByLabelText('Task for researcher')
    fireEvent.change(taskInput, { target: { value: 'Check the evidence' } })
    fireEvent.click(screen.getByRole('button', { name: 'Launch' }))

    await waitFor(() =>
      expect(requestGatewayMock).toHaveBeenLastCalledWith(
        'agent_definitions.launch',
        expect.objectContaining({
          definition_id: 'definition-1',
          digest: 'a'.repeat(64),
          revision: 'abc',
          session_id: 'session-1',
          task: 'Check the evidence'
        })
      )
    )
  })

  it('renders the empty catalog state', async () => {
    requestGatewayMock.mockResolvedValue({ version: 1, revision: 'empty', definitions: [] })
    render(<AgentDefinitions />)

    expect(await screen.findByText('No definitions')).toBeTruthy()
    expect(screen.queryByText('researcher')).toBeNull()
  })

  it('announces launch failures as alerts', async () => {
    requestGatewayMock.mockResolvedValueOnce({
      version: 1,
      revision: 'abc',
      definitions: [
        {
          definition_id: 'definition-1',
          name: 'researcher',
          description: 'Verify sources',
          identity: 'replace',
          provider: null,
          model: null,
          fallback_count: 0,
          relative_path: 'researcher.md',
          digest: 'a'.repeat(64)
        }
      ]
    }).mockRejectedValueOnce(new Error('routing denied'))

    render(<AgentDefinitions />)
    fireEvent.change(await screen.findByLabelText('Task for researcher'), { target: { value: 'Check' } })
    fireEvent.click(screen.getByRole('button', { name: 'Launch' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('routing denied')
    expect(alert.getAttribute('aria-live')).toBe('assertive')
  })
})
