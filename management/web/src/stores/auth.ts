import { create } from 'zustand';
import api from '@/lib/api';
import { rsaPsw } from '@/utils/crypto';

interface UserInfo {
  id: string;
  email: string;
  nickname: string | null;
  is_superuser: boolean;
  orgs: Array<{ org_id: string; org_name: string; role: string }>;
}

interface AuthState {
  user: UserInfo | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  fetchMe: () => Promise<void>;
  isLoggedIn: () => boolean;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  loading: false,

  login: async (email: string, password: string) => {
    // RSA-wrap the password before sending — matches RAGFlow's /v1/user/login
    // convention so both panels handle secrets with the same posture.
    const res = await api.post('/auth/login', {
      email,
      password: rsaPsw(password),
    });
    localStorage.setItem('admin_token', res.data.access_token);
    localStorage.setItem('admin_refresh_token', res.data.refresh_token);
    await get().fetchMe();
  },

  logout: () => {
    localStorage.removeItem('admin_token');
    localStorage.removeItem('admin_refresh_token');
    set({ user: null });
    window.location.href = '/admin/login';
  },

  fetchMe: async () => {
    set({ loading: true });
    try {
      const res = await api.get('/auth/me');
      set({ user: res.data, loading: false });
    } catch {
      set({ user: null, loading: false });
    }
  },

  isLoggedIn: () => !!localStorage.getItem('admin_token'),
}));
