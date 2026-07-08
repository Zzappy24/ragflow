import { useEffect, useState, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Table, Button, Card, Modal, Form, Input, InputNumber, App, Progress, Tag, Popconfirm, Space, Typography, Alert } from 'antd';
import { PlusOutlined, StopOutlined, CopyOutlined } from '@ant-design/icons';
import api from '@/lib/api';

interface CodeKey { id: string; label: string; key_masked: string | null; status: string; sync_status: string; }
interface CodeTeam { id: string; name: string; max_budget: number; status: string; sync_status: string; keys: CodeKey[]; }
interface Overview {
  entitlement: { status: string; org_code_budget: number; budget_period: string } | null;
  allocated: number;
  teams: CodeTeam[];
}

export default function CodePage() {
  const [searchParams] = useSearchParams();
  const orgId = searchParams.get('org') || '';
  const [overview, setOverview] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [teamModal, setTeamModal] = useState(false);
  const [keyModalTeam, setKeyModalTeam] = useState<CodeTeam | null>(null);
  const [freshKey, setFreshKey] = useState<string | null>(null);
  const [teamForm] = Form.useForm();
  const [keyForm] = Form.useForm();
  const { message } = App.useApp();

  const fetchOverview = useCallback(() => {
    if (!orgId) { setLoading(false); return; }
    setLoading(true);
    api.get(`/orgs/${orgId}/code/overview`)
      .then((res) => setOverview(res.data))
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
    await api.post(`/code/keys/${keyId}/revoke`);
    message.success('Clé révoquée');
    fetchOverview();
  };

  if (!orgId) return <Card><p className="text-gray-500">Sélectionne une organisation : <code>?org=ORG_ID</code></p></Card>;
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
        <Progress percent={total ? Math.round((allocated / total) * 100) : 0}
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
