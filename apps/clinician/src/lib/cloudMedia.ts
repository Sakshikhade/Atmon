import { supabase } from "./supabase"

const mediaUrl = import.meta.env.VITE_MEDIA_URL || "http://localhost:8000"

export async function playbackUrl(sessionId: string): Promise<string | null> {
  const { data } = await supabase.auth.getSession()
  const token = data.session?.access_token
  if (!token) return null
  try {
    const response = await fetch(`${mediaUrl}/media/sessions/${sessionId}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!response.ok) return null
    const body = (await response.json()) as { url?: string }
    return body.url ?? null
  } catch {
    return null
  }
}
