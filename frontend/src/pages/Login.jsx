import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import Wordmark from '../components/Wordmark.jsx'
import { Button, Input } from '../components/index.js'
import './runtime.css'

export default function Login({ onLogin }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const navigate = useNavigate()
  const location = useLocation()

  async function submit(e) {
    e.preventDefault()
    setError(null)
    try {
      const res = await api('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      onLogin({ username: res.username })
      // back to where the redirect came from
      const from = location.state?.from
      navigate(from && from !== '/login' ? from : '/')
    } catch (err) {
      setError(err.detail || 'Sign-in failed. Check the username and password.')
    }
  }

  return (
    <div className="center">
      <form className="login" onSubmit={submit}>
        <h1><Wordmark big /></h1>
        <p className="login-lede">Sign in to your agent server.</p>
        <Input label="Username" value={username} autoComplete="username"
               autoCapitalize="none" spellCheck={false} autoFocus
               onChange={(e) => setUsername(e.target.value)} />
        <Input label="Password" type="password" value={password}
               autoComplete="current-password"
               onChange={(e) => setPassword(e.target.value)} />
        {error && <div className="error" role="alert">{error}</div>}
        <Button type="submit" disabled={!username || !password}>Sign in</Button>
      </form>
    </div>
  )
}
