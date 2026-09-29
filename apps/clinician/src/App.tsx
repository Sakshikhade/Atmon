import { useCallback, useEffect, useState } from "react"
import type { Session } from "@supabase/supabase-js"
import { ClinicianApp } from "./ClinicianApp"
import { acceptInvites, loadClinician } from "./lib/api"
import type { ClinData } from "./lib/model"
import { supabase } from "./lib/supabase"

export function App() {
  const [session, setSession] = useState<Session | null | undefined>(undefined)
  const [data, setData] = useState<ClinData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [signedOut, setSignedOut] = useState(false)

  useEffect(() => {
    void supabase.auth.getSession().then(({ data: auth }) => setSession(auth.session))
    const { data: subscription } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next)
      if (!next) setData(null)
    })
    return () => subscription.subscription.unsubscribe()
  }, [])

  const load = useCallback(async (userId: string, name: string) => {
    const acceptError = await acceptInvites(name)
    if (acceptError) throw new Error(acceptError)
    return loadClinician(userId)
  }, [])

  useEffect(() => {
    if (!session) return
    let cancel = false
    setError(null)
    setData(null)
    const name = session.user.email?.split("@")[0] ?? "Clinician"
    void load(session.user.id, name)
      .then((next) => {
        if (!cancel) setData(next)
      })
      .catch((err: unknown) => {
        if (!cancel) setError(err instanceof Error ? err.message : "Could not open the workspace.")
      })
    return () => {
      cancel = true
    }
  }, [session, load])

  const reload = useCallback(async () => {
    const { data: auth } = await supabase.auth.getUser()
    if (!auth.user) return
    const name = auth.user.email?.split("@")[0] ?? "Clinician"
    setData(await load(auth.user.id, name))
  }, [load])

  if (session === undefined || (session && !data && !error)) {
    return <p className="muted" style={{ padding: 32 }}>Opening the workspace.</p>
  }
  if (!session) return <SignIn signedOut={signedOut} />
  if (error || !data) return <p style={{ padding: 32 }}>{error ?? "Could not open the workspace."}</p>

  return (
    <ClinicianApp
      data={data}
      reload={reload}
      onSignOut={() => {
        setSignedOut(true)
        void supabase.auth.signOut()
      }}
    />
  )
}

function SignIn({ signedOut }: { signedOut: boolean }) {
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  return (
    <div className="login">
      <div className="login-art">
        <div className="semi">atmon for clinicians</div>
        <h1 className="mt24">For autistic children, and the people around them.</h1>
        <p className="mt16" style={{ maxWidth: 420, opacity: 0.9 }}>Review what families choose to share. Every view stays on their terms.</p>
      </div>
      <form
        className="login-form"
        onSubmit={(event) => {
          event.preventDefault()
          setBusy(true)
          setError(null)
          void supabase.auth.signInWithPassword({ email, password }).then(({ error: signError }) => {
            setBusy(false)
            if (signError) setError(signError.message)
          })
        }}
      >
        <h1 className="h1">Sign in</h1>
        {signedOut ? <p className="mt8"><span className="badge ok">Signed out. Nothing you viewed is kept on this device.</span></p> : null}
        <label className="field mt24"><span>Work email</span><input type="email" autoComplete="username" value={email} onChange={(input) => setEmail(input.target.value)} /></label>
        <label className="field mt12"><span>Password</span><input type="password" autoComplete="current-password" value={password} onChange={(input) => setPassword(input.target.value)} /></label>
        {error ? <p className="small mt12" style={{ color: "var(--danger)" }}>{error}</p> : null}
        <button className="btn mt16" type="submit" disabled={busy || !email || !password}>{busy ? "Signing in" : "Sign in"}</button>
      </form>
    </div>
  )
}
