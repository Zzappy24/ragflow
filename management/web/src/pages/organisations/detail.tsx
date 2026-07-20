import { useEffect, useState, useCallback } from 'react';
import { useParams, Link, useNavigate, useSearchParams } from 'react-router-dom';
import { Card, Tabs, Spin, Progress, Row, Col, Statistic, Breadcrumb, Typography, Tag, Button, Modal, Input, App, Table, Space, Select, Switch, InputNumber, Tooltip, Alert, Form } from 'antd';
import { DatePicker } from 'antd';
import type { Dayjs } from 'dayjs';
import { AppstoreOutlined, TeamOutlined, AuditOutlined, HomeOutlined, DatabaseOutlined, FileOutlined, InboxOutlined, UndoOutlined, FireOutlined, ThunderboltOutlined, BarChartOutlined, WarningOutlined, DownloadOutlined, EuroOutlined } from '@ant-design/icons';
import {
  AreaChart, Area, BarChart, Bar, PieChart, Pie,
  XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip,
  ResponsiveContainer, Cell, Legend,
} from 'recharts';
import dayjs from 'dayjs';
import api from '@/lib/api';
import { useAuthStore } from '@/stores/auth';
import WorkspacesPage from '@/pages/workspaces';
import CodePage from '@/pages/code';
import MembersPage from '@/pages/members';
import AuditPage from '@/pages/audit';

const { Title, Text } = Typography;
const { RangePicker } = DatePicker;

const WS_COLORS = ['#6366f1', '#10b981', '#f59e0b', '#ec4899', '#f97316', '#06b6d4', '#8b5cf6', '#ef4444'];
const MODEL_TYPE_COLORS: Record<string, string> = {
  chat: '#6366f1', embedding: '#10b981', rerank: '#f59e0b',
  image2text: '#ec4899', tts: '#f97316', speech2text: '#06b6d4', asr: '#06b6d4', ocr: '#8b5cf6', unknown: '#9ca3af',
};
function typeColor(t: string) { return MODEL_TYPE_COLORS[t] ?? '#9ca3af'; }
function wsColor(i: number) { return WS_COLORS[i % WS_COLORS.length]; }

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n ?? 0);
}
function shortDate(d: string) { const p = d.split('-'); return `${p[2]}/${p[1]}`; }

interface DailyItem { date: string; tokens: number; [key: string]: number | string; }
interface ModelItem { model: string; type: string; type_label: string; factory: string; tokens: number; }
interface ModelTypeItem { type: string; type_label: string; tokens: number; }
interface WsItem { workspace_id: string; workspace_name: string; tokens: number; indexed_tokens: number; users: number; }

interface OrgUsage {
  totals: { workspaces: number; users: number; tokens_30d: number; indexed_tokens: number };
  daily_tokens: DailyItem[];
  daily_by_model_type_per_factory: Record<string, DailyItem[]>;
  daily_by_workspace: DailyItem[];
  factories: string[];
  model_types: string[];
  workspace_ids: string[];
  workspace_id_to_name: Record<string, string>;
  by_workspace: WsItem[];
  by_model: ModelItem[];
  by_model_type: ModelTypeItem[];
  by_factory: { factory: string; tokens: number }[];
}

function filterByRange<T extends { date: string }>(items: T[], range: [Dayjs, Dayjs] | null): T[] {
  if (!range) return items;
  const [s, e] = range;
  return items.filter((d) => {
    const day = dayjs(d.date);
    return !day.isBefore(s, 'day') && !day.isAfter(e, 'day');
  });
}

const RADIAN = Math.PI / 180;
function DonutLabel({ cx, cy, midAngle, innerRadius, outerRadius, percent }: any) {
  if (percent < 0.05) return null;
  const r = innerRadius + (outerRadius - innerRadius) * 0.5;
  const x = cx + r * Math.cos(-midAngle * RADIAN);
  const y = cy + r * Math.sin(-midAngle * RADIAN);
  return <text x={x} y={y} fill="white" textAnchor="middle" dominantBaseline="central" fontSize={11}>{`${(percent * 100).toFixed(0)}%`}</text>;
}

