// App shell (UIUX §1): fixed left sidebar + routed main pane.

import { Outlet } from 'react-router-dom'
import Sidebar from './components/Sidebar'
import './app.css'

export default function App() {
  return (
    <div className="shell">
      <Sidebar />
      <main className="main">
        <Outlet />
      </main>
    </div>
  )
}
