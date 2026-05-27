import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { LeaderboardPage } from './pages/LeaderboardPage'
import { LoginPage } from './pages/LoginPage'
import { RegisterPage } from './pages/RegisterPage'
import { StrategiesPage } from './pages/StrategiesPage'
import { TasksPage } from './pages/TasksPage'
import { PositionsPage } from './pages/PositionsPage'
import { SettingsPage } from './pages/SettingsPage'
import { Header } from './components/Header'
import { AdminLoginPage } from './pages/admin/AdminLoginPage'
import { AdminLayout } from './pages/admin/AdminLayout'
import { LimitsPage } from './pages/admin/LimitsPage'
import { SwitchesPage } from './pages/admin/SwitchesPage'
import { AnnouncementsPage } from './pages/admin/AnnouncementsPage'
import { AuditPage } from './pages/admin/AuditPage'
import { AdminUsersPage } from './pages/admin/UsersPage'

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-gray-950 text-gray-100">
        <Header />
        <Routes>
          <Route path="/" element={<LeaderboardPage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/strategies" element={<StrategiesPage />} />
          <Route path="/tasks" element={<TasksPage />} />
          <Route path="/positions" element={<PositionsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/admin/login" element={<AdminLoginPage />} />
          <Route path="/admin" element={<AdminLayout />}>
            <Route path="limits" element={<LimitsPage />} />
            <Route path="switches" element={<SwitchesPage />} />
            <Route path="announcements" element={<AnnouncementsPage />} />
            <Route path="audit" element={<AuditPage />} />
            <Route path="users" element={<AdminUsersPage />} />
          </Route>
        </Routes>
      </div>
    </BrowserRouter>
  )
}
