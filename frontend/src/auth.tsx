import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, getToken, setToken, type Me } from './api'

interface AuthState { me: Me | null; loading: boolean; can: (permission: string) => boolean; signOut: () => void }

const AuthContext = createContext<AuthState>({ me: null, loading: true, can: () => false, signOut: () => {} })

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const [token, setLocal] = useState(getToken())
  useEffect(() => {
    const onChange = () => { setLocal(getToken()); qc.clear() }
    window.addEventListener('auth-changed', onChange)
    return () => window.removeEventListener('auth-changed', onChange)
  }, [qc])
  const me = useQuery({ queryKey: ['me', token], queryFn: () => api.get<Me>('/api/auth/me'), enabled: !!token, retry: false })
  const value: AuthState = {
    me: token ? me.data ?? null : null,
    loading: !!token && me.isLoading,
    can: p => !!me.data?.permissions.includes(p),
    signOut: () => setToken(null),
  }
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export const useAuth = () => useContext(AuthContext)
