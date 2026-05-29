import { useEffect, useState } from 'react'
import { Link, Outlet, useNavigate, useLocation } from 'react-router-dom'
import { getAdminMe } from '../../api'

const MENU = [
  { path: '/admin/limits', label: '额度规则', roles: ['superadmin', 'risk_admin'] },
  { path: '/admin/switches', label: '平台开关', roles: ['superadmin', 'risk_admin'] },
  { path: '/admin/announcements', label: '公告管理', roles: ['superadmin', 'ops_admin'] },
  { path: '/admin/audit', label: '审计日志', roles: ['superadmin'] },
  { path: '/admin/users', label: '用户管理', roles: ['superadmin', 'ops_admin'] },
  { path: '/admin/configs', label: '平台配置', roles: ['superadmin'] },
  { path: '/admin/orders', label: '订单查询', roles: ['superadmin', 'risk_admin', 'ops_admin'] },
]

export function AdminLayout() {
  const [role, setRole] = useState('')
  const navigate = useNavigate()
  const location = useLocation()

  useEffect(() => {
    const token = localStorage.getItem('admin_token')
    if (!token) {
      navigate('/admin/login')
      return
    }
    const r = localStorage.getItem('admin_role') || ''
    setRole(r)
    getAdminMe().catch(() => {
      localStorage.removeItem('admin_token')
      localStorage.removeItem('admin_role')
      navigate('/admin/login')
    })
  }, [])

  const logout = () => {
    localStorage.removeItem('admin_token')
    localStorage.removeItem('admin_role')
    navigate('/admin/login')
  }

  return (
    <div className="flex min-h-screen bg-gray-950">
      <aside className="w-48 border-r border-gray-800 p-4">
        <Link to="/admin/limits" className="mb-6 block text-lg font-bold text-emerald-400">
          福润管理
        </Link>
        <nav className="flex flex-col gap-1">
          {MENU.filter((m) => m.roles.includes(role)).map((m) => (
            <Link
              key={m.path}
              to={m.path}
              className={`rounded px-3 py-2 text-sm ${
                location.pathname === m.path
                  ? 'bg-emerald-600 text-white'
                  : 'text-gray-400 hover:bg-gray-800'
              }`}
            >
              {m.label}
            </Link>
          ))}
        </nav>
        <button
          onClick={logout}
          className="mt-8 w-full rounded bg-gray-800 py-1.5 text-xs text-gray-400 hover:bg-gray-700"
        >
          退出登录
        </button>
      </aside>
      <main className="flex-1 p-6">
        <Outlet />
      </main>
    </div>
  )
}
