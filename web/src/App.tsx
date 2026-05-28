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
import { ConfigsPage } from './pages/admin/ConfigsPage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/admin/*" element={
          <div className="min-h-screen bg-gray-950 text-gray-100">
            <Routes>
              <Route path="login" element={<AdminLoginPage />} />
              <Route element={<AdminLayout />}>
                <Route path="limits" element={<LimitsPage />} />
                <Route path="switches" element={<SwitchesPage />} />
                <Route path="announcements" element={<AnnouncementsPage />} />
                <Route path="audit" element={<AuditPage />} />
                <Route path="users" element={<AdminUsersPage />} />
                <Route path="configs" element={<ConfigsPage />} />
              </Route>
            </Routes>
          </div>
        } />
        <Route path="*" element={
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
            </Routes>
          </div>
        } />
      </Routes>
    </BrowserRouter>
  )
}
