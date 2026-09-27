import { supabase } from "./supabase"

const mediaUrl = import.meta.env.VITE_MEDIA_URL || "http://localhost:8000"

export async function playbackUrl(sessionId: string): Promise<{ url: string | null; message: string | null }> {
  const missing = "This clip stays on the family's phone. Nothing has been uploaded."
  const { data } = await supabase.auth.getSession()
  const token = data.session?.access_token
  if (!token) return { url: null, message: missing }
  try {
    const response = await fetch(`${mediaUrl}/media/sessions/${sessionId}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (response.status === 404) return { url: null, message: missing }
    if (!response.ok) return { url: null, message: "The clip is stored, but the player could not open it." }
    const body = (await response.json()) as { url?: string }
    if (!body.url) return { url: null, message: "The clip is stored, but the player could not open it." }
    return { url: body.url, message: null }
  } catch {
    return { url: null, message: "The clip is stored, but the player could not open it." }
  }
}
