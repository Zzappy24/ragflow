/**
 * Bridge login page — consumed when the admin panel redirects to
 * /?bridge_code=<opaque>
 *
 * Reads the token from the URL, exchanges it with RAGFlow's /v1/user/bridge
 * endpoint, stores auth in localStorage, and redirects to /.
 */
import { Authorization, Token, UserInfo } from '@/constants/authorization';
import { getAuthorization } from '@/utils/authorization-util';
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';

export default function BridgePage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const called = useRef(false);

  useEffect(() => {
    // Guard against React StrictMode double-invocation in dev.
    if (called.current) return;
    called.current = true;

    const code = searchParams.get('bridge_code');
    if (!code) {
      navigate('/login', { replace: true });
      return;
    }

    (async () => {
      try {
        const res = await fetch('/v1/user/bridge', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code }),
        });

        const body = await res.json().catch(() => null);

        if (!res.ok || !body || body.code !== 0) {
          // "already used" can happen in React StrictMode (double effect) when
          // the first call already succeeded and stored auth in localStorage.
          // If we're already authenticated, just proceed silently.
          if (getAuthorization()) {
            navigate('/', { replace: true });
            return;
          }
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
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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
