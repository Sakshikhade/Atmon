/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SUPABASE_URL: string
  readonly VITE_SUPABASE_ANON_KEY: string
  /** Optional legacy media host; omit for on-device-only clips. */
  readonly VITE_MEDIA_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
