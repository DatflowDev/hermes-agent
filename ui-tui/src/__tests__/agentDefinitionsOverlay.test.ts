import { describe, expect, it } from 'vitest'

import { agentDefinitionLaunchParams, definitionWindow } from '../components/agentsOverlay.js'
import type { AgentDefinitionProjection } from '../gatewayTypes.js'

const definition: AgentDefinitionProjection = {
  definition_id: 'definition-1',
  description: 'Review evidence',
  digest: 'a'.repeat(64),
  fallback_count: 0,
  identity: 'replace',
  model: null,
  name: 'reviewer',
  provider: null,
  relative_path: 'reviewer.md'
}

describe('agent definitions overlay', () => {
  it('keeps the selected definition inside a bounded terminal window', () => {
    expect(definitionWindow(0, 12, 20)).toEqual({ start: 0, visible: 3 })
    expect(definitionWindow(10, 12, 20)).toEqual({ start: 9, visible: 3 })
    expect(definitionWindow(19, 12, 20)).toEqual({ start: 17, visible: 3 })
  })

  it('builds a typed launch bound to session, revision and digest', () => {
    expect(agentDefinitionLaunchParams(definition, 'revision-1', 'session-1', 'Check it', 'request-1')).toEqual({
      definition_id: 'definition-1',
      digest: 'a'.repeat(64),
      request_id: 'request-1',
      revision: 'revision-1',
      session_id: 'session-1',
      task: 'Check it'
    })
  })
})
