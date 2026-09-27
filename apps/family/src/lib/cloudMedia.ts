import { listVideoIds, loadVideo } from "./mediaStore"
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

export async function uploadLocalRecordings(): Promise<number> {
  const ids = await listVideoIds()
  if (ids.length === 0) return 0
  const { data: rows, error } = await supabase.from("sessions").select("id, storage_location").in("id", ids)
  if (error || !rows) return 0
  let uploaded = 0
  for (const row of rows) {
    if (row.storage_location === "cloud") continue
    const blob = await loadVideo(row.id)
    if (!blob) continue
    const cloudError = await uploadRecording(row.id, blob)
    if (!cloudError) uploaded += 1
  }
  return uploaded
}
