import { useEffect, useState, useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Table, Button, Card, Modal, Form, Input, InputNumber, App, Progress, Tag, Popconfirm, Space, Tooltip, Typography, Alert } from 'antd';
import { PlusOutlined, StopOutlined, CopyOutlined, MailOutlined, ReloadOutlined, DeleteOutlined, EditOutlined, UserAddOutlined } from '@ant-design/icons';
import api from '@/lib/api';
import CodeDashboardSection, { fmtTokens } from './dashboard-section';

interface CodeKey { id: string; label: string; key_masked: string | null; status: string; sync_status: string; spend: number | null; max_budget: number | null; rpm_limit: number | null; }
interface CodeTeam { id: string; name: string; max_budget: number; spend: number | null; status: string; sync_status: string; keys: CodeKey[]; tokens_today: number | null; }
interface CodeInvite { id: string; email: string; expires_at: string; created_by?: string; }
interface TeamAdmin { user_id: string; email: string; role: string; }
interface BulkResult { email: string; invite_id: string; email_sent: boolean; claim_url?: string; }
interface Overview {
  gateway_url: string | null; // URL publique /v1 à configurer dans Kilo/OpenCode (null = non configurée)
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

// Le backend préfixe les liens de claim avec ADMIN_PANEL_PUBLIC_URL — vide en
// dev, le lien arrive relatif. Le front connaît toujours sa vraie origine :
// on absolutise à l'affichage pour que le copier-coller marche partout.
const absClaimUrl = (u: string) => (u.startsWith('http') ? u : window.location.origin + u);

export default function CodePage({ orgId: orgIdProp }: { orgId?: string } = {}) {
  // Deux modes : page autonome (menu Code, vue produit/ops, org via ?org=)
  // ou embarqué comme onglet de la fiche organisation (orgId en prop —
  // pas de landing, pas de back-link, pas de dashboard global).
  const embedded = !!orgIdProp;
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const orgId = orgIdProp || searchParams.get('org') || '';
  // La vue org vit UNIQUEMENT dans la fiche organisation (onglet Code) :
  // le menu Code est la vue produit/ops (dashboard + annuaire). Les anciens
  // liens /admin/code?org=... redirigent vers la fiche.
  useEffect(() => {
    if (!embedded && orgId) navigate(`/organisations/${orgId}?tab=code`, { replace: true });
  }, [embedded, orgId, navigate]);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<'forbidden' | 'other' | null>(null);
  const [teamModal, setTeamModal] = useState(false);
  const [keyModalTeam, setKeyModalTeam] = useState<CodeTeam | null>(null);
  const [freshKey, setFreshKey] = useState<string | null>(null);
  const [teamForm] = Form.useForm();
  const [keyForm] = Form.useForm();
  const [budgetModalTeam, setBudgetModalTeam] = useState<CodeTeam | null>(null);
  const [budgetForm] = Form.useForm();
  const [adminModalTeam, setAdminModalTeam] = useState<CodeTeam | null>(null);
  const [adminForm] = Form.useForm();
  const [teamAdmins, setTeamAdmins] = useState<TeamAdmin[] | null>(null);
  const [limitsModalKey, setLimitsModalKey] = useState<CodeKey | null>(null);
  const [limitsForm] = Form.useForm();
  const { message } = App.useApp();

  // Bulk seat invites — one CodeKeyInvite per email, no key created until claim.
  const [bulkModalTeam, setBulkModalTeam] = useState<CodeTeam | null>(null);
  const [bulkText, setBulkText] = useState('');
  const [bulkBudget, setBulkBudget] = useState<number | null>(null);
  const [bulkRpm, setBulkRpm] = useState<number | null>(null);
  const [bulkResults, setBulkResults] = useState<BulkResult[] | null>(null);
  // Pending invites shown under each team's keys table.
  const [invitesByTeam, setInvitesByTeam] = useState<Record<string, CodeInvite[]>>({});

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
    if (!embedded && !orgId && summary && summary.length === 1) {
      navigate(`/organisations/${summary[0].org_id}?tab=code`, { replace: true });
    }
  }, [embedded, orgId, summary, navigate]);

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

  const fetchInvites = useCallback((teamId: string) => {
    api.get(`/code/teams/${teamId}/invites`)
      .then((res) => setInvitesByTeam((prev) => ({ ...prev, [teamId]: res.data })))
      .catch(() => {});
  }, []);

  useEffect(() => {
    (overview?.teams ?? []).forEach((t) => fetchInvites(t.id));
  }, [overview, fetchInvites]);

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

  const onDeleteTeam = async (team: CodeTeam) => {
    try {
      const res = await api.delete(`/code/teams/${team.id}`);
      message.success(res.data.deleted === 'hard'
        ? 'Team supprimée'
        : 'Team archivée — clés révoquées, historique de dépense conservé');
      fetchOverview();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg ?? 'Échec de la suppression de la team');
    }
  };

  const onUpdateBudget = async () => {
    if (!budgetModalTeam) return;
    try {
      const values = await budgetForm.validateFields();
      await api.put(`/code/teams/${budgetModalTeam.id}`, values);
      message.success('Budget mis à jour');
      setBudgetModalTeam(null);
      budgetForm.resetFields();
      fetchOverview();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      if (msg) message.error(msg);
    }
  };

  const fetchTeamAdmins = useCallback((teamId: string) => {
    api.get(`/code/teams/${teamId}/admins`)
      .then((res) => setTeamAdmins(res.data))
      .catch(() => setTeamAdmins([]));
  }, []);

  const onAddAdmin = async () => {
    if (!adminModalTeam) return;
    try {
      const values = await adminForm.validateFields();
      await api.post(`/code/teams/${adminModalTeam.id}/admins`, values);
      message.success(`${values.email} peut maintenant gérer les clés de « ${adminModalTeam.name} »`);
      adminForm.resetFields();
      fetchTeamAdmins(adminModalTeam.id);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      if (msg) message.error(msg);
    }
  };

  const onRemoveAdmin = async (targetUserId: string) => {
    if (!adminModalTeam) return;
    try {
      await api.delete(`/code/teams/${adminModalTeam.id}/admins/${targetUserId}`);
      message.success('Délégation révoquée');
      fetchTeamAdmins(adminModalTeam.id);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg ?? 'Échec de la révocation de la délégation');
    }
  };

  const onBulkInvite = async () => {
    if (!bulkModalTeam) return;
    const emails = bulkText.split('\n').map((s) => s.trim()).filter(Boolean);
    if (emails.length === 0) return;
    try {
      const res = await api.post(`/code/teams/${bulkModalTeam.id}/keys/bulk`,
        { emails, max_budget: bulkBudget ?? undefined, rpm_limit: bulkRpm ?? undefined });
      setBulkResults(res.data);
      fetchInvites(bulkModalTeam.id);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg ?? "Échec de l'invitation en masse");
    }
  };

  const onResendAll = async (team: CodeTeam) => {
    try {
      const res = await api.post(`/code/teams/${team.id}/invites/resend-all`);
      const sent = (res.data as BulkResult[]).filter((r) => r.email_sent).length;
      message.success(`${res.data.length} invitation(s) re-générée(s), ${sent} email(s) envoyé(s)`);
      // Réutilise le modal de résultats du bulk pour exposer les liens fallback.
      if ((res.data as BulkResult[]).some((r) => !r.email_sent)) {
        setBulkModalTeam(team);
        setBulkResults(res.data);
      }
      fetchInvites(team.id);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg ?? 'Échec du renvoi groupé');
    }
  };

  const onResend = async (inviteId: string, teamId: string) => {
    try {
      const res = await api.post(`/code/invites/${inviteId}/resend`);
      if (res.data.claim_url) {
        Modal.info({
          title: 'SMTP non configuré — lien à transmettre manuellement',
          content: (
            <Typography.Paragraph copyable={{ icon: <CopyOutlined /> }} code>
              {absClaimUrl(res.data.claim_url)}
            </Typography.Paragraph>
          ),
        });
      } else {
        message.success('Invitation renvoyée');
      }
      fetchInvites(teamId);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg ?? 'Échec du renvoi');
    }
  };

  const onUpdateKeyLimits = async () => {
    if (!limitsModalKey) return;
    try {
      const values = await limitsForm.validateFields();
      await api.put(`/code/keys/${limitsModalKey.id}`, {
        max_budget: values.max_budget ?? null,
        rpm_limit: values.rpm_limit ?? null,
      });
      message.success('Limites du siège mises à jour — effet immédiat, la clé ne change pas');
      setLimitsModalKey(null);
      limitsForm.resetFields();
      fetchOverview();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      if (msg) message.error(msg);
    }
  };

  const onCancelInvite = async (inviteId: string, teamId: string) => {
    try {
      await api.delete(`/code/invites/${inviteId}`);
      message.success('Invitation annulée — le lien est invalidé');
      fetchInvites(teamId);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg ?? "Échec de l'annulation");
    }
  };

  const onRotate = async (keyId: string) => {
    try {
      const res = await api.post(`/code/keys/${keyId}/rotate`);
      if (res.data.claim_url) {
        Modal.info({
          title: 'SMTP non configuré — lien à transmettre manuellement',
          content: (
            <Typography.Paragraph copyable={{ icon: <CopyOutlined /> }} code>
              {absClaimUrl(res.data.claim_url)}
            </Typography.Paragraph>
          ),
        });
      } else {
        message.success('Clé révoquée — nouvelle invitation envoyée');
      }
      fetchOverview();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg ?? 'Échec de la rotation de la clé');
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
            onRow={(r) => ({ onClick: () => navigate(`/organisations/${r.org_id}?tab=code`), style: { cursor: 'pointer' } })}
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

  const backLink = null;
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
    { title: 'Dépensé', dataIndex: 'spend', width: 130,
      render: (v: number | null, k: CodeKey) => {
        const spent = v == null ? '—' : `${Math.round(v * 100) / 100}`;
        return k.max_budget != null ? `${spent} / ${euro(k.max_budget)}` : (v == null ? '—' : `${spent} €`);
      } },
    { title: 'Statut', dataIndex: 'status', render: (s: string) => <Tag color={s === 'active' ? 'green' : 'red'}>{s}</Tag> },
    { title: 'Sync', dataIndex: 'sync_status', render: (s: string) => <Tag color={s === 'synced' ? 'blue' : 'orange'}>{s}</Tag> },
    {
      title: '', width: 100,
      render: (_: unknown, k: CodeKey) => k.status === 'active' && (
        <Space size="small">
          <Tooltip title="Modifier le budget / la limite rpm du siège (la clé ne change pas)">
            <Button type="text" icon={<EditOutlined />} size="small"
                    onClick={() => { setLimitsModalKey(k);
                                     limitsForm.setFieldsValue({ max_budget: k.max_budget, rpm_limit: k.rpm_limit }); }} />
          </Tooltip>
          <Popconfirm title={`Révoque la clé et envoie un nouveau lien à ${k.label}`}
                     onConfirm={() => onRotate(k.id)}>
            <Button type="text" icon={<ReloadOutlined />} size="small" />
          </Popconfirm>
          <Popconfirm title="Révoquer cette clé ?" onConfirm={() => onRevoke(k.id)}>
            <Button type="text" danger icon={<StopOutlined />} size="small" />
          </Popconfirm>
        </Space>
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

      {overview?.gateway_url && (
        <div className="mb-4 text-gray-600">
          Endpoint (base URL à configurer dans Kilo Code / OpenCode / Cline)&nbsp;:{' '}
          <Typography.Text copyable code>{overview.gateway_url}</Typography.Text>
        </div>
      )}

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
                     <Tag color="geekblue">{team.tokens_today == null ? '— tokens' : `${fmtTokens(team.tokens_today)} tokens auj.`}</Tag>
                     {team.sync_status !== 'synced' && <Tag color="orange">{team.sync_status}</Tag>}</Space>}
              extra={<Space>
                       <Tooltip title="Le flux nominal pour des développeurs : chaque email reçoit un lien one-time et récupère sa clé lui-même — vous ne voyez jamais la clé.">
                         <Button size="small" type="primary" ghost icon={<MailOutlined />}
                                 onClick={() => setBulkModalTeam(team)}>Inviter par email</Button>
                       </Tooltip>
                       <Tooltip title="Cas service (CI, intégration, test) : la clé est créée et affichée immédiatement, sans email — c'est vous qui la transmettez.">
                         <Button size="small" icon={<PlusOutlined />}
                                 onClick={() => setKeyModalTeam(team)}>Clé directe</Button>
                       </Tooltip>
                       <Tooltip title="Modifier le budget de la team">
                         <Button size="small" icon={<EditOutlined />}
                                 onClick={() => { setBudgetModalTeam(team); budgetForm.setFieldsValue({ max_budget: team.max_budget }); }} />
                       </Tooltip>
                       <Tooltip title="Déléguer la gestion des clés de cette team à un membre de l'organisation (invitations, rotation, révocation — sans accès aux autres teams).">
                         <Button size="small" icon={<UserAddOutlined />}
                                 onClick={() => { setAdminModalTeam(team); setTeamAdmins(null); fetchTeamAdmins(team.id); }} />
                       </Tooltip>
                       <Popconfirm
                         title="Supprimer cette team ?"
                         description={(team.keys?.length ?? 0) > 0
                           ? 'Ses clés seront révoquées et ses invitations annulées. L\'historique de dépense est conservé. Le budget alloué est libéré.'
                           : 'Aucune clé créée : la team sera supprimée définitivement.'}
                         okText="Supprimer" okButtonProps={{ danger: true }}
                         onConfirm={() => onDeleteTeam(team)}>
                         <Button size="small" danger icon={<DeleteOutlined />} />
                       </Popconfirm>
                     </Space>}>
          <Table rowKey="id" size="small" pagination={false}
                 columns={keyColumns(team)} dataSource={team.keys} />

          {(invitesByTeam[team.id] ?? []).length > 0 && (
            <div className="mt-3">
              <Space>
                <Typography.Text type="secondary">Invitations en attente</Typography.Text>
                <Popconfirm
                  title="Re-générer et renvoyer TOUTES les invitations pendantes ? (les anciens liens seront invalidés, l'expiration repart de 72 h)"
                  onConfirm={() => onResendAll(team)}>
                  <Button size="small" icon={<MailOutlined />}>Renvoyer tout</Button>
                </Popconfirm>
              </Space>
              <Table rowKey="id" size="small" pagination={false} showHeader={false}
                     className="mt-1"
                     dataSource={invitesByTeam[team.id]}
                     columns={[
                       { title: 'Email', dataIndex: 'email' },
                       { title: 'Expire', dataIndex: 'expires_at',
                         render: (v: string) => `expire le ${new Date(v).toLocaleString()}` },
                       { title: '', width: 150,
                         render: (_: unknown, inv: CodeInvite) => (
                           <Space size="small">
                             <Button size="small" onClick={() => onResend(inv.id, team.id)}>Renvoyer</Button>
                             <Popconfirm title={`Annuler l'invitation de ${inv.email} ? Le lien sera invalidé.`}
                                         okText="Annuler l'invitation" okButtonProps={{ danger: true }}
                                         onConfirm={() => onCancelInvite(inv.id, team.id)}>
                               <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                             </Popconfirm>
                           </Space>
                         ) },
                     ]} />
            </div>
          )}
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

      <Modal title={`Limites du siège — ${limitsModalKey?.label ?? ''}`} open={!!limitsModalKey}
             onOk={onUpdateKeyLimits} okText="Appliquer"
             onCancel={() => { setLimitsModalKey(null); limitsForm.resetFields(); }}>
        <Form form={limitsForm} layout="vertical">
          <Form.Item name="max_budget" label={`Budget (€ / ${ent?.budget_period ?? '1mo'})`}
                     extra="Vider le champ = supprimer la limite (seule celle de la team s'applique). Appliqué en temps réel.">
            <InputNumber min={0.01} className="w-full" placeholder="illimité (dans la limite team)" />
          </Form.Item>
          <Form.Item name="rpm_limit" label="Requêtes/minute">
            <InputNumber min={1} className="w-full" placeholder="illimité" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title={`Budget — ${budgetModalTeam?.name ?? ''}`} open={!!budgetModalTeam}
             onOk={onUpdateBudget}
             onCancel={() => { setBudgetModalTeam(null); budgetForm.resetFields(); }}>
        <Form form={budgetForm} layout="vertical">
          <Form.Item name="max_budget" label={`Budget (€ / ${ent?.budget_period ?? '1mo'})`}
                     rules={[{ required: true }]}
                     extra={`Disponible dans l'organisation : ${euro(Math.max(0, total - allocated + (budgetModalTeam?.max_budget ?? 0)))} max`}>
            <InputNumber min={1}
                         max={Math.max(0, total - allocated + (budgetModalTeam?.max_budget ?? 0))}
                         className="w-full" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title={`Déléguer la gestion — ${adminModalTeam?.name ?? ''}`} open={!!adminModalTeam}
             onOk={onAddAdmin} okText="Déléguer"
             onCancel={() => { setAdminModalTeam(null); adminForm.resetFields(); setTeamAdmins(null); }}>
        <Typography.Paragraph type="secondary">
          La personne pourra inviter des sièges, créer, faire tourner et révoquer les clés de
          cette team uniquement. Elle doit avoir un compte actif du panel, membre de cette
          organisation.
        </Typography.Paragraph>
        {(teamAdmins ?? []).length > 0 && (
          <div className="mb-3">
            <Typography.Text type="secondary">Délégations actives</Typography.Text>
            <Table rowKey="user_id" size="small" pagination={false} showHeader={false}
                   className="mt-1" dataSource={teamAdmins ?? []}
                   columns={[
                     { title: 'Email', dataIndex: 'email' },
                     { title: '', width: 60,
                       render: (_: unknown, a: TeamAdmin) => (
                         <Popconfirm title={`Révoquer la délégation de ${a.email} ?`}
                                     onConfirm={() => onRemoveAdmin(a.user_id)}>
                           <Button type="text" danger size="small" icon={<StopOutlined />} />
                         </Popconfirm>
                       ) },
                   ]} />
          </div>
        )}
        <Form form={adminForm} layout="vertical">
          <Form.Item name="email" label="Email du membre"
                     rules={[{ required: true, type: 'email' }]}>
            <Input placeholder="prenom.nom@client.com" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title={`Clé directe — ${keyModalTeam?.name ?? ''}`} open={!!keyModalTeam}
             onOk={freshKey ? () => { setFreshKey(null); setKeyModalTeam(null); } : onCreateKey}
             okText={freshKey ? 'Fermer' : 'Créer'}
             cancelButtonProps={freshKey ? { style: { display: 'none' } } : undefined}
             onCancel={() => { setFreshKey(null); setKeyModalTeam(null); }}>
        {freshKey ? (
          <Alert type="success" message="Clé créée — copiez-la MAINTENANT, elle ne sera plus jamais affichée."
                 description={
                   <>
                     <Typography.Paragraph copyable={{ icon: <CopyOutlined /> }} code>
                       {freshKey}
                     </Typography.Paragraph>
                     {overview?.gateway_url && (
                       <div className="text-gray-600">
                         Endpoint associé&nbsp;:{' '}
                         <Typography.Text copyable code>{overview.gateway_url}</Typography.Text>
                       </div>
                     )}
                   </>
                 } />
        ) : (
          <Form form={keyForm} layout="vertical">
            <Form.Item name="label" label="Label (dev / siège)" rules={[{ required: true }]}>
              <Input placeholder="ci-pipeline, integration-test… (ou un email)" />
            </Form.Item>
            <Form.Item name="max_budget"
                       label={`Budget du siège (€ / ${ent?.budget_period ?? '1mo'}) — optionnel`}
                       extra="Vide = seule la limite de la team s'applique. Appliqué en temps réel par la gateway.">
              <InputNumber min={0.01} className="w-full" placeholder="illimité (dans la limite team)" />
            </Form.Item>
            <Form.Item name="rpm_limit" label="Limite de requêtes/minute — optionnel">
              <InputNumber min={1} className="w-full" placeholder="illimité" />
            </Form.Item>
          </Form>
        )}
      </Modal>

      <Modal title={`Inviter par email — ${bulkModalTeam?.name ?? ''}`} open={!!bulkModalTeam}
             onOk={bulkResults
               ? () => { setBulkModalTeam(null); setBulkResults(null); setBulkText(''); setBulkBudget(null); setBulkRpm(null); }
               : onBulkInvite}
             okText={bulkResults ? 'Fermer' : 'Envoyer'}
             cancelButtonProps={bulkResults ? { style: { display: 'none' } } : undefined}
             onCancel={() => { setBulkModalTeam(null); setBulkResults(null); setBulkText(''); setBulkBudget(null); setBulkRpm(null); }}>
        {bulkResults ? (
          <Table rowKey="invite_id" size="small" pagination={false} dataSource={bulkResults}
                 columns={[
                   { title: 'Email', dataIndex: 'email' },
                   {
                     title: 'Statut',
                     render: (_: unknown, r: BulkResult) => r.email_sent
                       ? <Tag color="green">✓ envoyé</Tag>
                       : (
                         <span>
                           ✗ non envoyé — lien à transmettre :{' '}
                           <Typography.Text copyable={{ text: r.claim_url ? absClaimUrl(r.claim_url) : '' }} code>
                             {r.claim_url ? absClaimUrl(r.claim_url) : ''}
                           </Typography.Text>
                         </span>
                       ),
                   },
                 ]} />
        ) : (
          <Form layout="vertical">
            <Form.Item label="Emails (un par ligne)">
              <Input.TextArea rows={6} placeholder="un email par ligne"
                              value={bulkText} onChange={(e) => setBulkText(e.target.value)} />
            </Form.Item>
            <Space size="large">
              <Form.Item label={`Budget par siège (€ / ${ent?.budget_period ?? '1mo'}) — optionnel`}
                         extra="Appliqué à chaque invité, en temps réel par la gateway.">
                <InputNumber min={0.01} value={bulkBudget}
                             onChange={(v) => setBulkBudget(v ?? null)}
                             placeholder="illimité" style={{ width: 180 }} />
              </Form.Item>
              <Form.Item label="Requêtes/minute — optionnel">
                <InputNumber min={1} value={bulkRpm}
                             onChange={(v) => setBulkRpm(v ?? null)}
                             placeholder="illimité" style={{ width: 140 }} />
              </Form.Item>
            </Space>
          </Form>
        )}
      </Modal>
    </div>
  );
}
