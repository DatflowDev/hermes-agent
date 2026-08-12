import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useState } from 'react'

import { useSessionView } from '@/app/chat/session-view'
import { useGatewayRequest } from '@/app/gateway/hooks/use-gateway-request'
import { Codicon } from '@/components/ui/codicon'

interface AgentDefinitionProjection {
  definition_id: string
  name: string
  description: string
  identity: 'profile' | 'replace'
  provider: string | null
  model: string | null
  fallback_count: number
  tools_allow: string[] | null
  mcp_allow: string[] | null
  relative_path: string
  digest: string
}

interface AgentDefinitionsResponse {
  version: number
  revision: string
  definitions: AgentDefinitionProjection[]
}

export function AgentDefinitions() {
  const { requestGateway } = useGatewayRequest()
  const sessionId = useStore(useSessionView().$runtimeId)
  const [definitions, setDefinitions] = useState<AgentDefinitionProjection[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState('')
  const [tasks, setTasks] = useState<Record<string, string>>({})
  const [launching, setLaunching] = useState('')
  const [notice, setNotice] = useState('')
  const [noticeIsError, setNoticeIsError] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')

    try {
      const result = await requestGateway<AgentDefinitionsResponse>('agent_definitions.list', { session_id: sessionId })
      setDefinitions(result.definitions ?? [])
      setRevision(result.revision ?? '')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setLoading(false)
    }
  }, [requestGateway, sessionId])

  const launch = useCallback(
    async (definition: AgentDefinitionProjection) => {
      const task = (tasks[definition.definition_id] ?? '').trim()
      if (!sessionId || !task) {
        return
      }

      setLaunching(definition.definition_id)
      setNotice('')
      setNoticeIsError(false)

      try {
        await requestGateway('agent_definitions.launch', {
          definition_id: definition.definition_id,
          digest: definition.digest,
          request_id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
          revision,
          session_id: sessionId,
          task
        })
        setNotice(`${definition.name} launched`)
        setTasks(current => ({ ...current, [definition.definition_id]: '' }))
      } catch (cause) {
        setNoticeIsError(true)
        setNotice(cause instanceof Error ? cause.message : String(cause))
      } finally {
        setLaunching('')
      }
    },
    [requestGateway, revision, sessionId, tasks]
  )

  useEffect(() => {
    void load()
  }, [load])

  if (loading) {
    return <p aria-live="polite" className="py-8 text-center text-xs text-muted-foreground">Loading definitions…</p>
  }

  if (error) {
    return (
      <div className="grid place-items-center gap-3 py-8 text-center" role="alert">
        <p className="text-sm text-destructive">Could not load agent definitions.</p>
        <p className="text-xs text-muted-foreground">{error}</p>
        <button className="text-xs text-foreground underline underline-offset-4" onClick={() => void load()} type="button">
          Retry
        </button>
      </div>
    )
  }

  if (definitions.length === 0) {
    return (
      <div className="grid place-items-center gap-2 py-10 text-center">
        <Codicon name="file-code" size="1.4rem" />
        <p className="text-sm font-medium text-foreground/90">No definitions</p>
        <p className="max-w-md text-xs leading-relaxed text-muted-foreground/75">
          Add Markdown definitions below the active profile’s agents directory.
        </p>
      </div>
    )
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      {notice ? (
        <p
          aria-live={noticeIsError ? 'assertive' : 'polite'}
          className={`px-3 py-1 text-xs ${noticeIsError ? 'text-destructive' : 'text-muted-foreground'}`}
          role={noticeIsError ? 'alert' : 'status'}
        >
          {notice}
        </p>
      ) : null}
      <ul className="space-y-1" role="list">
      {definitions.map(definition => {
        const route = [definition.provider, definition.model].filter(Boolean).join(' / ') || 'inherits delegation route'

        return (
          <li className="rounded-md px-3 py-2.5 hover:bg-muted/40" key={`${definition.name}:${definition.digest}`}>
            <div className="flex min-w-0 items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-foreground">{definition.name}</p>
                <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{definition.description}</p>
              </div>
              <span className="shrink-0 rounded border border-border px-1.5 py-0.5 font-mono text-[0.65rem] text-muted-foreground">
                {definition.identity}
              </span>
            </div>
            <p className="mt-2 truncate font-mono text-[0.65rem] text-muted-foreground/75">
              {route} · {definition.fallback_count} fallback{definition.fallback_count === 1 ? '' : 's'} · {definition.relative_path}
            </p>
            <p className="mt-1 truncate font-mono text-[0.65rem] text-muted-foreground/75">
              tools: {definition.tools_allow?.join(', ') ?? 'inherit'} · MCP: {definition.mcp_allow?.join(', ') ?? 'inherit'}
            </p>
            <div className="mt-2 flex gap-2">
              <input
                aria-label={`Task for ${definition.name}`}
                className="min-w-0 flex-1 rounded border border-border bg-background px-2 py-1 text-xs"
                onChange={event => setTasks(current => ({ ...current, [definition.definition_id]: event.target.value }))}
                placeholder="Task for this agent"
                value={tasks[definition.definition_id] ?? ''}
              />
              <button
                className="rounded bg-foreground px-2 py-1 text-xs text-background disabled:opacity-50"
                disabled={!sessionId || !(tasks[definition.definition_id] ?? '').trim() || launching === definition.definition_id}
                onClick={() => void launch(definition)}
                type="button"
              >
                {launching === definition.definition_id ? 'Launching…' : 'Launch'}
              </button>
            </div>
          </li>
        )
      })}
      </ul>
    </div>
  )
}
