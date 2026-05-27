import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { LeaderboardPage } from './pages/LeaderboardPage'
import { LoginPage } from './pages/LoginPage'
import { RegisterPage } from './pages/RegisterPage'
import { StrategiesPage } from './pages/StrategiesPage'
import { TasksPage } from './pages/TasksPage'
import { Header } from './components/Header'

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
        </Routes>
      </div>
    </BrowserRouter>
  )
}