function OrgUsageTab({ orgId }: { orgId: string }) {
  const [data, setData] = useState<OrgUsage | null>(null);
  const [loading, setLoading] = useState(true);
  const [dateRange, setDateRange] = useState<[Dayjs, Dayjs] | null>(null);
  const [selectedTypes, setSelectedTypes] = useState<string[]>([]);
  const [selectedWs, setSelectedWs] = useState<string[]>([]);
  const [selectedFactory, setSelectedFactory] = useState<string | null>(null);

  useEffect(() => {
    api.get(`/orgs/${orgId}/usage`)
      .then((r) => setData(r.data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [orgId]);

  if (loading) return <Spin className="flex justify-center mt-10" />;
  if (!data) return <p className="text-gray-400">Données indisponibles.</p>;

  const activeTypes = selectedTypes.length > 0 ? selectedTypes : data.model_types;
  const activeWs = selectedWs.length > 0 ? selectedWs : data.workspace_ids;

  const dailySeriesForFactory = selectedFactory
    ? (data.daily_by_model_type_per_factory[selectedFactory] ?? data.daily_by_model_type_per_factory['__all__'])
    : data.daily_by_model_type_per_factory['__all__'];
  const filteredDailyByType = filterByRange(dailySeriesForFactory, dateRange);
  const filteredDailyByWs = filterByRange(data.daily_by_workspace, dateRange);
  const filteredModels = data.by_model
    .filter((m) => activeTypes.includes(m.type))
    .filter((m) => !selectedFactory || m.factory === selectedFactory);
  const filteredModelTypes = data.by_model_type.filter((m) => activeTypes.includes(m.type));
  const filteredWsList = data.by_workspace.filter((w) => activeWs.includes(w.workspace_id));

  const typeOptions = data.model_types.map((t) => ({
    label: data.by_model_type.find((m) => m.type === t)?.type_label ?? t, value: t,
  }));
  const wsOptions = data.workspace_ids.map((id) => ({
    label: data.workspace_id_to_name[id] ?? id, value: id,
  }));
  const factoryOptions = data.by_factory.map((f) => ({ label: f.factory || '(direct)', value: f.factory }));

  const wsColumns = [
    {
      title: 'Workspace', dataIndex: 'workspace_name', key: 'name',
      render: (name: string, row: any) => (
        <Link to={`/workspaces/${row.workspace_id}`} className="text-indigo-600 hover:underline">{name}</Link>
      ),
    },
    { title: 'Membres', dataIndex: 'users', key: 'users', width: 90, align: 'right' as const },
    {
      title: 'Tokens indexés', dataIndex: 'indexed_tokens', key: 'idx',
      width: 130, align: 'right' as const,
      render: (v: number) => <span className="text-emerald-600">{fmtTokens(v)}</span>,
      sorter: (a: any, b: any) => a.indexed_tokens - b.indexed_tokens,
    },
    {
      title: 'Tokens LLM (30j)', dataIndex: 'tokens', key: 'tokens',
      width: 140, align: 'right' as const,
      render: (v: number) => <span className="text-indigo-600 font-medium">{fmtTokens(v)}</span>,
      defaultSortOrder: 'descend' as const,
      sorter: (a: any, b: any) => a.tokens - b.tokens,
    },
  ];

  const modelColumns = [
    {
      title: 'Modèle', dataIndex: 'model', key: 'model',
      render: (m: string, row: any) => (
        <div>
          <div className="font-medium text-sm">{m}</div>
          <div className="text-xs text-gray-400">{row.factory}</div>
        </div>
      ),
    },
    {
      title: 'Type', dataIndex: 'type_label', key: 'type', width: 110,
      render: (t: string, row: any) => (
        <Tag style={{ background: typeColor(row.type) + '20', borderColor: typeColor(row.type), color: typeColor(row.type) }}>{t}</Tag>
      ),
    },
    {
      title: 'Tokens (30j)', dataIndex: 'tokens', key: 'tokens',
      width: 120, align: 'right' as const,
      render: (v: number) => fmtTokens(v),
      defaultSortOrder: 'descend' as const,
      sorter: (a: any, b: any) => a.tokens - b.tokens,
    },
  ];

  return (
    <Space direction="vertical" className="w-full" size="middle">
      {/* Filters */}
      <div className="bg-gray-50 border border-gray-200 rounded-lg px-4 py-3 flex flex-wrap gap-3 items-center">
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide mr-1">Filtres</span>
        <RangePicker size="small" disabledDate={(d) => d.isAfter(dayjs())}
          onChange={(v) => setDateRange(v ? [v[0]!, v[1]!] : null)} />
        <Select size="small" mode="multiple" allowClear placeholder="Workspace"
          style={{ minWidth: 160 }} options={wsOptions}
          onChange={(v) => setSelectedWs(v)} maxTagCount={2} />
        <Select size="small" mode="multiple" allowClear placeholder="Type de modèle"
          style={{ minWidth: 180 }} options={typeOptions}
          onChange={(v) => setSelectedTypes(v)} maxTagCount={2} />
        <Select size="small" allowClear placeholder="Provider"
          style={{ minWidth: 140 }} options={factoryOptions}
          onChange={(v) => setSelectedFactory(v ?? null)} />
      </div>

      {/* KPI cards */}
      <Row gutter={[16, 16]}>
        <Col xs={12} sm={6}>
          <Card size="small"><Statistic title="Workspaces" value={data.totals.workspaces} prefix={<AppstoreOutlined />} /></Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small"><Statistic title="Membres" value={data.totals.users} prefix={<TeamOutlined />} /></Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic title="Tokens indexés" value={fmtTokens(data.totals.indexed_tokens)} prefix={<DatabaseOutlined />} />
            <div className="text-xs text-gray-400 mt-1">contenu stocké dans les KBs</div>
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <div className="flex items-center gap-2 mb-1">
              <ThunderboltOutlined className="text-indigo-500" />
              <span className="text-gray-500 text-sm">Tokens LLM (30j)</span>
            </div>
            <div className="text-xl font-semibold">{fmtTokens(data.totals.tokens_30d)}</div>
            <div className="mt-2 space-y-1">
              {filteredModelTypes.map((m) => (
                <div key={m.type} className="flex items-center justify-between text-xs">
                  <div className="flex items-center gap-1.5">
                    <span className="inline-block w-2 h-2 rounded-full" style={{ background: typeColor(m.type) }} />
                    <span className="text-gray-500">{m.type_label}</span>
                  </div>
                  <span className="font-medium text-gray-700">{fmtTokens(m.tokens)}</span>
                </div>
              ))}
            </div>
          </Card>
        </Col>
      </Row>

      {/* Charts row 1 */}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <Card title="Tokens LLM par workspace (30j)" size="small">
            <ResponsiveContainer width="100%" height={220}>
              <AreaChart data={filteredDailyByWs}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={shortDate} interval="preserveStartEnd" />
                <YAxis tickFormatter={fmtTokens} tick={{ fontSize: 10 }} width={48} />
                <RechartsTooltip formatter={(v, name) => [fmtTokens(v as number), data.workspace_id_to_name[name as string] ?? (name as string)]} />
                <Legend formatter={(id) => data.workspace_id_to_name[id] ?? id} iconSize={10} />
                {activeWs.map((wsId, i) => (
                  <Area key={wsId} type="monotone" dataKey={wsId} stackId="1"
                    stroke={wsColor(i)} fill={wsColor(i)} fillOpacity={0.6} dot={false} />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card title="Par type de modèle (30j)" size="small">
            <ResponsiveContainer width="100%" height={220}>
              <AreaChart data={filteredDailyByType}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={shortDate} interval="preserveStartEnd" />
                <YAxis tickFormatter={fmtTokens} tick={{ fontSize: 10 }} width={48} />
                <RechartsTooltip formatter={(v, name) =>
                  [fmtTokens(v as number), data.by_model_type.find((m) => m.type === (name as string))?.type_label ?? (name as string)]} />
                <Legend iconSize={10} />
                {activeTypes.map((mt) => (
                  <Area key={mt} type="monotone" dataKey={mt} stackId="1"
                    stroke={typeColor(mt)} fill={typeColor(mt)} fillOpacity={0.7}
                    name={data.by_model_type.find((m) => m.type === mt)?.type_label ?? mt}
                    dot={false} />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          </Card>
        </Col>
      </Row>

      {/* Charts row 2 */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={10}>
          <Card title="Répartition par type" size="small">
            {filteredModelTypes.length > 0 ? (
              <ResponsiveContainer width="100%" height={200}>
                <PieChart>
                  <Pie data={filteredModelTypes} dataKey="tokens" nameKey="type_label"
                    cx="50%" cy="50%" innerRadius={50} outerRadius={80}
                    labelLine={false} label={DonutLabel}>
                    {filteredModelTypes.map((m) => <Cell key={m.type} fill={typeColor(m.type)} />)}
                  </Pie>
                  <RechartsTooltip formatter={(v, name) => [fmtTokens(v as number), name as string]} />
                  <Legend iconSize={10} formatter={(_, entry: any) => entry.payload.type_label} />
                </PieChart>
              </ResponsiveContainer>
            ) : <div className="flex items-center justify-center h-48 text-gray-400 text-sm">Aucune donnée</div>}
          </Card>
        </Col>
        <Col xs={24} sm={14}>
          <Card title="Workspaces — indexés vs LLM" size="small">
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={filteredWsList} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" horizontal={false} />
                <XAxis type="number" tickFormatter={fmtTokens} tick={{ fontSize: 10 }} />
                <YAxis type="category" dataKey="workspace_name" tick={{ fontSize: 11 }} width={90} />
                <RechartsTooltip formatter={(v, k) =>
                  [fmtTokens(v as number), k === 'indexed_tokens' ? 'Tokens indexés' : 'Tokens LLM']} />
                <Legend iconSize={10} formatter={(k) => k === 'indexed_tokens' ? 'Tokens indexés' : 'Tokens LLM'} />
                <Bar dataKey="indexed_tokens" fill="#10b981" radius={[0, 2, 2, 0]} />
                <Bar dataKey="tokens" fill="#6366f1" radius={[0, 2, 2, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </Card>
        </Col>
      </Row>

      {/* Tables */}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card title="Workspaces" size="small">
            <Table columns={wsColumns} dataSource={filteredWsList} rowKey="workspace_id" size="small" pagination={false} />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="Modèles utilisés" size="small">
            <Table columns={modelColumns} dataSource={filteredModels}
              rowKey={(r) => `${r.model}-${r.type}`} size="small"
              pagination={{ pageSize: 6, hideOnSinglePage: true }} />
          </Card>
        </Col>
      </Row>
    </Space>
  );
}

function formatTime(t: string | number | null) {
  if (!t) return '—';
  const ms = typeof t === 'number' && t < 1e11 ? t * 1000 : t;
  return dayjs(ms).format('DD/MM/YYYY HH:mm');
}

function OrgArchivesTab({ orgId }: { orgId: string }) {
  const { message } = App.useApp();
  const [data, setData] = useState<{ workspaces: any[]; users: any[] }>({ workspaces: [], users: [] });
  const [loading, setLoading] = useState(true);
  const [restoring, setRestoring] = useState<string | null>(null);

  const fetch = () => {
    setLoading(true);
    api.get(`/orgs/${orgId}/archives`)
      .then((r) => setData(r.data))
      .catch(() => setData({ workspaces: [], users: [] }))
      .finally(() => setLoading(false));
  };

  useEffect(fetch, [orgId]);

  const restoreWs = async (wsId: string, name: string) => {
    setRestoring(wsId);
    try {
      await api.post(`/orgs/${orgId}/archives/workspaces/${wsId}/restore`);
      message.success(`Workspace "${name}" restauré`);
      fetch();
    } catch (err: any) {
      message.error(err?.response?.data?.detail ?? 'Erreur lors de la restauration');
    } finally {
      setRestoring(null);
    }
  };

  const restoreUser = async (uid: string, email: string) => {
    setRestoring(uid);
    try {
      await api.post(`/orgs/${orgId}/archives/users/${uid}/restore`);
      message.success(`Utilisateur "${email}" restauré`);
      fetch();
    } catch (err: any) {
      message.error(err?.response?.data?.detail ?? 'Erreur lors de la restauration');
    } finally {
      setRestoring(null);
    }
  };

  const wsColumns = [
    { title: 'Nom', dataIndex: 'name', key: 'name' },
    { title: 'Archivé le', dataIndex: 'create_time', key: 'create_time', width: 160, render: formatTime },
    {
      title: '',
      key: 'actions',
      width: 120,
      render: (_: unknown, r: any) => (
        <Button
          size="small"
          icon={<UndoOutlined />}
          loading={restoring === r.id}
          onClick={() => restoreWs(r.id, r.name)}
        >
          Restaurer
        </Button>
      ),
    },
  ];

  const userColumns = [
    { title: 'Email', dataIndex: 'email', key: 'email' },
    { title: 'Nom', dataIndex: 'nickname', key: 'nickname' },
    { title: 'Archivé le', dataIndex: 'create_time', key: 'create_time', width: 160, render: formatTime },
    {
      title: '',
      key: 'actions',
      width: 120,
      render: (_: unknown, r: any) => (
        <Button
          size="small"
          icon={<UndoOutlined />}
          loading={restoring === r.id}
          onClick={() => restoreUser(r.id, r.email)}
        >
          Restaurer
        </Button>
      ),
    },
  ];

  if (loading) return <Spin className="flex justify-center mt-8" />;

  const isEmpty = data.workspaces.length === 0 && data.users.length === 0;

  if (isEmpty) {
    return (
      <Card>
        <div className="text-center py-10 text-gray-400">
          <InboxOutlined className="text-4xl mb-3" />
          <p>Aucun élément archivé dans cette organisation.</p>
        </div>
      </Card>
    );
  }

  return (
    <Space direction="vertical" className="w-full" size="large">
      {data.workspaces.length > 0 && (
        <Card title={<span><AppstoreOutlined /> Workspaces archivés ({data.workspaces.length})</span>}>
          <Table columns={wsColumns} dataSource={data.workspaces} rowKey="id" pagination={false} size="small" />
        </Card>
      )}
      {data.users.length > 0 && (
        <Card title={<span><TeamOutlined /> Utilisateurs archivés ({data.users.length})</span>}>
          <Table columns={userColumns} dataSource={data.users} rowKey="id" pagination={false} size="small" />
        </Card>
      )}
    </Space>
  );
}

interface OrgDetail {
  id: string;
  name: string;
  slug: string;
  max_users: number;
  max_workspaces: number;
  max_datasets: number;
  max_documents: number;
  max_storage_gb: number;
}

interface OrgStats {
  quotas: Record<string, { current: number; max: number }>;
}

interface WsQuota {
  workspace_id: string;
  workspace_name: string;
  enabled: boolean;
  quota_exceeded: boolean;
  allow_overage: boolean;
  current_usage: number;
  limit: number;
  period_start: string | null;
  period_end: string | null;
}

interface OrgQuota {
  org_id: string;
  org_name: string;
  max_tokens_monthly: number;
  allow_overage: boolean;
  current_period_start: string | null;
  current_period_end: string | null;
  workspaces: WsQuota[];
  overage_today_workspace_ids: string[];
}

interface BillingSummary {
  month: string;
  code: { teams: { team_id: string; name: string; bu: string; archived: boolean; spend_eur: number; tokens: number | null }[];
          total_eur: number; total_tokens: number | null };
  rag: { workspaces: { workspace_id: string; name: string; bu: string; tokens: number }[];
         total_tokens: number; monthly_fee_eur: number | null };
  total_eur: number;
}

export default function OrgDetailPage() {
  const { orgId } = useParams<{ orgId: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { message, modal } = App.useApp();
  const { user } = useAuthStore();
  const [org, setOrg] = useState<OrgDetail | null>(null);
  const [stats, setStats] = useState<OrgStats | null>(null);
  const [quota, setQuota] = useState<OrgQuota | null>(null);
  const [loading, setLoading] = useState(true);
  const [archiveModalOpen, setArchiveModalOpen] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [purgeModalOpen, setPurgeModalOpen] = useState(false);
  const [purgeConfirmText, setPurgeConfirmText] = useState('');
  const [purging, setPurging] = useState(false);
  const [quotaForm, setQuotaForm] = useState<{ max_tokens_monthly: number; allow_overage: boolean; rag_monthly_fee_eur: number | null }>({ max_tokens_monthly: 0, allow_overage: true, rag_monthly_fee_eur: null });
  const [savingQuota, setSavingQuota] = useState(false);
  const [resourceForm, setResourceForm] = useState<{ max_users: number; max_workspaces: number; max_datasets: number; max_documents: number; max_storage_gb: number } | null>(null);
  const [savingResources, setSavingResources] = useState(false);
  const [codeForm] = Form.useForm();
  const [codeLoading, setCodeLoading] = useState(true);
  const [codeError, setCodeError] = useState(false);
  const [codeBudgetPeriod, setCodeBudgetPeriod] = useState('1mo');

  const handleArchive = async () => {
    setArchiving(true);
    try {
      await api.delete(`/orgs/${orgId}`);
      message.success('Organisation archived');
      navigate('/organisations');
    } catch (err: any) {
      message.error(err?.response?.data?.detail ?? 'Failed to archive organisation');
    } finally {
      setArchiving(false);
      setArchiveModalOpen(false);
    }
  };

  const handlePurge = async () => {
    if (purgeConfirmText !== 'DELETE') return;
    setPurging(true);
    try {
      await api.delete(`/orgs/${orgId}/purge?confirm=DELETE`);
      message.success('Organisation permanently deleted');
      navigate('/organisations');
    } catch (err: any) {
      message.error(err?.response?.data?.detail ?? 'Failed to purge organisation');
    } finally {
      setPurging(false);
      setPurgeModalOpen(false);
      setPurgeConfirmText('');
    }
  };

  const refreshStats = () => {
    if (!orgId) return;
    api.get(`/orgs/${orgId}/stats`).then((r) => setStats(r.data));
  };

  const handleSaveResources = async () => {
    if (!orgId || !resourceForm) return;
    setSavingResources(true);
    try {
      await api.put(`/orgs/${orgId}`, resourceForm);
      message.success('Quotas de ressources enregistrés');
      const [o, st] = await Promise.all([api.get(`/orgs/${orgId}`), api.get(`/orgs/${orgId}/stats`)]);
      setOrg(o.data); setStats(st.data);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg ?? 'Échec de l\'enregistrement');
    } finally { setSavingResources(false); }
  };

  const handleSaveQuota = async () => {
    if (!orgId) return;
    setSavingQuota(true);
    try {
      await api.patch(`/orgs/${orgId}/quota`, quotaForm);
      message.success('Paramètres de quota enregistrés');
      refreshQuota();
    } catch (err: any) {
      message.error(err?.response?.data?.detail ?? 'Failed to save quota');
    } finally {
      setSavingQuota(false);
    }
  };

  const handleResetPeriod = async () => {
    if (!orgId) return;
    modal.confirm({
      title: 'Reset billing period?',
      content: 'This will reset the current period to the current calendar month and invalidate quota caches.',
      onOk: async () => {
        try {
          await api.patch(`/orgs/${orgId}/quota`, { reset_period: true });
          message.success('Billing period reset to current month');
          refreshQuota();
        } catch (err: any) {
          message.error(err?.response?.data?.detail ?? 'Failed to reset period');
        }
      },
    });
  };

  // Facturation : exports CSV mensuels des deux produits, un seul endroit.
  const downloadCsv = async (path: string, filename: string, month?: string) => {
    try {
      const res = await api.get(path, { params: month ? { month } : {}, responseType: 'blob' });
      const url = URL.createObjectURL(res.data as Blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      message.error("Échec de l'export CSV");
    }
  };
  const prevMonthStr = () => {
    const d = new Date(); d.setMonth(d.getMonth() - 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
  };
  const [billingMonth, setBillingMonth] = useState<string | undefined>(undefined); // undefined = mois courant
  const [billing, setBilling] = useState<BillingSummary | null>(null);
  useEffect(() => {
    if (!orgId) return;
    api.get(`/orgs/${orgId}/billing/summary`, { params: billingMonth ? { month: billingMonth } : {} })
      .then((res) => setBilling(res.data))
      .catch(() => setBilling(null));
  }, [orgId, billingMonth]);

  const refreshQuota = useCallback(() => {
    if (!orgId) return;
    api.get(`/orgs/${orgId}/quota`).then((r) => {
      setQuota(r.data);
      setQuotaForm({ max_tokens_monthly: r.data.max_tokens_monthly ?? 0, allow_overage: r.data.allow_overage ?? true, rag_monthly_fee_eur: r.data.rag_monthly_fee_eur ?? null });
    }).catch(() => {});
  }, [orgId]);

  useEffect(() => {
    if (!orgId || !user?.is_superuser) return;
    setCodeLoading(true);
    setCodeError(false);
    api.get(`/orgs/${orgId}/code/overview`).then((r) => {
      codeForm.setFieldsValue({
        enabled: r.data.entitlement?.status === 'active',
        org_code_budget: r.data.entitlement?.org_code_budget ?? 0,
      });
      setCodeBudgetPeriod(r.data.entitlement?.budget_period ?? '1mo');
    }).catch(() => {
      setCodeError(true);
      message.error("Impossible de charger l'entitlement");
    }).finally(() => setCodeLoading(false));
  }, [orgId, user?.is_superuser, codeForm, message]);

  useEffect(() => {
    if (!orgId) return;
    setLoading(true);
    Promise.all([
      api.get(`/orgs/${orgId}`),
      api.get(`/orgs/${orgId}/stats`),
      api.get(`/orgs/${orgId}/quota`),
    ])
      .then(([orgRes, statsRes, quotaRes]) => {
        setOrg(orgRes.data);
        setResourceForm({ max_users: orgRes.data.max_users, max_workspaces: orgRes.data.max_workspaces,
          max_datasets: orgRes.data.max_datasets, max_documents: orgRes.data.max_documents, max_storage_gb: orgRes.data.max_storage_gb });
        setStats(statsRes.data);
        setQuota(quotaRes.data);
        setQuotaForm({ max_tokens_monthly: quotaRes.data.max_tokens_monthly ?? 0, allow_overage: quotaRes.data.allow_overage ?? true, rag_monthly_fee_eur: quotaRes.data.rag_monthly_fee_eur ?? null });
      })
      .finally(() => setLoading(false));
  }, [orgId]);

  if (loading) return <Spin size="large" className="flex justify-center mt-20" />;
  if (!org) return <div>Organisation not found</div>;

  const quotaItems = stats?.quotas
    ? Object.entries(stats.quotas).map(([key, val]) => ({
        key,
        label: key.charAt(0).toUpperCase() + key.slice(1),
        current: val.current,
        max: val.max,
        percent: val.max > 0 ? Math.round((val.current / val.max) * 100) : 0,
      }))
    : [];

  const getQuota = (key: string) => stats?.quotas?.[key];
  const usersQ = getQuota('users');
  const wsQ = getQuota('workspaces');
  const datasetsQ = getQuota('datasets');
  const docsQ = getQuota('documents');

  return (
    <div>
      <Breadcrumb
        className="mb-3"
        items={[
          { title: <Link to="/"><HomeOutlined /></Link> },
          { title: <Link to="/organisations">Organisations</Link> },
          { title: org.name },
        ]}
      />

      <div className="flex items-start justify-between mb-6">
        <div>
          <div className="flex items-center gap-3">
            <Title level={3} className="!mb-0">{org.name}</Title>
            <Tag>{org.slug}</Tag>
          </div>
          <Text type="secondary" className="text-xs">
            ID&nbsp;<Text code copyable={{ text: org.id }} className="text-xs">{org.id}</Text>
          </Text>
        </div>
      </div>

      <Row gutter={16} className="mb-6">
        <Col xs={12} lg={6}>
          <Card>
            <Statistic
              title="Users"
              value={usersQ?.current ?? 0}
              suffix={`/ ${usersQ?.max ?? '∞'}`}
              prefix={<TeamOutlined />}
            />
          </Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card>
            <Statistic
              title="Workspaces"
              value={wsQ?.current ?? 0}
              suffix={`/ ${wsQ?.max ?? '∞'}`}
              prefix={<AppstoreOutlined />}
            />
          </Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card>
            <Statistic
              title="Datasets"
              value={datasetsQ?.current ?? 0}
              suffix={`/ ${datasetsQ?.max ?? '∞'}`}
              prefix={<DatabaseOutlined />}
            />
          </Card>
        </Col>
        <Col xs={12} lg={6}>
          <Card>
            <Statistic
              title="Documents"
              value={docsQ?.current ?? 0}
              suffix={`/ ${docsQ?.max ?? '∞'}`}
              prefix={<FileOutlined />}
            />
          </Card>
        </Col>
      </Row>

      <Tabs
        type="line"
        size="large"
        activeKey={searchParams.get('tab') ?? 'workspaces'}
        onChange={(k) => setSearchParams((prev) => { prev.set('tab', k); return prev; }, { replace: true })}
        items={[
          {
            key: 'workspaces',
            label: <span><AppstoreOutlined /> Workspaces</span>,
            children: <WorkspacesPage orgId={orgId} onChange={refreshStats} />,
          },
          {
            key: 'members',
            label: <span><TeamOutlined /> Members</span>,
            children: <MembersPage orgId={orgId} onChange={refreshStats} />,
          },
          {
            key: 'quotas',
            label: 'Quotas',
            children: (
              <div className="space-y-4">
                {/* Resource quotas */}
                <Card title="Resource limits">
                  {quotaItems.map((q) => (
                    <div key={q.key} className="mb-4">
                      <div className="flex justify-between mb-1">
                        <span className="font-medium">{q.label}</span>
                        <span className="text-gray-500">{q.current} / {q.max}</span>
                      </div>
                      <Progress
                        percent={q.percent}
                        status={q.percent >= 90 ? 'exception' : q.percent >= 75 ? 'active' : 'normal'}
                        showInfo={false}
                      />
                    </div>
                  ))}
                </Card>

                {/* Quotas de ressources (superuser) */}
                {user?.is_superuser && resourceForm && (
                  <Card className="mb-4" title="Quotas de ressources"
                    extra={<Button size="small" type="primary" loading={savingResources} onClick={handleSaveResources}>Enregistrer</Button>}>
                    <div className="flex flex-wrap gap-6">
                    <div className="flex flex-col gap-1">
                      <span className="text-sm text-gray-600">Utilisateurs{stats?.quotas?.users?.current != null ? <span className="text-xs text-gray-400 ml-1">{`(utilisé : ${stats.quotas.users.current})`}</span> : null}</span>
                      <InputNumber min={1} value={resourceForm.max_users} style={{ width: 150 }}
                        onChange={(v) => setResourceForm(f => f ? { ...f, max_users: v ?? 1 } : f)} />
                    </div>
                    <div className="flex flex-col gap-1">
                      <span className="text-sm text-gray-600">Workspaces{stats?.quotas?.workspaces?.current != null ? <span className="text-xs text-gray-400 ml-1">{`(utilisé : ${stats.quotas.workspaces.current})`}</span> : null}</span>
                      <InputNumber min={1} value={resourceForm.max_workspaces} style={{ width: 150 }}
                        onChange={(v) => setResourceForm(f => f ? { ...f, max_workspaces: v ?? 1 } : f)} />
                    </div>
                    <div className="flex flex-col gap-1">
                      <span className="text-sm text-gray-600">Datasets{stats?.quotas?.datasets?.current != null ? <span className="text-xs text-gray-400 ml-1">{`(utilisé : ${stats.quotas.datasets.current})`}</span> : null}</span>
                      <InputNumber min={1} value={resourceForm.max_datasets} style={{ width: 150 }}
                        onChange={(v) => setResourceForm(f => f ? { ...f, max_datasets: v ?? 1 } : f)} />
                    </div>
                    <div className="flex flex-col gap-1">
                      <span className="text-sm text-gray-600">Documents{stats?.quotas?.documents?.current != null ? <span className="text-xs text-gray-400 ml-1">{`(utilisé : ${stats.quotas.documents.current})`}</span> : null}</span>
                      <InputNumber min={1} value={resourceForm.max_documents} style={{ width: 150 }}
                        onChange={(v) => setResourceForm(f => f ? { ...f, max_documents: v ?? 1 } : f)} />
                    </div>
                    <div className="flex flex-col gap-1">
                      <span className="text-sm text-gray-600">Stockage (Go)</span>
                      <InputNumber min={1} value={resourceForm.max_storage_gb} style={{ width: 150 }}
                        onChange={(v) => setResourceForm(f => f ? { ...f, max_storage_gb: v ?? 1 } : f)} />
                    </div>
                    </div>
                  </Card>
                )}

                {/* Token quota */}
                <Card
                  title={<span>Quota de tokens {quota?.overage_today_workspace_ids?.length ? <Tag color="red" icon={<WarningOutlined />}>Dépassement aujourd'hui</Tag> : null}</span>}
                  extra={user?.is_superuser && (
                    <Space>
                      <Button size="small" onClick={handleResetPeriod}>Réinitialiser la période</Button>
                      <Button size="small" type="primary" loading={savingQuota} onClick={handleSaveQuota}>Enregistrer</Button>
                    </Space>
                  )}
                >
                  {quota?.current_period_start && (
                    <div className="text-xs text-gray-400 mb-3">
                      Période : {quota.current_period_start} → {quota.current_period_end}
                    </div>
                  )}

                  {/* Config (superuser only) */}
                  {user?.is_superuser && (
                    <div className="flex items-center gap-6 mb-4 p-3 bg-gray-50 rounded">
                      <div className="flex items-center gap-2">
                        <span className="text-sm text-gray-600">Limite mensuelle (tokens)</span>
                        <Tooltip title="0 = unlimited">
                          <InputNumber
                            min={0}
                            step={1_000_000}
                            value={quotaForm.max_tokens_monthly}
                            onChange={(v) => setQuotaForm(f => ({ ...f, max_tokens_monthly: v ?? 0 }))}
                            formatter={(v) => String(v).replace(/\B(?=(\d{3})+(?!\d))/g, ',')}
                            parser={(v) => Number(v?.replace(/,/g, '') ?? 0) as any}
                            style={{ width: 160 }}
                          />
                        </Tooltip>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-sm text-gray-600">Dépassement autorisé</span>
                        <Switch
                          checked={quotaForm.allow_overage}
                          onChange={(v) => setQuotaForm(f => ({ ...f, allow_overage: v }))}
                          checkedChildren="Soft" unCheckedChildren="Hard"
                        />
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-sm text-gray-600">Forfait RAG (€/mois)</span>
                        <Tooltip title="Ligne forfait du relevé de facturation. Vide = non contractualisé. Les tokens restent du fair-use, jamais valorisés.">
                          <InputNumber min={0} value={quotaForm.rag_monthly_fee_eur}
                            onChange={(v) => setQuotaForm(f => ({ ...f, rag_monthly_fee_eur: v ?? null }))}
                            placeholder="—" style={{ width: 120 }} />
                        </Tooltip>
                      </div>
                    </div>
                  )}

                  {/* Global org gauge */}
                  {quota && quota.max_tokens_monthly > 0 && (() => {
                    const totalUsed = quota.workspaces.reduce((sum, ws) => sum + (ws.current_usage || 0), 0);
                    const pct = Math.min(Math.round((totalUsed / quota.max_tokens_monthly) * 100), 100);
                    const exceeded = totalUsed >= quota.max_tokens_monthly;
                    return (
                      <div className="mb-6 p-3 border rounded bg-gray-50">
                        <div className="flex justify-between mb-1">
                          <span className="font-semibold text-base">Total organisation</span>
                          <span className="text-gray-600 text-sm font-medium">
                            {fmtTokens(totalUsed)} / {fmtTokens(quota.max_tokens_monthly)}
                            <span className="ml-2 text-gray-400">({pct}%)</span>
                          </span>
                        </div>
                        <Progress
                          percent={pct}
                          status={pct >= 100 ? 'exception' : pct >= 80 ? 'active' : 'normal'}
                          showInfo={false}
                          strokeColor={pct >= 100 ? '#ef4444' : pct >= 80 ? '#f59e0b' : '#6366f1'}
                          size={12}
                        />
                        {exceeded && (
                          <div className="mt-1 text-xs text-red-500 flex items-center gap-1">
                            <WarningOutlined />
                            {quota.allow_overage ? 'Quota dépassé — dépassement toléré (limite souple)' : 'Quota dépassé — requêtes bloquées (limite stricte)'}
                          </div>
                        )}
                      </div>
                    );
                  })()}

                  {/* Per-workspace gauges */}
                  {quota?.workspaces?.length ? quota.workspaces.map((ws) => {
                    const isOverage = quota.overage_today_workspace_ids.includes(ws.workspace_id);
                    const pct = ws.enabled && ws.limit > 0 ? Math.min(Math.round((ws.current_usage / ws.limit) * 100), 100) : 0;
                    return (
                      <div key={ws.workspace_id} className="mb-4">
                        <div className="flex justify-between mb-1">
                          <span className="font-medium flex items-center gap-2">
                            {ws.workspace_name}
                            {isOverage && <Tag color="orange" icon={<WarningOutlined />}>overage</Tag>}
                            {ws.quota_exceeded && !ws.allow_overage && <Tag color="red">hard limit</Tag>}
                            {ws.quota_exceeded && ws.allow_overage && <Tag color="orange">soft limit</Tag>}
                            {!ws.enabled && <Tag color="default">no quota</Tag>}
                          </span>
                          <span className="text-gray-500 text-sm">
                            {fmtTokens(ws.current_usage)}
                            {ws.enabled && ws.limit > 0 && quota && (
                              <span className="text-xs text-gray-400 ml-1">
                                ({Math.round((ws.current_usage / quota.max_tokens_monthly) * 100)}% org quota)
                              </span>
                            )}
                          </span>
                        </div>
                        {ws.enabled && ws.limit > 0 && (
                          <Progress
                            percent={pct}
                            status={pct >= 100 ? 'exception' : pct >= 80 ? 'active' : 'normal'}
                            showInfo={false}
                            strokeColor={pct >= 100 ? '#ef4444' : pct >= 80 ? '#f59e0b' : '#6366f1'}
                          />
                        )}
                      </div>
                    );
                  }) : (
                    <Alert message="Aucun workspace dans cette organisation" type="info" showIcon />
                  )}
                </Card>
              </div>
            ),
          },
          {
            key: 'code',
            label: <span><ThunderboltOutlined /> Code</span>,
            children: <CodePage orgId={orgId} />,
          },
          {
            key: 'billing',
            label: <span><EuroOutlined /> Facturation</span>,
            children: (
              <div>
                {/* Facturation — LE point d'entrée compta : récap consolidé
                    du mois + relevé exportable + détails jour par jour. */}
                <Card title="Facturation" className="mt-4"
                  extra={
                    <Select size="small" style={{ width: 150 }}
                      value={billingMonth ?? 'cur'}
                      onChange={(v) => setBillingMonth(v === 'cur' ? undefined : v)}
                      options={[
                        { value: 'cur', label: 'Mois courant' },
                        { value: prevMonthStr(), label: 'Mois précédent' },
                      ]} />
                  }>
                  {billing ? (
                    <Row gutter={24}>
                      <Col xs={24} lg={12}>
                        <Statistic title={`Code — consommé en ${billing.month}`}
                          value={billing.code.total_eur} suffix="€" precision={2} />
                        <Table size="small" pagination={false} showHeader={false} className="mt-2"
                          rowKey="team_id" dataSource={billing.code.teams}
                          locale={{ emptyText: 'Aucune consommation Code ce mois' }}
                          columns={[
                            { dataIndex: 'name',
                              render: (v: string, r: { bu: string; archived: boolean }) => (
                                <span>{v} {r.bu && <Tag>{r.bu}</Tag>}
                                  {r.archived && <Tag color="default">archivée</Tag>}</span>) },
                            { dataIndex: 'spend_eur', align: 'right' as const,
                              render: (v: number) => `${v} €` },
                          ]} />
                      </Col>
                      <Col xs={24} lg={12}>
                        <Statistic title={`RAG — forfait ${billing.month}`}
                          value={billing.rag.monthly_fee_eur ?? '—'}
                          suffix={billing.rag.monthly_fee_eur != null ? '€' : ''}
                          precision={billing.rag.monthly_fee_eur != null ? 2 : undefined} />
                        <div className="text-gray-400 text-xs mb-1">
                          {billing.rag.total_tokens.toLocaleString()} tokens consommés (fair-use, inclus)
                        </div>
                        <Table size="small" pagination={false} showHeader={false} className="mt-2"
                          rowKey="workspace_id" dataSource={billing.rag.workspaces}
                          locale={{ emptyText: 'Aucune consommation RAG ce mois' }}
                          columns={[
                            { dataIndex: 'name',
                              render: (v: string, r: { bu: string }) => (
                                <span>{v} {r.bu && <Tag>{r.bu}</Tag>}</span>) },
                            { dataIndex: 'tokens', align: 'right' as const,
                              render: (v: number) => v.toLocaleString() },
                          ]} />
                      </Col>
                    </Row>
                  ) : <Card loading bordered={false} />}
                  {billing && (
                    <div className="mt-3 text-right">
                      <Typography.Text strong>
                        Total général : {billing.total_eur.toFixed(2)} €
                      </Typography.Text>
                      <Typography.Text type="secondary" className="ml-2 text-xs">
                        (forfait RAG + conso Code)
                      </Typography.Text>
                    </div>
                  )}
                  <Space className="mt-4" wrap>
                    <Tooltip title="LE document compta : totaux et lignes par team/workspace, les deux produits">
                      <Button type="primary" icon={<DownloadOutlined />}
                        onClick={() => downloadCsv(`/orgs/${orgId}/billing/statement`,
                          `releve-${billingMonth ?? 'mois-courant'}.csv`, billingMonth)}>
                        Relevé mensuel (CSV)
                      </Button>
                    </Tooltip>
                    <Button icon={<DownloadOutlined />}
                      onClick={() => downloadCsv(`/orgs/${orgId}/usage/export`,
                        `rag-usage-${billingMonth ?? 'mois-courant'}.csv`, billingMonth)}>
                      Détail RAG (jour/jour)
                    </Button>
                    <Button icon={<DownloadOutlined />}
                      onClick={() => downloadCsv(`/orgs/${orgId}/code/export`,
                        `code-usage-${billingMonth ?? 'mois-courant'}.csv`, billingMonth)}>
                      Détail Code (jour/jour)
                    </Button>
                  </Space>
                </Card>
              </div>
            ),
          },
          {
            key: 'usage',
            label: <span><BarChartOutlined /> Stats</span>,
            children: <OrgUsageTab orgId={orgId!} />,
          },
          {
            key: 'audit',
            label: <span><AuditOutlined /> Audit</span>,
            children: <AuditPage orgId={orgId} />,
          },
          {
            key: 'archives',
            label: <span><InboxOutlined /> Archives</span>,
            children: <OrgArchivesTab orgId={orgId!} />,
          },
        ]}
      />

      {user?.is_superuser && (
        <Card
          title="Produit Code (entitlement)"
          className="mt-8"
          extra={<Link to={`/code?org=${orgId}`}>Gérer les code-teams →</Link>}
        >
          {codeError && (
            <Alert
              className="mb-3"
              type="error"
              showIcon
              message="Impossible de charger l'entitlement — rechargez avant de modifier"
            />
          )}
          {codeLoading && !codeError && (
            <div className="mb-3 text-gray-400 text-sm">
              <Spin size="small" /> Chargement de l'entitlement…
            </div>
          )}
          <Form
            form={codeForm}
            layout="inline"
            disabled={codeLoading || codeError}
            onFinish={async (v) => {
              try {
                await api.put(`/orgs/${orgId}/code/entitlement`, {
                  status: v.enabled ? 'active' : 'suspended',
                  org_code_budget: v.org_code_budget,
                  budget_period: codeBudgetPeriod,
                });
                message.success('Entitlement mis à jour');
              } catch (err: any) {
                message.error(err?.response?.data?.detail ?? "Échec de la mise à jour de l'entitlement");
              }
            }}
          >
            <Form.Item name="enabled" label="Activé" valuePropName="checked" initialValue={false}>
              <Switch />
            </Form.Item>
            <Form.Item name="org_code_budget" label="Budget (€/mois)" initialValue={0}>
              <InputNumber min={0} />
            </Form.Item>
            <Button htmlType="submit" type="primary" disabled={codeLoading || codeError}>Enregistrer</Button>
          </Form>
        </Card>
      )}

      {user?.is_superuser && (
        <Card
          className="mt-8 border-red-300"
          styles={{ header: { borderBottom: '1px solid #fca5a5', color: '#dc2626' } }}
          title={<span className="text-red-600 font-semibold">Danger Zone</span>}
        >
          <div className="flex items-center justify-between py-3">
            <div>
              <div className="font-medium">Archive this organisation</div>
              <div className="text-sm text-gray-500">
                Désactive l'organisation et libère le slug. Les données sont conservées et récupérables.
              </div>
            </div>
            <Button
              icon={<InboxOutlined />}
              onClick={() => setArchiveModalOpen(true)}
              style={{ color: '#f97316', borderColor: '#f97316' }}
            >
              Archiver
            </Button>
          </div>
          <div className="border-t border-red-100 my-1" />
          <div className="flex items-center justify-between py-3">
            <div>
              <div className="font-medium">Supprimer définitivement</div>
              <div className="text-sm text-gray-500">
                Irréversible. Supprime tous les workspaces, datasets, documents et membres.
              </div>
            </div>
            <Button
              danger
              type="primary"
              icon={<FireOutlined />}
              onClick={() => setPurgeModalOpen(true)}
            >
              Supprimer définitivement
            </Button>
          </div>
        </Card>
      )}

      <Modal
        open={archiveModalOpen}
        title="Archiver l'organisation"
        onCancel={() => setArchiveModalOpen(false)}
        footer={[
          <Button key="cancel" onClick={() => setArchiveModalOpen(false)}>Annuler</Button>,
          <Button key="archive" loading={archiving} onClick={handleArchive}>Archiver</Button>,
        ]}
      >
        <p>
          L'organisation <strong>{org?.name}</strong> sera désactivée. Les données sont conservées
          et l'organisation peut être restaurée manuellement depuis la base de données.
        </p>
      </Modal>

      <Modal
        open={purgeModalOpen}
        title={<span className="text-red-600">Permanently delete organisation</span>}
        onCancel={() => { setPurgeModalOpen(false); setPurgeConfirmText(''); }}
        footer={[
          <Button key="cancel" onClick={() => { setPurgeModalOpen(false); setPurgeConfirmText(''); }}>
            Cancel
          </Button>,
          <Button
            key="purge"
            danger
            type="primary"
            disabled={purgeConfirmText !== 'DELETE'}
            loading={purging}
            onClick={handlePurge}
          >
            Delete permanently
          </Button>,
        ]}
      >
        <p>
          This will permanently delete <strong>{org?.name}</strong> and all its data.
          This action <strong>cannot be undone</strong>.
        </p>
        <p className="mt-3 mb-1 text-sm">
          Type <strong>DELETE</strong> to confirm:
        </p>
        <Input
          value={purgeConfirmText}
          onChange={(e) => setPurgeConfirmText(e.target.value)}
          placeholder="DELETE"
          status={purgeConfirmText && purgeConfirmText !== 'DELETE' ? 'error' : undefined}
        />
      </Modal>
    </div>
  );
}
