/**
 * Initial-password setup page.
 *
 * Landing target of the admin-panel "invite" email. Reads the ``invite_token``
 * query param (minted by ``POST /api/admin/users`` in the management server)
 * and POSTs it along with the user's chosen password to
 * ``/api/v1/set_initial_password`` on RAGFlow.
 *
 * On success:
 *   - Backend returns an ``Authorization`` header + user JSON body
 *   - We persist both to ``localStorage`` (same keys the regular login uses)
 *   - We redirect to ``/`` — the authenticated root layout
 *
 * On failure we surface the backend's error message (expired / reused /
 * malformed token) and block submission.
 */
import { Authorization, Token, UserInfo } from '@/constants/authorization';
import { rsaPsw } from '@/utils';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router';

import { Button, ButtonLoading } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

export default function SetPasswordPage() {
  const navigate = useNavigate();
  const token = useMemo(() => {
    if (typeof window === 'undefined') return '';
    return new URL(window.location.href).searchParams.get('invite_code') ?? '';
  }, []);

  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token)
      setError('Missing invite code. Ask your admin to re-send the invite.');
  }, [token]);

  const canSubmit =
    !!token && password.length >= 8 && password === confirm && !loading;

  const onSubmit = async () => {
    if (!canSubmit) return;
    setError(null);
    setLoading(true);
    try {
      const res = await fetch('/api/v1/set_initial_password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          code: token,
          password: rsaPsw(password),
        }),
      });

      const body = await res.json().catch(() => null);
      // RAGFlow wraps responses as { code, data, message }; code === 0 is success.
      if (!res.ok || !body || body.code !== 0) {
        setError(body?.message || `Request failed (${res.status})`);
        setLoading(false);
        return;
      }

      const auth = res.headers.get(Authorization);
      if (auth) localStorage.setItem(Authorization, auth);
      if (body.data?.access_token)
        localStorage.setItem(Token, body.data.access_token);
      if (body.data) {
        const userInfo = {
          avatar: body.data.avatar,
          name: body.data.nickname,
          email: body.data.email,
        };
        localStorage.setItem(UserInfo, JSON.stringify(userInfo));
      }
      // Pin the workspace so X-Workspace-Id is sent on all subsequent requests.
      if (body.data?.default_workspace_id) {
        localStorage.setItem(
          'active_workspace_id',
          body.data.default_workspace_id,
        );
      }

      navigate('/');
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Network error');
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg-base p-4">
      <div className="w-full max-w-md bg-bg-component rounded-2xl shadow-xl border border-border-button p-8">
        <h1 className="text-2xl font-semibold text-text-primary mb-2">
          Set your password
        </h1>
        <p className="text-sm text-text-secondary mb-6">
          Welcome. Choose a password to activate your account.
        </p>

        <div className="flex flex-col gap-4">
          <div>
            <label className="text-sm text-text-primary mb-1 block">
              New password
            </label>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="At least 8 characters"
              autoFocus
              disabled={!token || loading}
            />
          </div>

          <div>
            <label className="text-sm text-text-primary mb-1 block">
              Confirm password
            </label>
            <Input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') onSubmit();
              }}
              disabled={!token || loading}
            />
            {confirm.length > 0 && confirm !== password && (
              <p className="text-xs text-red-500 mt-1">
                Passwords do not match
              </p>
            )}
          </div>

          {error && (
            <p
              className="text-sm text-red-500"
              data-testid="set-password-error"
            >
              {error}
            </p>
          )}

          {loading ? (
            <ButtonLoading className="w-full" />
          ) : (
            <Button className="w-full" onClick={onSubmit} disabled={!canSubmit}>
              Activate account
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
