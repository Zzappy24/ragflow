/**
 * CUSTOM B2B SaaS — "Forgot password" self-service flow.
 *
 * Front for the upstream backend chain (api/apps/restful_apis/user_api.py):
 *   1. POST /api/v1/auth/password/forgot/captcha?email=  -> PNG image
 *   2. POST /api/v1/auth/password/forgot/otp             { email, captcha }
 *   3. POST /api/v1/auth/password/forgot/otp/verify      { email, otp }  (4 chars A-Z)
 *   4. POST /api/v1/auth/password/reset                  { email, new_password, confirm_new_password } (RSA)
 * The email inbox is the trust anchor (industry-standard reset); backend
 * enforces captcha, hashed OTP with TTL, attempt limits and rate limiting.
 * Style/skeleton cloned from pages/set-password (same standalone layout).
 */
import { rsaPsw } from '@/utils';
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router';

import { Button, ButtonLoading } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

type Step = 'email' | 'otp' | 'reset' | 'done';

export default function ForgotPasswordPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>('email');
  const [email, setEmail] = useState('');
  const [captcha, setCaptcha] = useState('');
  const [captchaUrl, setCaptchaUrl] = useState<string | null>(null);
  const [otp, setOtp] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadCaptcha = useCallback(async () => {
    setError(null);
    setCaptcha('');
    if (!email.includes('@')) {
      setError('Enter your email first.');
      return;
    }
    try {
      const res = await fetch(
        `/api/v1/auth/password/forgot/captcha?email=${encodeURIComponent(email)}`,
        { method: 'POST' },
      );
      const type = res.headers.get('content-type') || '';
      if (type.includes('json')) {
        const body = await res.json().catch(() => null);
        setError(body?.message || 'Unable to load captcha.');
        return;
      }
      const blob = await res.blob();
      setCaptchaUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return URL.createObjectURL(blob);
      });
    } catch {
      setError('Network error while loading captcha.');
    }
  }, [email]);

  useEffect(
    () => () => {
      if (captchaUrl) URL.revokeObjectURL(captchaUrl);
    },
    [captchaUrl],
  );

  const post = async (url: string, payload: Record<string, unknown>) => {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const body = await res.json().catch(() => null);
    if (!res.ok || !body || body.code !== 0) {
      throw new Error(body?.message || `Request failed (${res.status})`);
    }
    return body;
  };

  const submitEmail = async () => {
    if (!email.includes('@') || !captcha || loading) return;
    setLoading(true);
    setError(null);
    try {
      await post('/api/v1/auth/password/forgot/otp', { email, captcha });
      setStep('otp');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed');
      loadCaptcha(); // captcha is single-use server-side
    } finally {
      setLoading(false);
    }
  };

  const submitOtp = async () => {
    if (otp.trim().length < 4 || loading) return;
    setLoading(true);
    setError(null);
    try {
      await post('/api/v1/auth/password/forgot/otp/verify', {
        email,
        otp: otp.trim().toUpperCase(),
      });
      setStep('reset');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed');
    } finally {
      setLoading(false);
    }
  };

  const passwordOk =
    password.length >= 8 && /[A-Z]/.test(password) && /[0-9]/.test(password);

  const submitReset = async () => {
    if (!passwordOk || password !== confirm || loading) return;
    setLoading(true);
    setError(null);
    try {
      await post('/api/v1/auth/password/reset', {
        email,
        new_password: rsaPsw(password),
        confirm_new_password: rsaPsw(confirm),
      });
      setStep('done');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg-base p-4">
      <div className="w-full max-w-md bg-bg-component rounded-2xl shadow-xl border border-border-button p-8">
        <h1 className="text-2xl font-semibold text-text-primary mb-2">
          Reset your password
        </h1>

        {step === 'email' && (
          <div className="flex flex-col gap-4">
            <p className="text-sm text-text-secondary">
              Enter your account email, then the characters from the image. A
              verification code will be sent to your inbox.
            </p>
            <div>
              <label className="text-sm text-text-primary mb-1 block">
                Email
              </label>
              <Input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                onBlur={() =>
                  email.includes('@') && !captchaUrl && loadCaptcha()
                }
                placeholder="you@company.com"
                autoFocus
                disabled={loading}
              />
            </div>
            {captchaUrl && (
              <div>
                <label className="text-sm text-text-primary mb-1 block">
                  Captcha
                </label>
                <img
                  src={captchaUrl}
                  alt="captcha"
                  title="Click to refresh"
                  className="h-16 rounded cursor-pointer border border-border-button mb-2"
                  onClick={loadCaptcha}
                />
                <Input
                  value={captcha}
                  onChange={(e) => setCaptcha(e.target.value.toUpperCase())}
                  onKeyDown={(e) => e.key === 'Enter' && submitEmail()}
                  placeholder="Characters from the image"
                  maxLength={8}
                  disabled={loading}
                />
              </div>
            )}
            {error && <p className="text-sm text-red-500">{error}</p>}
            {loading ? (
              <ButtonLoading className="w-full" />
            ) : captchaUrl ? (
              <Button
                className="w-full"
                onClick={submitEmail}
                disabled={!email.includes('@') || !captcha}
              >
                Send verification code
              </Button>
            ) : (
              <Button
                className="w-full"
                onClick={loadCaptcha}
                disabled={!email.includes('@')}
              >
                Continue
              </Button>
            )}
          </div>
        )}

        {step === 'otp' && (
          <div className="flex flex-col gap-4">
            <p className="text-sm text-text-secondary">
              A 4-character code was sent to{' '}
              <span className="text-text-primary">{email}</span>. It expires in
              a few minutes.
            </p>
            <Input
              value={otp}
              onChange={(e) => setOtp(e.target.value.toUpperCase())}
              onKeyDown={(e) => e.key === 'Enter' && submitOtp()}
              placeholder="Code from the email"
              maxLength={8}
              autoFocus
              disabled={loading}
            />
            {error && <p className="text-sm text-red-500">{error}</p>}
            {loading ? (
              <ButtonLoading className="w-full" />
            ) : (
              <Button
                className="w-full"
                onClick={submitOtp}
                disabled={otp.trim().length < 4}
              >
                Verify code
              </Button>
            )}
          </div>
        )}

        {step === 'reset' && (
          <div className="flex flex-col gap-4">
            <div>
              <label className="text-sm text-text-primary mb-1 block">
                New password
              </label>
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="8+ characters, 1 uppercase, 1 digit"
                autoFocus
                disabled={loading}
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
                onKeyDown={(e) => e.key === 'Enter' && submitReset()}
                disabled={loading}
              />
              {confirm.length > 0 && confirm !== password && (
                <p className="text-xs text-red-500 mt-1">
                  Passwords do not match
                </p>
              )}
            </div>
            {error && <p className="text-sm text-red-500">{error}</p>}
            {loading ? (
              <ButtonLoading className="w-full" />
            ) : (
              <Button
                className="w-full"
                onClick={submitReset}
                disabled={!passwordOk || password !== confirm}
              >
                Reset password
              </Button>
            )}
          </div>
        )}

        {step === 'done' && (
          <div className="flex flex-col gap-4">
            <p className="text-sm text-text-secondary">
              Your password has been reset. You can now sign in with it.
            </p>
            <Button className="w-full" onClick={() => navigate('/login')}>
              Back to sign in
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
