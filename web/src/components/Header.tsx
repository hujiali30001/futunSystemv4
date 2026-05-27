import { Link, useNavigate } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { getMe, type UserInfo } from '../api'

export function Header() {
  const [user, setUser] = useState<UserInfo | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    const token = localStorage.getItem('token')
    if (!token) return
    getMe()
      .then(setUser)
      .catch(() => localStorage.removeItem('token'))
  }, [])

  const handleLogout = () => {
    localStorage.removeItem('token')
    setUser(null)
    navigate('/')
  }

  return (
    <header className="border-b border-gray-800 bg-gray-900">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3">
        <div className="flex items-center gap-6">
          <Link to="/" className="text-xl font-bold text-emerald-400">
            福润
          </Link>
          <nav className="flex gap-4 text-sm text-gray-400">
            <Link to="/" className="hover:text-white">
              排行榜
            </Link>
            {user && (
              <>
                <Link to="/strategies" className="hover:text-white">
                  我的策略
                </Link>
                <Link to="/tasks" className="hover:text-white">
                  任务
                </Link>
                <Link to="/positions" className="hover:text-white">持仓</Link>
                <Link to="/settings" className="hover:text-white">设置</Link>
              </>
            )}
          </nav>
        </div>
        <div>
          {user ? (
            <div className="flex items-center gap-3">
              <span className="text-sm text-gray-300">{user.username}</span>
              <button
                onClick={handleLogout}
                className="rounded px-3 py-1 text-sm text-gray-400 hover:bg-gray-800"
              >
                退出
              </button>
            </div>
          ) : (
            <div className="flex gap-2">
              <Link
                to="/login"
                className="rounded bg-emerald-600 px-4 py-1.5 text-sm font-medium hover:bg-emerald-500"
              >
                登录
              </Link>
              <Link
                to="/register"
                className="rounded border border-gray-700 px-4 py-1.5 text-sm text-gray-300 hover:bg-gray-800"
              >
                注册
              </Link>
            </div>
          )}
        </div>
      </div>
    </header>
  )
}
