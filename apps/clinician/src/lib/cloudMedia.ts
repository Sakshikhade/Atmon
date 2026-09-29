import { supabase } from "./supabase"

/** Optional legacy media host. Leave unset unless a separate media API is running. */
const mediaUrl = (import.meta.env.VITE_MEDIA_URL || "").replace(/\/$/, "")

async function token(): Promise<string | null> {
  const { data } = await supabase.auth.getSession()
  return data.session?.access_token ?? null
}

export async function playbackUrl(sessionId: string, eventId: string): Promise<{ url: string | null; message: string | null }> {
  const missing = "This clip stays on the family's phone. Nothing has been uploaded."
  if (!mediaUrl) return { url: null, message: missing }
  const access = await token()
  if (!access) return { url: null, message: missing }
  try {
    const response = await fetch(`${mediaUrl}/media/sessions/${sessionId}?event=${eventId}`, {
      headers: { Authorization: `Bearer ${access}` },
    })
    if (response.status === 404) return { url: null, message: missing }
    if (!response.ok) {
      const body = (await response.json().catch(() => ({}))) as { error?: string }
      return { url: null, message: body.error ?? "The clip is stored, but the player could not open it." }
    }
    const body = (await response.json()) as { url?: string }
    if (!body.url) return { url: null, message: "The clip is stored, but the player could not open it." }
    return { url: body.url, message: null }
  } catch {
    return { url: null, message: "The clip is stored, but the player could not open it." }
  }
}

export async function playbackStillOpen(sessionId: string): Promise<boolean> {
  if (!mediaUrl) return false
  const access = await token()
  if (!access) return false
  try {
    const response = await fetch(`${mediaUrl}/media/sessions/${sessionId}/status`, {
      headers: { Authorization: `Bearer ${access}` },
    })
    return response.ok
  } catch {
    return true
  }
}

async function savePdf(response: Response, filename: string): Promise<string | null> {
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { error?: string }
    return body.error ?? "The export did not start."
  }
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
  return null
}

export async function downloadExport(grantId: string): Promise<string | null> {
  if (!mediaUrl) return "Cloud export is not configured for this build."
  const access = await token()
  if (!access) return "Sign in again."
  try {
    const response = await fetch(`${mediaUrl}/exports`, {
      method: "POST",
      headers: { Authorization: `Bearer ${access}`, "Content-Type": "application/json" },
      body: JSON.stringify({ grantId }),
    })
    return await savePdf(response, "atmon-log.pdf")
  } catch {
    return "The export did not start."
  }
}

export async function downloadStoredExport(exportId: string): Promise<string | null> {
  if (!mediaUrl) return "Cloud export is not configured for this build."
  const access = await token()
  if (!access) return "Sign in again."
  try {
    const response = await fetch(`${mediaUrl}/exports/${exportId}`, {
      headers: { Authorization: `Bearer ${access}` },
    })
    return await savePdf(response, "atmon-log.pdf")
  } catch {
    return "The export could not be opened."
  }
}
