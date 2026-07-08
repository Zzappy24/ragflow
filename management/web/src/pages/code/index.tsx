import { useEffect, useState, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Table, Button, Card, Modal, Form, Input, InputNumber, Select, App, Progress, Tag, Popconfirm, Space, Typography, Alert } from 'antd';
import { PlusOutlined, StopOutlined, CopyOutlined } from '@ant-design/icons';
import api from '@/lib/api';

interface CodeKey { id: string; label: string; key_masked: string | null; status: string; sync_status: string; }
interface CodeTeam { id: string; name: string; max_budget: number; status: string; sync_status: string; keys: CodeKey[]; }
interface Overview {
  entitlement: { status: string; org_code_budget: number; budget_period: string } | null;
  allocated: number;
  teams: CodeTeam[];
}
interface OrgOption { id: string; name: string; }

export default function CodePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const orgId = searchParams.get('org') || '';
  const [overview, setOverview] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<'forbidden' | 'other' | null>(null);
  const [teamModal, setTeamModal] = useState(false);
  const [keyModalTeam, setKeyModalTeam] = useState<CodeTeam | null>(null);
  const [freshKey, setFreshKey] = useState<string | null>(null);
  const [teamForm] = Form.useForm();
  const [keyForm] = Form.useForm();
  const { message } = App.useApp();

  // No ?org= yet: figure out which org(s) the caller can see (same /orgs
  // endpoint the Organisations page uses — superusers get every org,
  // everyone else only the orgs they're a member of). A single visible org
  // is auto-selected; several render a picker instead of a dead end.
  const [orgOptions, setOrgOptions] = useState<OrgOption[] | null>(null);
  const [orgOptionsLoading, setOrgOptionsLoading] = useState(false);

  useEffect(() => {
    if (orgId) return;
    setOrgOptionsLoading(true);
    api.get('/orgs')
      .then((res) => setOrgOptions(res.data))
      .catch(() => setOrgOptions([]))
      .finally(() => setOrgOptionsLoading(false));
  }, [orgId]);

  useEffect(() => {
    if (!orgId && orgOptions && orgOptions.length === 1) {
      setSearchParams({ org: orgOptions[0].id });
    }
  }, [orgId, orgOptions, setSearchParams]);

  const fetchOverview = useCallback(() => {
    if (!orgId) { setLoading(false); return; }
    setLoading(true);
    setLoadError(null);
    api.get(`/orgs/${orgId}/code/overview`)
      .then((res) => setOverview(res.data))
      .catch((err: unknown) => {
        const status = (err as { response?: { status?: number } })?.response?.status;
        setLoadError(status === 403 ? 'forbidden' : 'other');
        setOverview(null);
      })
      .finally(() => setLoading(false));
  }, [orgId]);

  useEffect(fetchOverview, [fetchOverview]);

  const onCreateTeam = async () => {
    try {
      const values = await teamForm.validateFields();
      await api.post(`/orgs/${orgId}/code/teams`, { ...values, model_access: [] });
      message.success('Code team créée');
      setTeamModal(false);
      teamForm.resetFields();
      fetchOverview();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      if (msg) message.error(msg);
    }
  };

  const onCreateKey = async () => {
    if (!keyModalTeam) return;
    try {
      const values = await keyForm.validateFields();
      const res = await api.post(`/code/teams/${keyModalTeam.id}/keys`, values);
      if (res.data.plain_key) {
        setFreshKey(res.data.plain_key);
      } else {
        message.warning('Gateway injoignable — la clé est en attente, réessayez.');
        setKeyModalTeam(null);
      }
      keyForm.resetFields();
      fetchOverview();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      if (msg) message.error(msg);
    }
  };

  const onRevoke = async (keyId: string) => {
    try {
      await api.post(`/code/keys/${keyId}/revoke`);
      message.success('Clé révoquée');
      fetchOverview();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg ?? 'Échec de la révocation de la clé');
    }
  };

  if (!orgId) {
    if (orgOptionsLoading || orgOptions === null) return <Card loading />;
    if (orgOptions.length === 0)
      return <Card><Alert type="info" message="Aucune organisation disponible pour ton compte." /></Card>;
    // orgOptions.length === 1 is handled by the auto-select effect above —
    // this only renders once there's a real choice to make.
    return (
      <Card>
        <p className="mb-2 text-gray-500">Sélectionne une organisation :</p>
        <Select
          className="w-full max-w-sm"
          placeholder="Organisation"
          options={orgOptions.map((o) => ({ value: o.id, label: o.name }))}
          onChange={(value: string) => setSearchParams({ org: value })}
        />
      </Card>
    );
  }
  if (!loading && loadError)
    return (
      <Card>
        <Alert
          type="error"
          showIcon
          message={loadError === 'forbidden' ? 'Accès refusé à cette organisation' : 'Accès refusé ou erreur de chargement'}
        />
      </Card>
    );
  if (!loading && overview && !overview.entitlement)
    return <Card><Alert type="info" message="Le produit Code n'est pas activé pour cette organisation." /></Card>;

  const ent = overview?.entitlement;
  const allocated = overview?.allocated ?? 0;
  const total = ent?.org_code_budget ?? 0;

  const keyColumns = (_team: CodeTeam) => [
    { title: 'Label', dataIndex: 'label' },
    { title: 'Clé', dataIndex: 'key_masked', render: (v: string | null) => <code>{v || '—'}</code> },
    { title: 'Statut', dataIndex: 'status', render: (s: string) => <Tag color={s === 'active' ? 'green' : 'red'}>{s}</Tag> },
    { title: 'Sync', dataIndex: 'sync_status', render: (s: string) => <Tag color={s === 'synced' ? 'blue' : 'orange'}>{s}</Tag> },
    {
      title: '', width: 60,
      render: (_: unknown, k: CodeKey) => k.status === 'active' && (
        <Popconfirm title="Révoquer cette clé ?" onConfirm={() => onRevoke(k.id)}>
          <Button type="text" danger icon={<StopOutlined />} size="small" />
        </Popconfirm>
      ),
    },
  ];

  return (
    <div>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-semibold">Code — accès gateway</h2>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setTeamModal(true)}>
          Nouvelle code-team
        </Button>
      </div>

      {ent?.status === 'suspended' && (
        <Alert className="mb-4" type="warning" message="Produit Code suspendu — toutes les clés sont bloquées." />
      )}

      <Card className="mb-4" title="Budget de l'organisation">
        <Progress percent={total ? Math.min(100, Math.round((allocated / total) * 100)) : 0}
                  format={() => `${allocated} € alloués / ${total} € (${ent?.budget_period})`} />
      </Card>

      {(overview?.teams ?? []).map((team) => (
        <Card key={team.id} className="mb-4"
              title={<Space>{team.name}<Tag>{team.max_budget} €</Tag>
                     {team.sync_status !== 'synced' && <Tag color="orange">{team.sync_status}</Tag>}</Space>}
              extra={<Button size="small" icon={<PlusOutlined />}
                             onClick={() => setKeyModalTeam(team)}>Nouvelle clé</Button>}>
          <Table rowKey="id" size="small" pagination={false}
                 columns={keyColumns(team)} dataSource={team.keys} />
        </Card>
      ))}

      <Modal title="Nouvelle code-team" open={teamModal} onOk={onCreateTeam}
             onCancel={() => setTeamModal(false)}>
        <Form form={teamForm} layout="vertical">
          <Form.Item name="name" label="Nom" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="max_budget" label={`Budget (€ / ${ent?.budget_period ?? '1mo'})`}
                     rules={[{ required: true }]}>
            <InputNumber min={1} max={Math.max(0, total - allocated)} className="w-full" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title={`Nouvelle clé — ${keyModalTeam?.name ?? ''}`} open={!!keyModalTeam}
             onOk={freshKey ? () => { setFreshKey(null); setKeyModalTeam(null); } : onCreateKey}
             okText={freshKey ? 'Fermer' : 'Créer'}
             onCancel={() => { setFreshKey(null); setKeyModalTeam(null); }}>
        {freshKey ? (
          <Alert type="success" message="Clé créée — copiez-la MAINTENANT, elle ne sera plus jamais affichée."
                 description={
                   <Typography.Paragraph copyable={{ icon: <CopyOutlined /> }} code>
                     {freshKey}
                   </Typography.Paragraph>
                 } />
        ) : (
          <Form form={keyForm} layout="vertical">
            <Form.Item name="label" label="Label (dev / siège)" rules={[{ required: true }]}>
              <Input placeholder="dev-alice" />
            </Form.Item>
          </Form>
        )}
      </Modal>
    </div>
  );
}
