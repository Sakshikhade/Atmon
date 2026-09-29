/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SUPABASE_URL: string
  readonly VITE_SUPABASE_ANON_KEY: string
  readonly VITE_MEDIA_URL: string
  readonly VITE_DETECT_URL: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
