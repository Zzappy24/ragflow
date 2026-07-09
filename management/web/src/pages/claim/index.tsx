import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Alert, Button, Card, Typography } from 'antd';
import { CopyOutlined } from '@ant-design/icons';
import axios from 'axios';

// Public page (no auth, no Layout — mounted outside the auth guard in
// App.tsx like /login). Uses plain axios instead of '@/lib/api': the auth
// interceptor's 401-redirect must never fire here, and this route never
// carries an admin token anyway.
//
// The claim is gated behind an explicit "Révéler" click (not fired on
// mount) so that antispam/URL-scanner bots following the email link don't
// consume the one-time token before the human opens it.
export default function ClaimPage() {
  const [params] = useSearchParams();
  const token = params.get('token') || '';
  const [state, setState] = useState<'idle' | 'loading' | 'done' | 'notfound' | 'retry'>('idle');
  const [result, setResult] = useState<{ plain_key: string; label: string; gateway_url: string | null } | null>(null);

  const claim = () => {
    setState('loading');
    axios.post('/api/admin/public/code/claim', { token })
      .then((r) => { setResult(r.data); setState('done'); })
      .catch((e) => setState(
        e?.response?.status === 503 || e?.response?.status === 429 ? 'retry' : 'notfound',
      ));
  };

  useEffect(() => { if (!token) setState('notfound'); }, [token]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 p-4">
      <Card className="max-w-xl w-full" title="Récupération de votre clé d'accès Code">
        {state === 'idle' && (
          <>
            <p className="mb-4">Cliquez pour révéler votre clé. Elle ne sera affichée qu'UNE seule fois — copiez-la immédiatement.</p>
            <Button type="primary" onClick={claim}>Révéler ma clé</Button>
          </>
        )}
        {state === 'loading' && <Card loading />}
        {state === 'done' && result && (
          <Alert type="success" message={`Clé pour ${result.label} — copiez-la MAINTENANT, elle ne sera plus jamais affichée.`}
            description={
              <>
                <Typography.Paragraph copyable={{ icon: <CopyOutlined /> }} code>{result.plain_key}</Typography.Paragraph>
                {result.gateway_url && (
                  <div>Endpoint à configurer dans Kilo Code / OpenCode / Cline :{' '}
                    <Typography.Text copyable code>{result.gateway_url}</Typography.Text></div>
                )}
              </>
            } />
        )}
        {state === 'retry' && (
          <Alert type="warning" showIcon message="Service momentanément indisponible — votre lien reste valide, réessayez dans quelques minutes."
            action={<Button onClick={claim}>Réessayer</Button>} />
        )}
        {state === 'notfound' && (
          <Alert type="error" showIcon message="Lien invalide, expiré ou déjà utilisé. Contactez votre administrateur." />
        )}
      </Card>
    </div>
  );
}
