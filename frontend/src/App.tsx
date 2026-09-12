import { Link, Route, Routes } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import Editor from './pages/Editor'

export default function App() {
  return (
    <div className="min-h-full">
      <header className="border-b border-line px-6 py-4 flex items-center justify-between">
        <Link to="/" className="flex items-baseline gap-2">
          <span className="text-lg font-semibold tracking-tight">FÁBRICA</span>
          <span className="text-lg font-semibold tracking-tight text-signal">DE VÍDEOS</span>
        </Link>
        <span className="font-mono text-xs text-muted">v0.1 — demo local</span>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/projects/:id" element={<Editor />} />
        </Routes>
      </main>
    </div>
  )
}
