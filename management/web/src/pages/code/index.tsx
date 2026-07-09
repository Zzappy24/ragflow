import { useEffect, useState, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Table, Button, Card, Modal, Form, Input, InputNumber, App, Progress, Tag, Popconfirm, Space, Typography, Alert } from 'antd';
import { PlusOutlined, StopOutlined, CopyOutlined } from '@ant-design/icons';
import api from '@/lib/api';
import CodeDashboardSection from './dashboard-section';

interface CodeKey { id: string; label: string; key_masked: string | null; status: string; sync_status: string; spend: number | null; }
interface CodeTeam { id: string; name: string; max_budget: number; spend: number | null; status: string; sync_status: string; keys: CodeKey[]; }
interface Overview {
  entitlement: { status: string; org_code_budget: number; budget_period: string } | null;
  allocated: number;
  org_spend: number | null; // null = gateway injoignable (≠ 0 dépensé)
  teams: CodeTeam[];
}
interface OrgSummary {
  org_id: string; org_name: string;
  code_status: 'active' | 'suspended' | null;
  org_code_budget: number; allocated: number;
  spend: number | null;
  teams_count: number; keys_count: number;
}

const euro = (v: number) => `${Math.round(v * 100) / 100} €`;

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

  // No ?org= yet: landing = searchable orgs table with code status per org
  // (GET /code/orgs-summary — superusers see every org, everyone else only
  // their memberships). A single visible org is auto-selected. Row click
  // pushes ?org= into the URL so the browser back button works.
  const [summary, setSummary] = useState<OrgSummary[] | null>(null);
  const [summaryError, setSummaryError] = useState(false);
  const [orgSearch, setOrgSearch] = useState('');

  useEffect(() => {
    if (orgId) return;
    setSummaryError(false);
    api.get('/code/orgs-summary')
      .then((res) => setSummary(res.data))
      .catch(() => { setSummary([]); setSummaryError(true); });
  }, [orgId]);

  useEffect(() => {
    if (!orgId && summary && summary.length === 1) {
      setSearchParams({ org: summary[0].org_id });
    }
  }, [orgId, summary, setSearchParams]);

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

  const statusTag = (s: OrgSummary['code_status']) =>
    s === 'active' ? <Tag color="green">actif</Tag>
    : s === 'suspended' ? <Tag color="orange">suspendu</Tag>
    : <Tag>non activé</Tag>;

  if (!orgId) {
    if (summaryError)
      return <Card><Alert type="error" showIcon message="Impossible de charger les organisations." /></Card>;
    if (summary && summary.length === 0)
      return <Card><Alert type="info" message="Aucune organisation disponible pour ton compte." /></Card>;
    // summary.length === 1 is handled by the auto-select effect above —
    // the table only renders once there's a real choice to make.
    const statusRank: Record<string, number> = { active: 0, suspended: 1 };
    const filtered = (summary ?? [])
      .filter((o) => o.org_name.toLowerCase().includes(orgSearch.toLowerCase()))
      .sort((a, b) =>
        (statusRank[a.code_status ?? 'z'] ?? 2) - (statusRank[b.code_status ?? 'z'] ?? 2)
        || a.org_name.localeCompare(b.org_name));
    return (
      <div>
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-xl font-semibold">Code — accès gateway</h2>
          <Input.Search placeholder="Rechercher une organisation…" allowClear
            style={{ width: 320 }} value={orgSearch}
            onChange={(e) => setOrgSearch(e.target.value)} />
        </div>
        <CodeDashboardSection />
        <Card>
          <Table rowKey="org_id" size="middle" loading={summary === null}
            pagination={false} dataSource={filtered}
            onRow={(r) => ({ onClick: () => setSearchParams({ org: r.org_id }), style: { cursor: 'pointer' } })}
            columns={[
              { title: 'Organisation', dataIndex: 'org_name' },
              { title: 'Statut', dataIndex: 'code_status', width: 130, render: statusTag },
              { title: 'Dépensé', width: 120,
                render: (_, r: OrgSummary) =>
                  !r.code_status ? '—' : r.spend === null ? '—' : euro(r.spend) },
              { title: 'Budget (alloué / total)', width: 180,
                render: (_, r: OrgSummary) => r.code_status ? `${euro(r.allocated)} / ${euro(r.org_code_budget)}` : '—' },
              { title: 'Teams', dataIndex: 'teams_count', width: 90,
                render: (v: number, r: OrgSummary) => (r.code_status ? v : '—') },
              { title: 'Clés', dataIndex: 'keys_count', width: 90,
                render: (v: number, r: OrgSummary) => (r.code_status ? v : '—') },
            ]} />
        </Card>
      </div>
    );
  }

  const backLink = (
    <Button type="link" className="px-0 mb-2" onClick={() => setSearchParams({})}>
      ← Toutes les organisations
    </Button>
  );
  if (!loading && loadError)
    return (
      <div>
        {backLink}
        <Card>
          <Alert
            type="error"
            showIcon
            message={loadError === 'forbidden' ? 'Accès refusé à cette organisation' : 'Accès refusé ou erreur de chargement'}
          />
        </Card>
      </div>
    );
  if (!loading && overview && !overview.entitlement)
    return (
      <div>
        {backLink}
        <Card><Alert type="info" message="Le produit Code n'est pas activé pour cette organisation." /></Card>
      </div>
    );

  const ent = overview?.entitlement;
  const allocated = overview?.allocated ?? 0;
  const total = ent?.org_code_budget ?? 0;

  const keyColumns = (_team: CodeTeam) => [
    { title: 'Label', dataIndex: 'label' },
    { title: 'Clé', dataIndex: 'key_masked', render: (v: string | null) => <code>{v || '—'}</code> },
    { title: 'Dépensé', dataIndex: 'spend', width: 100,
      render: (v: number | null) => (v == null ? '—' : `${Math.round(v * 100) / 100} €`) },
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
      {backLink}
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-semibold">
          Code — accès gateway
          {ent?.status === 'active' && <Tag color="green" className="ml-2">actif</Tag>}
          {ent?.status === 'suspended' && <Tag color="orange" className="ml-2">suspendu</Tag>}
        </h2>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setTeamModal(true)}>
          Nouvelle code-team
        </Button>
      </div>

      {ent?.status === 'suspended' && (
        <Alert className="mb-4" type="warning" message="Produit Code suspendu — toutes les clés sont bloquées." />
      )}

      <Card className="mb-4" title="Budget de l'organisation">
        <Progress
          percent={total && overview?.org_spend != null
            ? Math.min(100, Math.round((overview.org_spend / total) * 100)) : 0}
          status={overview?.org_spend != null && total && overview.org_spend >= total ? 'exception' : undefined}
          format={() => overview?.org_spend != null
            ? `${euro(overview.org_spend)} dépensés / ${euro(total)} (${ent?.budget_period})`
            : `dépense indisponible — gateway injoignable`} />
        <div className="text-gray-500 mt-2">
          Alloué aux teams : {euro(allocated)} / {euro(total)}
        </div>
      </Card>

      {(overview?.teams ?? []).map((team) => (
        <Card key={team.id} className="mb-4"
              title={<Space>{team.name}
                     <Tag color={team.spend != null && team.spend >= team.max_budget ? 'red' : 'blue'}>
                       {team.spend != null ? `${euro(team.spend)} / ${euro(team.max_budget)}` : `— / ${euro(team.max_budget)}`}
                     </Tag>
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
