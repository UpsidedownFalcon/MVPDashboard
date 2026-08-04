// Login (UIUX §2): full-black brand moment. Inline error never says which
// field was wrong. Already-authed visits bounce to /.

import { useState, type FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { LogoMark } from '../components/Logo'
import { ApiError } from '../lib/api'
import { useAuth } from '../lib/auth'
import '../app.css'

export default function Login() {
  const { user, ready, login } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (ready && user) return <Navigate to="/" replace />

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(username, password)
      navigate('/', { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setError('Too many attempts — wait a minute and try again.')
      } else {
        setError('Invalid username or password')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login">
      <div className="login-glow" aria-hidden />
      <form className="login-card" onSubmit={(e) => void submit(e)}>
        <LogoMark className="login-mark" />
        <h1 className="login-title">HIPPOS</h1>
        <div className="eyebrow login-eyebrow">Motion Intelligence</div>
        <label className="login-label" htmlFor="username">
          Username
        </label>
        <input
          id="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          autoFocus
          required
        />
        <label className="login-label" htmlFor="password">
          Password
        </label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          required
        />
        {error && (
          <div className="login-error" role="alert">
            {error}
          </div>
        )}
        <button className="login-submit" type="submit" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
