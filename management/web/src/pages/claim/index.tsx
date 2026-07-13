import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Alert, Button, Card, Tabs, Typography } from 'antd';
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

interface ClaimResult {
  plain_key: string;
  label: string;
  gateway_url: string | null;
  models?: string[];
}

const Snippet = ({ text }: { text: string }) => (
  <Typography.Paragraph copyable={{ icon: <CopyOutlined />, text }}>
    <pre className="bg-gray-100 rounded p-3 text-xs overflow-x-auto whitespace-pre-wrap">{text}</pre>
  </Typography.Paragraph>
);

// Configs copy-paste par outil. La clé et l'URL sont injectées : le dev
// repart avec une config qui marche, pas avec une clé brute à deviner.
function QuickStart({ result }: { result: ClaimResult }) {
  const gw = result.gateway_url ?? 'https://<gateway>/v1';
  const model = result.models?.[0] ?? '<model>';
  const key = result.plain_key;

  const kiloJson = JSON.stringify({
    $schema: 'https://app.kilo.ai/config.json',
    provider: {
      cyllene: {
        name: 'cyllene',
        npm: '@ai-sdk/openai-compatible',
        options: { baseURL: gw, apiKey: key },
        models: { [model]: { name: model } },
      },
    },
  }, null, 2);

  const clineSteps = [
    'Dans VS Code, ouvrez les réglages de l\'extension Cline.',
    'API Provider : « OpenAI Compatible ».',
    `Base URL : ${gw}`,
    'API Key : votre clé ci-dessus.',
    `Model ID : ${model}`,
  ].join('\n');

  const opencode = JSON.stringify({
    provider: {
      cyllene: {
        npm: '@ai-sdk/openai-compatible',
        name: 'Cyllene Code',
        options: { baseURL: gw, apiKey: key },
        models: { [model]: { name: model } },
      },
    },
  }, null, 2);

  const curl = [
    `curl ${gw}/chat/completions \\`,
    `  -H "Authorization: Bearer ${key}" \\`,
    '  -H "Content-Type: application/json" \\',
    `  -d '{"model":"${model}","messages":[{"role":"user","content":"dis bonjour"}]}'`,
  ].join('\n');

  return (
    <Card size="small" title="Bien démarrer" className="mt-4">
      <Tabs
        size="small"
        items={[
          {
            key: 'kilo', label: 'Kilo Code',
            children: (
              <>
                <div className="text-gray-500 text-xs mb-2">
                  À fusionner dans votre config Kilo (<code>~/.config/kilo/config.json</code>) :
                </div>
                <Snippet text={kiloJson} />
              </>
            ),
          },
          { key: 'cline', label: 'Cline', children: <Snippet text={clineSteps} /> },
          {
            key: 'opencode', label: 'OpenCode',
            children: (
              <>
                <div className="text-gray-500 text-xs mb-2">
                  À fusionner dans <code>~/.config/opencode/opencode.json</code> :
                </div>
                <Snippet text={opencode} />
              </>
            ),
          },
          { key: 'curl', label: 'Tester (curl)', children: <Snippet text={curl} /> },
        ]}
      />
      <div className="text-gray-400 text-xs">
        Votre clé est personnelle : ne la partagez pas, ne la commitez pas. En cas de doute,
        demandez une rotation à votre administrateur.
      </div>
    </Card>
  );
}

export default function ClaimPage() {
  const [params] = useSearchParams();
  const token = params.get('token') || '';
  const [state, setState] = useState<'idle' | 'loading' | 'done' | 'notfound' | 'retry'>('idle');
  const [result, setResult] = useState<ClaimResult | null>(null);

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
          <>
            <Alert type="success" message={`Clé pour ${result.label} — copiez-la MAINTENANT, elle ne sera plus jamais affichée.`}
              description={
                <>
                  <Typography.Paragraph copyable={{ icon: <CopyOutlined /> }} code>{result.plain_key}</Typography.Paragraph>
                  {result.gateway_url && (
                    <div>Endpoint :{' '}
                      <Typography.Text copyable code>{result.gateway_url}</Typography.Text></div>
                  )}
                </>
              } />
            <QuickStart result={result} />
          </>
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
