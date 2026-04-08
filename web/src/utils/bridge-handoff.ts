/**
 * Bridge auth handoff from the admin panel.
 *
 * When the admin panel issues a "launch workspace" link, the user lands at
 * `/?bridge_token=<jwt>`. This module runs *before* the React app renders:
 *
 *   1. Reads `bridge_token` from the URL (and strips it from history immediately
 *      so it can't leak via Referer or browser history).
 *   2. POSTs it to `/v1/user/bridge`. The backend verifies the JWT, enforces
 *      single-use via Redis SETNX on the jti, re-checks workspace membership,
 *      and logs the user in (returning Authorization in headers + the active
 *      workspace_id in the body).
 *   3. Persists the new auth + active workspace to localStorage so the rest of
 *      the app boots inside the bridged workspace.
 *
 * Failures are logged but non-fatal: the app continues to render normally and
 * the user will hit the login screen if no prior session exists.
 */
import { Authorization, UserInfo } from '@/constants/authorization';

const BRIDGE_PARAM = 'bridge_token';
const ACTIVE_WORKSPACE_KEY = 'active_workspace_id';

export async function consumeBridgeToken(): Promise<void> {
  if (typeof window === 'undefined') return;

  const url = new URL(window.location.href);
  const token = url.searchParams.get(BRIDGE_PARAM);
  if (!token) return;

  // Strip the token from the visible URL immediately to minimise the replay
  // window — even though the backend already enforces single-use via Redis,
  // we don't want it sitting in history or being copy-pasted by accident.
  url.searchParams.delete(BRIDGE_PARAM);
  window.history.replaceState({}, '', url.toString());

  try {
    const res = await fetch('/v1/user/bridge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
      credentials: 'include',
    });

    if (!res.ok) {
      console.warn('[bridge] handoff failed with status', res.status);
      return;
    }

    const auth = res.headers.get(Authorization);
    if (auth) {
      localStorage.setItem(Authorization, auth);
    }

    const body = await res.json().catch(() => null);
    if (body?.data) {
      localStorage.setItem(UserInfo, JSON.stringify(body.data));
      const wsId = body.data.active_workspace_id;
      if (wsId) {
        localStorage.setItem(ACTIVE_WORKSPACE_KEY, wsId);
      }
    }
  } catch (err) {
    console.warn('[bridge] handoff network error', err);
  }
}
