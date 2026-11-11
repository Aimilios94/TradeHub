export type Source = {
  id: string
  name: string
  kinds: string[]
  role: string
  status: 'working' | 'fallback' | 'pending' | 'untested' | 'dropped'
  enabled: boolean
  needs_key: boolean
  version_introduced: string
  last_success_at: string | null
  last_error_at: string | null
  last_error: string | null
  last_verified_on: string | null
  notes: string | null
}

export type Check = { ok: boolean; error?: string; schema_revision?: string }
export type Deps = Record<'database' | 'searxng' | 'ollama', Check>

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path)
  if (!response.ok) throw new Error(`${path} returned HTTP ${response.status}`)
  return response.json() as Promise<T>
}
