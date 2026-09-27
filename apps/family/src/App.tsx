import { useCallback, useEffect, useState } from "react"
import type { Session } from "@supabase/supabase-js"
import { FamilyApp, Intro, SignIn, Splash } from "./FamilyApp"
import { flushOutbox, loadFamily } from "./lib/api"
import { uploadLocalRecordings } from "./lib/cloudMedia"
import type { FamilyData } from "./lib/model"
import { supabase } from "./lib/supabase"

export function App() {
  const [session, setSession] = useState<Session | null | undefined>(undefined)
  const [gate, setGate] = useState<"intro" | "signin">("intro")
  const [signedOut, setSignedOut] = useState(false)
  const [splash, setSplash] = useState(true)
  const [data, setData] = useState<FamilyData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    void supabase.auth.getSession().then(({ data: auth }) => setSession(auth.session))
    const { data: subscription } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next)
      if (!next) setData(null)
    })
    return () => subscription.subscription.unsubscribe()
  }, [])

  useEffect(() => {
    if (!session) return
    let cancel = false
    setError(null)
    flushOutbox()
      .catch(() => undefined)
      .then(() => loadFamily(session.user.id))
      .then((loaded) => {
        if (!cancel) setData(loaded)
        return uploadLocalRecordings().catch(() => 0)
      })
      .then((count) => {
        if (cancel || !count) return
        return loadFamily(session.user.id).then((loaded) => {
          if (!cancel) setData(loaded)
        })
      })
      .catch((err: unknown) => {
        if (!cancel) setError(err instanceof Error ? err.message : "Could not load the log.")
      })
    return () => {
      cancel = true
    }
  }, [session])

  useEffect(() => {
    if (!session) return
    const sync = () => {
      void flushOutbox()
        .catch(() => undefined)
        .then(() => loadFamily(session.user.id))
        .then(setData)
        .catch(() => undefined)
    }
    window.addEventListener("online", sync)
    return () => window.removeEventListener("online", sync)
  }, [session])

  const reload = useCallback(async () => {
    const { data: auth } = await supabase.auth.getUser()
    if (!auth.user) return
    await flushOutbox().catch(() => undefined)
    setData(await loadFamily(auth.user.id))
  }, [])

  const finishSplash = useCallback(() => setSplash(false), [])

  if (session === undefined) return null

  if (!session) {
    if (gate === "signin") {
      return (
        <SignIn
          onSubmit={async (email, password) => {
            const { error: signError } = await supabase.auth.signInWithPassword({ email, password })
            if (!signError) setSplash(true)
            return signError?.message ?? null
          }}
        />
      )
    }
    return <Intro signedOut={signedOut} onStart={() => setGate("signin")} />
  }

  if (splash) return <Splash onDone={finishSplash} />
  if (error) {
    return (
      <div className="intro-scrim">
        <div className="intro">
          <h1 className="h2">The log didn't load</h1>
          <p className="muted mt12">{error}</p>
        </div>
      </div>
    )
  }
  if (!data) {
    return (
      <div className="intro-scrim">
        <div className="intro">
          <p className="muted">Loading the log…</p>
        </div>
      </div>
    )
  }

  return (
    <FamilyApp
      data={data}
      reload={reload}
      onSignOut={() => {
        setSignedOut(true)
        setGate("intro")
        setSplash(true)
        void supabase.auth.signOut()
      }}
    />
  )
}
