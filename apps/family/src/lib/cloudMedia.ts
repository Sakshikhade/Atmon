import { supabase } from "./supabase"

const mediaUrl = import.meta.env.VITE_MEDIA_URL || "http://localhost:8000"

export async function uploadRecording(sessionId: string, blob: Blob): Promise<string | null> {
  const { data } = await supabase.auth.getSession()
  const token = data.session?.access_token
  if (!token) return "Sign in again before the cloud copy."
  try {
    const response = await fetch(`${mediaUrl}/media/sessions/${sessionId}`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": blob.type || "video/webm",
      },
      body: blob,
    })
    if (!response.ok) return "Saved on this phone. The cloud copy did not start."
    return null
  } catch {
    return "Saved on this phone. The cloud copy did not start."
  }
}
