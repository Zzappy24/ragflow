import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Alert, Button, Card, Form, Input, Spin, Typography } from 'antd';
import axios from 'axios';

// Page PUBLIQUE d'acceptation d'invitation d'organisation (« tout est
// invitation », zéro-leak) — montée hors du guard d'auth comme /claim et
// /login, et sur axios brut pour les mêmes raisons (pas de redirect 401).
//
// Le backend (GET /org-invites/introspect) dit si l'email a déjà un compte :
// - needs_password=true  → la personne crée son compte (nom + mot de passe)
// - needs_password=false → simple bouton « Accepter »
// Cette distinction n'est visible que de l'INVITÉ, jamais de l'admin.

interface Introspection {
  org_name: string;
  email: string;
  role: string;
  workspace_name?: string | null;
  workspace_role?: string | null;
  needs_password: boolean;
}

export default function OrgInvitePage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token') || '';
  const [intro, setIntro] = useState<Introspection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ org_name: string; ragflow_url: string } | null>(null);
  const [form] = Form.useForm();

  useEffect(() => {
    if (!token) {
      setError('Lien invalide — le paramètre token est manquant.');
      return;
    }
    axios.get('/api/admin/org-invites/introspect', { params: { token } })
      .then((res) => setIntro(res.data))
      .catch((err) => {
        const status = err?.response?.status;
        setError(status === 410
          ? "Cette invitation a expiré — demandez à l'administrateur de la renvoyer."
          : 'Invitation invalide ou déjà utilisée.');
      });
  }, [token]);

  const onAccept = async () => {
    let values: { nickname?: string; password?: string; confirm?: string } = {};
    if (intro?.needs_password) {
      try {
        values = await form.validateFields();
      } catch {
        return;
      }
    }
    setBusy(true);
    try {
      const res = await axios.post('/api/admin/org-invites/accept', {
        token,
        nickname: values.nickname,
        password: values.password,
      });
      setDone(res.data);
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail || "Échec de l'acceptation — réessayez ou contactez l'administrateur.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 p-4">
      <Card className="w-full max-w-md">
        <Typography.Title level={4}>Invitation Cyllene</Typography.Title>

        {error && <Alert type="error" showIcon message={error} />}

        {!error && !intro && !done && <Spin />}

        {done ? (
          <>
            <Alert type="success" showIcon className="mb-4"
                   message={`Vous avez rejoint « ${done.org_name} ».`} />
            <Button type="primary" block href={done.ragflow_url}>
              Accéder à la plateforme
            </Button>
          </>
        ) : intro && !error ? (
          <>
            <Typography.Paragraph>
              <b>{intro.org_name}</b> vous invite à la rejoindre
              (<Typography.Text code>{intro.email}</Typography.Text>, rôle {intro.role}).
            </Typography.Paragraph>
            {intro.workspace_name && (
              <Typography.Paragraph type="secondary">
                Vous rejoindrez le workspace « {intro.workspace_name} » en tant que {intro.workspace_role}.
              </Typography.Paragraph>
            )}
            {intro.needs_password && (
              <Form form={form} layout="vertical">
                <Form.Item name="nickname" label="Votre nom" rules={[{ required: true }]}>
                  <Input placeholder="Prénom Nom" maxLength={100} />
                </Form.Item>
                <Form.Item name="password" label="Mot de passe"
                           rules={[{ required: true, min: 8, message: '8 caractères minimum' }]}>
                  <Input.Password />
                </Form.Item>
                <Form.Item name="confirm" label="Confirmer le mot de passe"
                           dependencies={['password']}
                           rules={[{ required: true },
                                   ({ getFieldValue }) => ({
                                     validator: (_, v) => v === getFieldValue('password')
                                       ? Promise.resolve()
                                       : Promise.reject(new Error('Les mots de passe ne correspondent pas')),
                                   })]}>
                  <Input.Password />
                </Form.Item>
              </Form>
            )}
            <Button type="primary" block loading={busy} onClick={onAccept}>
              {intro.needs_password ? 'Créer mon compte et rejoindre' : "Accepter l'invitation"}
            </Button>
          </>
        ) : null}
      </Card>
    </div>
  );
}
