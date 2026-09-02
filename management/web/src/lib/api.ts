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

// Sessions opaques (2026-09-02) : un seul token révocable serveur — plus de
// refresh JWT. Sur 401 : la session est morte (expirée 24 h ou révoquée),
// on purge et on repasse par le login.
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('admin_token');
      localStorage.removeItem('admin_refresh_token');
      window.location.href = '/admin/login';
    }
    return Promise.reject(error);
  },
);

export default api;
