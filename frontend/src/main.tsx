import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import '@fontsource-variable/inter'
import '@fontsource/jetbrains-mono/400.css'
import 'uplot/dist/uPlot.min.css'
import './theme.css'
import App from './App'
import { AuthProvider, RequireAuth } from './lib/auth'
import { LiveProvider } from './lib/ws'
import Device from './pages/Device'
import Login from './pages/Login'
import Overview from './pages/Overview'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // hold the previous render on refetch — no skeleton flash (UIUX §7)
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route
              element={
                <RequireAuth>
                  <LiveProvider>
                    <App />
                  </LiveProvider>
                </RequireAuth>
              }
            >
              <Route path="/" element={<Overview />} />
              <Route path="/device/:id" element={<Device />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
)
