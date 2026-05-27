import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { adminLogin } from '../../api'

export function AdminLoginPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const navigate = useNavigate()

  const handleLogin = async () => {
    try {
      const res = await adminLogin(username, password)
      localStorage.setItem('admin_token', res.access_token)
      localStorage.setItem('admin_role', res.role)
      navigate('/admin/limits')
    } catch {
      setError('登录失败')
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-950">
      <div className="w-96 rounded-lg border border-gray-800 p-6">
        <h2 className="mb-4 text-center text-xl font-bold text-emerald-400">管理后台</h2>
        {error && <p className="mb-3 text-center text-sm text-red-400">{error}</p>}
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="用户名"
          onKeyDown={(e) => e.key === 'Enter' && handleLogin()}
          className="mb-3 w-full rounded bg-gray-800 border border-gray-700 px-3 py-2 text-gray-300"
        />
        <input
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          type="password"
          placeholder="密码"
          onKeyDown={(e) => e.key === 'Enter' && handleLogin()}
          className="mb-4 w-full rounded bg-gray-800 border border-gray-700 px-3 py-2 text-gray-300"
        />
        <button
          onClick={handleLogin}
          className="w-full rounded bg-emerald-600 py-2 font-medium hover:bg-emerald-500"
        >
          登录
        </button>
      </div>
    </div>
  )
}
