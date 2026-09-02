import axios from 'axios';

const api = axios.create({
  baseURL: '/api/admin',
  timeout: 30000,
});

// Inject auth token
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('admin_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Session 7 jours glissants (2026-09-02) : le backend émet un refresh token
// (7 j) que le front stockait sans JAMAIS l'utiliser → déconnexion toutes
// les 60 min à l'expiration du JWT d'accès. Sur 401 : UNE tentative de
// refresh (single-flight partagée entre requêtes concurrentes), rejeu de la
// requête d'origine, et déconnexion seulement si le refresh échoue.
let refreshing: Promise<string | null> | null = null;

async function tryRefresh(): Promise<string | null> {
  const rt = localStorage.getItem('admin_refresh_token');
  if (!rt) return null;
  try {
    // axios brut : ne pas repasser par les intercepteurs (boucle).
    const res = await axios.post('/api/admin/auth/refresh', { refresh_token: rt });
    localStorage.setItem('admin_token', res.data.access_token);
    localStorage.setItem('admin_refresh_token', res.data.refresh_token);
    return res.data.access_token as string;
  } catch {
    return null;
  }
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;
    if (error.response?.status === 401 && original && !original._retried) {
      original._retried = true;
      refreshing = refreshing ?? tryRefresh().finally(() => { refreshing = null; });
      const token = await refreshing;
      if (token) {
        original.headers.Authorization = `Bearer ${token}`;
        return api(original);
      }
      localStorage.removeItem('admin_token');
      localStorage.removeItem('admin_refresh_token');
      window.location.href = '/admin/login';
    }
    return Promise.reject(error);
  },
);

export default api;
