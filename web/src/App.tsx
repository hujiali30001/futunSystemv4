import { lazy, Suspense } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { Header } from './components/Header'
import { AdminLayout } from './pages/admin/AdminLayout'

const DashboardPage = lazy(() => import('./pages/DashboardPage').then(m => ({ default: m.DashboardPage })))
const LeaderboardPage = lazy(() => import('./pages/LeaderboardPage').then(m => ({ default: m.LeaderboardPage })))
const LoginPage = lazy(() => import('./pages/LoginPage').then(m => ({ default: m.LoginPage })))
const RegisterPage = lazy(() => import('./pages/RegisterPage').then(m => ({ default: m.RegisterPage })))
const StrategiesPage = lazy(() => import('./pages/StrategiesPage').then(m => ({ default: m.StrategiesPage })))
const TasksPage = lazy(() => import('./pages/TasksPage').then(m => ({ default: m.TasksPage })))
const PositionsPage = lazy(() => import('./pages/PositionsPage').then(m => ({ default: m.PositionsPage })))
const SettingsPage = lazy(() => import('./pages/SettingsPage').then(m => ({ default: m.SettingsPage })))
const AdminLoginPage = lazy(() => import('./pages/admin/AdminLoginPage').then(m => ({ default: m.AdminLoginPage })))
const LimitsPage = lazy(() => import('./pages/admin/LimitsPage').then(m => ({ default: m.LimitsPage })))
const SwitchesPage = lazy(() => import('./pages/admin/SwitchesPage').then(m => ({ default: m.SwitchesPage })))
const AnnouncementsPage = lazy(() => import('./pages/admin/AnnouncementsPage').then(m => ({ default: m.AnnouncementsPage })))
const AuditPage = lazy(() => import('./pages/admin/AuditPage').then(m => ({ default: m.AuditPage })))
const AdminUsersPage = lazy(() => import('./pages/admin/UsersPage').then(m => ({ default: m.AdminUsersPage })))
const ConfigsPage = lazy(() => import('./pages/admin/ConfigsPage').then(m => ({ default: m.ConfigsPage })))
const OrdersPage = lazy(() => import('./pages/admin/OrdersPage').then(m => ({ default: m.OrdersPage })))

function PageLoader() {
  return (
    <div className="flex items-center justify-center py-20">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-700 border-t-emerald-500" />
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/admin/*" element={
          <div className="min-h-screen bg-gray-950 text-gray-100">
            <Routes>
              <Route path="login" element={<Suspense fallback={<PageLoader />}><AdminLoginPage /></Suspense>} />
              <Route element={<AdminLayout />}>
                <Route path="limits" element={<Suspense fallback={<PageLoader />}><LimitsPage /></Suspense>} />
                <Route path="switches" element={<Suspense fallback={<PageLoader />}><SwitchesPage /></Suspense>} />
                <Route path="announcements" element={<Suspense fallback={<PageLoader />}><AnnouncementsPage /></Suspense>} />
                <Route path="audit" element={<Suspense fallback={<PageLoader />}><AuditPage /></Suspense>} />
                <Route path="users" element={<Suspense fallback={<PageLoader />}><AdminUsersPage /></Suspense>} />
                <Route path="configs" element={<Suspense fallback={<PageLoader />}><ConfigsPage /></Suspense>} />
                <Route path="orders" element={<Suspense fallback={<PageLoader />}><OrdersPage /></Suspense>} />
              </Route>
            </Routes>
          </div>
        } />
        <Route path="*" element={
          <div className="min-h-screen bg-gray-950 text-gray-100">
            <Header />
            <Routes>
              <Route path="/" element={<Suspense fallback={<PageLoader />}><DashboardPage /></Suspense>} />
              <Route path="/leaderboard" element={<Suspense fallback={<PageLoader />}><LeaderboardPage /></Suspense>} />
              <Route path="/login" element={<Suspense fallback={<PageLoader />}><LoginPage /></Suspense>} />
              <Route path="/register" element={<Suspense fallback={<PageLoader />}><RegisterPage /></Suspense>} />
              <Route path="/strategies" element={<Suspense fallback={<PageLoader />}><StrategiesPage /></Suspense>} />
              <Route path="/tasks" element={<Suspense fallback={<PageLoader />}><TasksPage /></Suspense>} />
              <Route path="/positions" element={<Suspense fallback={<PageLoader />}><PositionsPage /></Suspense>} />
              <Route path="/settings" element={<Suspense fallback={<PageLoader />}><SettingsPage /></Suspense>} />
            </Routes>
          </div>
        } />
      </Routes>
    </BrowserRouter>
  )
}
