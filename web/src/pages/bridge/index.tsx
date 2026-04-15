/**
 * Bridge login page — consumed when the admin panel redirects to
 * /?bridge_token=<jwt>
 *
 * Reads the token from the URL, exchanges it with RAGFlow's /v1/user/bridge
 * endpoint, stores auth in localStorage, and redirects to /.
 */
import { Authorization, Token, UserInfo } from '@/constants/authorization';
import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';

export default function BridgePage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const token = searchParams.get('bridge_token');
    if (!token) {
      navigate('/login', { replace: true });
      return;
    }

    (async () => {
      try {
        const res = await fetch('/v1/user/bridge', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token }),
        });

        const body = await res.json().catch(() => null);
        if (!res.ok || !body || body.code !== 0) {
          setError(body?.message || `Bridge login failed (${res.status})`);
          setTimeout(() => navigate('/login', { replace: true }), 2000);
          return;
        }

        // Same pattern as set-password/index.tsx
        const auth = res.headers.get(Authorization);
        if (auth) localStorage.setItem(Authorization, auth);
        if (body.data?.access_token)
          localStorage.setItem(Token, body.data.access_token);
        if (body.data) {
          localStorage.setItem(
            UserInfo,
            JSON.stringify({
              avatar: body.data.avatar,
              name: body.data.nickname,
              email: body.data.email,
            }),
          );
        }
        // Pin the workspace that was encoded in the bridge token
        if (body.data?.active_workspace_id) {
          localStorage.setItem(
            'active_workspace_id',
            body.data.active_workspace_id,
          );
        }

        navigate('/', { replace: true });
      } catch {
        setError('Network error — redirecting to login…');
        setTimeout(() => navigate('/login', { replace: true }), 2000);
      }
    })();
  }, []); // run once on mount

  if (error) {
    return (
      <div className="fixed inset-0 flex items-center justify-center text-destructive">
        {error}
      </div>
    );
  }

  return (
    <div className="fixed inset-0 flex items-center justify-center">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
    </div>
  );
}
