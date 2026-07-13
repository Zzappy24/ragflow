import { useEffect, useState } from 'react';
import { Card, Row, Col, Statistic, Spin, Table, Select, Tag, Tooltip as AntTooltip, Progress } from 'antd';
import { WarningOutlined } from '@ant-design/icons';
import dayjs, { Dayjs } from 'dayjs';
import { DatePicker } from 'antd';
import {
  BankOutlined, AppstoreOutlined, TeamOutlined,
  ThunderboltOutlined, DatabaseOutlined,
} from '@ant-design/icons';
import {
  AreaChart, Area, BarChart, Bar, PieChart, Pie,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  Cell, Legend,
} from 'recharts';
import api from '@/lib/api';
import { useAuthStore } from '@/stores/auth';
import { Link } from 'react-router-dom';

const { RangePicker } = DatePicker;

// ── Constants ─────────────────────────────────────────────────────────────────

const MODEL_TYPE_COLORS: Record<string, string> = {
  chat: '#6366f1',
  embedding: '#10b981',
  rerank: '#f59e0b',
  image2text: '#ec4899',
  tts: '#f97316',
  speech2text: '#06b6d4',
  asr: '#06b6d4',
  ocr: '#8b5cf6',
  unknown: '#9ca3af',
};

const WS_COLORS = ['#6366f1', '#10b981', '#f59e0b', '#ec4899', '#f97316', '#06b6d4', '#8b5cf6', '#ef4444'];

function typeColor(t: string) { return MODEL_TYPE_COLORS[t] ?? '#9ca3af'; }
function wsColor(i: number) { return WS_COLORS[i % WS_COLORS.length]; }

const ORG_COLORS = ['#6366f1', '#8b5cf6', '#a78bfa', '#c4b5fd', '#ddd6fe'];

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n ?? 0);
}

function shortDate(d: string) {
  const p = d.split('-');
  return `${p[2]}/${p[1]}`;
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface DailyItem { date: string; tokens: number; [key: string]: number | string; }
interface ModelItem { model: string; type: string; type_label: string; factory: string; tokens: number; }
interface ModelTypeItem { type: string; type_label: string; tokens: number; }
interface FactoryItem { factory: string; tokens: number; }
interface OrgItem { org_id: string; org_name: string; tokens: number; indexed_tokens: number; workspaces: number; users: number; }
interface OrgQuotaSummary { org_id: string; org_name: string; max_tokens_monthly: number; allow_overage: boolean; total_used: number; pct: number; quota_exceeded: boolean; enabled: boolean; current_period_start: string | null; current_period_end: string | null; }
interface WsItem { workspace_id: string; workspace_name: string; tokens: number; indexed_tokens: number; users: number; }

interface GlobalStats {
  totals: { orgs: number; workspaces: number; users: number; active_users_15m: number; tokens_30d: number; indexed_tokens: number };
  daily_tokens: DailyItem[];
  daily_by_model_type_per_factory: Record<string, DailyItem[]>;
  factories: string[];
  model_types: string[];
  by_org: OrgItem[];
  by_model: ModelItem[];
  by_model_type: ModelTypeItem[];
  by_factory: FactoryItem[];
}

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
  by_factory: FactoryItem[];
}

// ── Shared helpers ────────────────────────────────────────────────────────────

function filterByRange<T extends { date: string }>(items: T[], range: [Dayjs, Dayjs] | null): T[] {
  if (!range) return items;
  const [s, e] = range;
  return items.filter((d) => {
    const day = dayjs(d.date);
    return !day.isBefore(s, 'day') && !day.isAfter(e, 'day');
  });
}

function FiltersBar({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-gray-50 border border-gray-200 rounded-lg px-4 py-3 mb-6 flex flex-wrap gap-3 items-center">
      <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide mr-1">Filtres</span>
      {children}
    </div>
  );
}

function KpiCard({ title, value, icon, sub }: { title: string; value: string | number; icon: React.ReactNode; sub?: string }) {
  return (
    <Card size="small" className="h-full">
      <Statistic title={title} value={value} prefix={icon} />
      {sub && <div className="text-xs text-gray-400 mt-1">{sub}</div>}
    </Card>
  );
}

function ActiveUsersCard({ count }: { count: number }) {
  return (
    <Card size="small" className="h-full">
      <div className="text-gray-500 text-sm mb-1">En ligne</div>
      <div className="flex items-center gap-2">
        <span className="relative flex h-2.5 w-2.5">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
          <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-green-500" />
        </span>
        <span className="text-2xl font-semibold text-gray-800">{count}</span>
      </div>
      <div className="text-xs text-gray-400 mt-1">15 dernières minutes</div>
    </Card>
  );
}

function TokenBreakdownCard({ byType, dateRange, totalFiltered, total30d }: {
  byType: ModelTypeItem[];
  dateRange: [Dayjs, Dayjs] | null;
  totalFiltered: number;
  total30d: number;
}) {
  const total = dateRange ? totalFiltered : total30d;
  return (
    <Card size="small" className="h-full">
      <div className="flex items-center gap-2 mb-2">
        <ThunderboltOutlined className="text-indigo-500" />
        <span className="text-gray-500 text-sm">{dateRange ? 'Tokens LLM (période)' : 'Tokens LLM (30j)'}</span>
      </div>
      <div className="text-2xl font-semibold text-gray-800 mb-3">{fmtTokens(total)}</div>
      <div className="space-y-1">
        {byType.map((m) => (
          <div key={m.type} className="flex items-center justify-between text-xs">
            <div className="flex items-center gap-1.5">
              <span className="inline-block w-2 h-2 rounded-full flex-shrink-0" style={{ background: typeColor(m.type) }} />
              <span className="text-gray-500">{m.type_label}</span>
            </div>
            <span className="font-medium text-gray-700">{fmtTokens(m.tokens)}</span>
          </div>
        ))}
        {byType.length === 0 && <div className="text-xs text-gray-400">Aucune donnée</div>}
      </div>
    </Card>
  );
}

// Custom donut label
const RADIAN = Math.PI / 180;
function DonutLabel({ cx, cy, midAngle, innerRadius, outerRadius, percent }: any) {
  if (percent < 0.05) return null;
  const r = innerRadius + (outerRadius - innerRadius) * 0.5;
  const x = cx + r * Math.cos(-midAngle * RADIAN);
  const y = cy + r * Math.sin(-midAngle * RADIAN);
  return (
    <text x={x} y={y} fill="white" textAnchor="middle" dominantBaseline="central" fontSize={11}>
      {`${(percent * 100).toFixed(0)}%`}
    </text>
  );
}

// ── Org-admin dashboard ───────────────────────────────────────────────────────

function OrgDashboard({ orgId, orgName }: { orgId: string; orgName: string }) {
  const [data, setData] = useState<OrgUsage | null>(null);
  const [loading, setLoading] = useState(true);

  // Filters
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

  if (loading) return <Spin className="flex justify-center mt-20" />;
  if (!data) return null;

  // Apply filters
  const activeTypes = selectedTypes.length > 0 ? selectedTypes : data.model_types;
  const activeWs = selectedWs.length > 0 ? selectedWs : data.workspace_ids;

  // Real data from backend — pick the factory-specific series or the aggregate
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

  const totalFiltered = filteredDailyByType.reduce((s, d) =>
    s + activeTypes.reduce((ss, t) => ss + ((d[t] as number) || 0), 0), 0);

  const typeOptions = data.model_types.map((t) => ({
    label: data.by_model_type.find((m) => m.type === t)?.type_label ?? t,
    value: t,
  }));
  const wsOptions = data.workspace_ids.map((id) => ({
    label: data.workspace_id_to_name[id] ?? id,
    value: id,
  }));
  const factoryOptions = data.by_factory.map((f) => ({ label: f.factory || '(direct)', value: f.factory }));

  const wsColumns = [
    {
      title: 'Workspace', dataIndex: 'workspace_name', key: 'ws',
      render: (name: string, row: any) => (
        <Link to={`/workspaces/${row.workspace_id}`} className="text-indigo-600 hover:underline">{name}</Link>
      ),
    },
    { title: 'Membres', dataIndex: 'users', key: 'users', width: 90, align: 'right' as const },
    {
      title: 'Tokens indexés', dataIndex: 'indexed_tokens', key: 'idx',
      width: 130, align: 'right' as const,
      render: (v: number) => <span className="text-emerald-600 font-medium">{fmtTokens(v)}</span>,
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
      title: 'Type', dataIndex: 'type_label', key: 'type',
      width: 120,
      render: (t: string, row: any) => (
        <Tag color="default" style={{ background: typeColor(row.type) + '20', borderColor: typeColor(row.type), color: typeColor(row.type) }}>
          {t}
        </Tag>
      ),
    },
    {
      title: 'Tokens (30j)', dataIndex: 'tokens', key: 'tokens',
      width: 130, align: 'right' as const,
      render: (v: number) => fmtTokens(v),
      defaultSortOrder: 'descend' as const,
      sorter: (a: any, b: any) => a.tokens - b.tokens,
    },
  ];

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold">{orgName}</h2>
      </div>

      <FiltersBar>
        <RangePicker size="small" disabledDate={(d) => d.isAfter(dayjs())}
          onChange={(v) => setDateRange(v ? [v[0]!, v[1]!] : null)} />
        <Select size="small" mode="multiple" allowClear placeholder="Workspace"
          style={{ minWidth: 160 }} options={wsOptions}
          onChange={(v) => setSelectedWs(v)} maxTagCount={2} />
        <Select size="small" mode="multiple" allowClear placeholder="Type de modèle"
          style={{ minWidth: 180 }} options={typeOptions}
          onChange={(v) => setSelectedTypes(v)} maxTagCount={2} />
        <Select size="small" allowClear placeholder="Provider / Factory"
          style={{ minWidth: 160 }} options={factoryOptions}
          onChange={(v) => setSelectedFactory(v ?? null)} />
      </FiltersBar>

      {/* KPI cards */}
      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={12} sm={6}>
          <KpiCard title="Workspaces" value={data.totals.workspaces} icon={<AppstoreOutlined />} />
        </Col>
        <Col xs={12} sm={6}>
          <KpiCard title="Membres" value={data.totals.users} icon={<TeamOutlined />} />
        </Col>
        <Col xs={12} sm={6}>
          <KpiCard title="Tokens indexés" value={fmtTokens(data.totals.indexed_tokens)} icon={<DatabaseOutlined />} sub="contenu stocké dans les KBs" />
        </Col>
        <Col xs={12} sm={6}>
          <TokenBreakdownCard
            byType={filteredModelTypes}
            dateRange={dateRange}
            totalFiltered={totalFiltered}
            total30d={data.totals.tokens_30d}
          />
        </Col>
      </Row>

      {/* Charts row 1: stacked by workspace + stacked by model type */}
      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={24} lg={14}>
          <Card title="Tokens LLM par workspace (30j)" size="small">
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={filteredDailyByWs}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={shortDate} interval="preserveStartEnd" />
                <YAxis tickFormatter={fmtTokens} tick={{ fontSize: 10 }} width={48} />
                <Tooltip formatter={(v, name) => [fmtTokens(v as number), data.workspace_id_to_name[name as string] ?? (name as string)]} />
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
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={filteredDailyByType}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={shortDate} interval="preserveStartEnd" />
                <YAxis tickFormatter={fmtTokens} tick={{ fontSize: 10 }} width={48} />
                <Tooltip formatter={(v, name) =>
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

      {/* Charts row 2: donut + bar by workspace */}
      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={24} sm={10}>
          <Card title="Répartition par type" size="small">
            {filteredModelTypes.length > 0 ? (
              <ResponsiveContainer width="100%" height={220}>
                <PieChart>
                  <Pie data={filteredModelTypes} dataKey="tokens" nameKey="type_label"
                    cx="50%" cy="50%" innerRadius={55} outerRadius={85}
                    labelLine={false} label={DonutLabel}>
                    {filteredModelTypes.map((m) => <Cell key={m.type} fill={typeColor(m.type)} />)}
                  </Pie>
                  <Tooltip formatter={(v, name) => [fmtTokens(v as number), name as string]} />
                  <Legend iconSize={10} formatter={(_, entry: any) => entry.payload.type_label} />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex items-center justify-center h-52 text-gray-400 text-sm">Aucune donnée</div>
            )}
          </Card>
        </Col>
        <Col xs={24} sm={14}>
          <Card title="Workspaces — tokens indexés vs LLM" size="small">
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={filteredWsList} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" horizontal={false} />
                <XAxis type="number" tickFormatter={fmtTokens} tick={{ fontSize: 10 }} />
                <YAxis type="category" dataKey="workspace_name" tick={{ fontSize: 11 }} width={90} />
                <Tooltip formatter={(v, name) =>
                  [fmtTokens(v as number), name === 'indexed_tokens' ? 'Tokens indexés' : 'Tokens LLM (30j)']} />
                <Legend iconSize={10} formatter={(k) => k === 'indexed_tokens' ? 'Tokens indexés' : 'Tokens LLM (30j)'} />
                <Bar dataKey="indexed_tokens" fill="#10b981" radius={[0, 2, 2, 0]} stackId={undefined} />
                <Bar dataKey="tokens" fill="#6366f1" radius={[0, 2, 2, 0]} stackId={undefined} />
              </BarChart>
            </ResponsiveContainer>
          </Card>
        </Col>
      </Row>

      {/* Tables */}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card title="Workspaces" size="small">
            <Table columns={wsColumns} dataSource={filteredWsList}
              rowKey="workspace_id" size="small" pagination={false} />
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
    </div>
  );
}

// ── Superuser global dashboard ────────────────────────────────────────────────

function SuperDashboard() {
  const [data, setData] = useState<GlobalStats | null>(null);
  const [quotas, setQuotas] = useState<Record<string, OrgQuotaSummary>>({});
  const [loading, setLoading] = useState(true);
  const [activeUsers, setActiveUsers] = useState<number>(0);
  const [codeKpis, setCodeKpis] = useState<{ cycle_spend: number | null; active_orgs: number; budget_alerts: number } | null>(null);

  // Filters
  const [dateRange, setDateRange] = useState<[Dayjs, Dayjs] | null>(null);
  const [selectedOrgs, setSelectedOrgs] = useState<string[]>([]);
  const [selectedTypes, setSelectedTypes] = useState<string[]>([]);
  const [selectedFactory, setSelectedFactory] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.get('/stats/overview'),
      api.get('/stats/quotas').catch(() => ({ data: [] })),
    ])
      .then(([overviewRes, quotasRes]) => {
        setData(overviewRes.data);
        setActiveUsers(overviewRes.data.totals.active_users_15m ?? 0);
        const map: Record<string, OrgQuotaSummary> = {};
        for (const q of quotasRes.data) map[q.org_id] = q;
        setQuotas(map);
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    api.get('/code/dashboard').then((r) => setCodeKpis(r.data.kpis)).catch(() => {});
  }, []);

  // Poll active users every 60s — one Redis ZCOUNT, negligible cost
  useEffect(() => {
    const id = setInterval(() => {
      api.get('/stats/active-users')
        .then((r) => setActiveUsers(r.data.active_users_15m ?? 0))
        .catch(() => {});
    }, 60_000);
    return () => clearInterval(id);
  }, []);

  if (loading) return <Spin size="large" className="flex justify-center mt-20" />;
  if (!data) return null;

  const activeTypes = selectedTypes.length > 0 ? selectedTypes : data.model_types;
  const activeOrgs = selectedOrgs.length > 0 ? selectedOrgs : null;

  // Real data from backend — pick factory-specific series or the aggregate
  const dailySeriesForFactory = selectedFactory
    ? (data.daily_by_model_type_per_factory[selectedFactory] ?? data.daily_by_model_type_per_factory['__all__'])
    : data.daily_by_model_type_per_factory['__all__'];
  const filteredDailyByType = filterByRange(dailySeriesForFactory, dateRange);

  const filteredOrgs = activeOrgs ? data.by_org.filter((o) => activeOrgs.includes(o.org_id)) : data.by_org;
  const filteredModels = data.by_model
    .filter((m) => activeTypes.includes(m.type))
    .filter((m) => !selectedFactory || m.factory === selectedFactory);

  // Recompute model type totals from the real factory-scoped data
  const filteredModelTypes: ModelTypeItem[] = selectedFactory
    ? Object.values(
        filteredModels.reduce((acc, m) => {
          if (!acc[m.type]) acc[m.type] = { type: m.type, type_label: m.type_label, tokens: 0 };
          acc[m.type].tokens += m.tokens;
          return acc;
        }, {} as Record<string, ModelTypeItem>)
      ).sort((a, b) => b.tokens - a.tokens)
    : data.by_model_type.filter((m) => activeTypes.includes(m.type));

  const totalFiltered = filteredDailyByType.reduce((s, d) =>
    s + activeTypes.reduce((ss, t) => ss + ((d[t] as number) || 0), 0), 0);

  const typeOptions = data.model_types.map((t) => ({
    label: data.by_model_type.find((m) => m.type === t)?.type_label ?? t,
    value: t,
  }));
  const orgOptions = data.by_org.map((o) => ({ label: o.org_name, value: o.org_id }));
  const factoryOptions = data.by_factory.map((f) => ({ label: f.factory || '(direct)', value: f.factory }));

  const orgColumns = [
    {
      title: 'Organisation', dataIndex: 'org_name', key: 'org',
      render: (name: string, row: any) => (
        <Link to={`/organisations/${row.org_id}`} className="text-indigo-600 hover:underline">{name}</Link>
      ),
    },
    { title: 'WS', dataIndex: 'workspaces', key: 'ws', width: 60, align: 'right' as const },
    { title: 'Users', dataIndex: 'users', key: 'users', width: 70, align: 'right' as const },
    {
      title: 'Indexés', dataIndex: 'indexed_tokens', key: 'idx',
      width: 100, align: 'right' as const,
      render: (v: number) => <span className="text-emerald-600">{fmtTokens(v)}</span>,
      sorter: (a: any, b: any) => a.indexed_tokens - b.indexed_tokens,
    },
    {
      title: 'Tokens LLM (30j)', dataIndex: 'tokens', key: 'tokens',
      width: 130, align: 'right' as const,
      render: (v: number) => <span className="text-indigo-600 font-medium">{fmtTokens(v)}</span>,
      defaultSortOrder: 'descend' as const,
      sorter: (a: any, b: any) => a.tokens - b.tokens,
    },
    {
      title: 'Quota mensuel', key: 'quota', width: 180,
      render: (_: any, row: OrgItem) => {
        const q = quotas[row.org_id];
        if (!q || !q.enabled) return <span className="text-gray-300 text-xs">—</span>;
        const color = q.pct >= 100 ? '#ef4444' : q.pct >= 80 ? '#f59e0b' : '#6366f1';
        return (
          <AntTooltip title={`${fmtTokens(q.total_used)} / ${fmtTokens(q.max_tokens_monthly)} (${q.pct}%)${q.quota_exceeded ? (q.allow_overage ? ' — overage' : ' — bloqué') : ''}`}>
            <div className="flex items-center gap-2">
              <div className="flex-1">
                <Progress percent={q.pct} showInfo={false} strokeColor={color} size={6} />
              </div>
              <span className="text-xs tabular-nums" style={{ color, minWidth: 32 }}>{q.pct}%</span>
              {q.quota_exceeded && <WarningOutlined style={{ color: q.allow_overage ? '#f59e0b' : '#ef4444', fontSize: 12 }} />}
            </div>
          </AntTooltip>
        );
      },
    },
  ];

  const modelColumns = [
    {
      title: 'Modèle', dataIndex: 'model', key: 'model',
      render: (m: string, row: any) => (
        <div>
          <div className="font-medium text-sm truncate max-w-[200px]">{m}</div>
          <div className="text-xs text-gray-400">{row.factory}</div>
        </div>
      ),
    },
    {
      title: 'Type', dataIndex: 'type_label', key: 'type', width: 110,
      render: (t: string, row: any) => (
        <Tag style={{ background: typeColor(row.type) + '20', borderColor: typeColor(row.type), color: typeColor(row.type) }}>
          {t}
        </Tag>
      ),
      filters: data.model_types.map((t) => ({
        text: data.by_model_type.find((m) => m.type === t)?.type_label ?? t,
        value: t,
      })),
      onFilter: (value: any, record: any) => record.type === value,
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
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold">Dashboard global</h2>
      </div>

      <FiltersBar>
        <RangePicker size="small" disabledDate={(d) => d.isAfter(dayjs())}
          onChange={(v) => setDateRange(v ? [v[0]!, v[1]!] : null)} />
        <Select size="small" mode="multiple" allowClear placeholder="Organisation"
          style={{ minWidth: 180 }} options={orgOptions}
          onChange={(v) => setSelectedOrgs(v)} maxTagCount={2} />
        <Select size="small" mode="multiple" allowClear placeholder="Type de modèle"
          style={{ minWidth: 180 }} options={typeOptions}
          onChange={(v) => setSelectedTypes(v)} maxTagCount={2} />
        <Select size="small" allowClear placeholder="Provider / Factory"
          style={{ minWidth: 160 }} options={factoryOptions}
          onChange={(v) => setSelectedFactory(v ?? null)} />
      </FiltersBar>

      {/* KPI cards */}
      <Row gutter={[12, 12]} className="mb-6">
        <Col xs={12} sm={8} lg={3}>
          <KpiCard title="Organisations" value={data.totals.orgs} icon={<BankOutlined />} />
        </Col>
        <Col xs={12} sm={8} lg={3}>
          <KpiCard title="Workspaces" value={data.totals.workspaces} icon={<AppstoreOutlined />} />
        </Col>
        <Col xs={12} sm={8} lg={3}>
          <KpiCard title="Utilisateurs" value={data.totals.users} icon={<TeamOutlined />} />
        </Col>
        <Col xs={12} sm={8} lg={4}>
          <ActiveUsersCard count={activeUsers} />
        </Col>
        <Col xs={12} sm={8} lg={3}>
          <KpiCard title="Tokens indexés" value={fmtTokens(data.totals.indexed_tokens)} icon={<DatabaseOutlined />} sub="contenu stocké" />
        </Col>
        <Col xs={24} sm={12} lg={8}>
          <TokenBreakdownCard
            byType={filteredModelTypes}
            dateRange={dateRange}
            totalFiltered={totalFiltered}
            total30d={data.totals.tokens_30d}
          />
        </Col>
      </Row>

      {codeKpis && (
        <Row gutter={[16, 16]} className="mb-6">
          <Col xs={24} md={8}>
            <Card size="small" title="Produit Code" extra={<Link to="/code">→ ouvrir</Link>}>
              <Row gutter={8}>
                <Col xs={24} sm={8}><Statistic title="Dépensé (cycle)" value={codeKpis.cycle_spend ?? '—'} suffix={codeKpis.cycle_spend != null ? '€' : ''} /></Col>
                <Col xs={24} sm={8}><Statistic title="Orgs actives" value={codeKpis.active_orgs} /></Col>
                <Col xs={24} sm={8}><Statistic title="Alertes" value={codeKpis.budget_alerts}
                  valueStyle={codeKpis.budget_alerts > 0 ? { color: '#cf1322' } : undefined} /></Col>
              </Row>
            </Card>
          </Col>
        </Row>
      )}

      {/* Row 1: stacked area by model type + donut */}
      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={24} lg={16}>
          <Card title="Tokens LLM par type de modèle — 30 jours" size="small">
            <ResponsiveContainer width="100%" height={260}>
              <AreaChart data={filteredDailyByType}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={shortDate} interval="preserveStartEnd" />
                <YAxis tickFormatter={fmtTokens} tick={{ fontSize: 10 }} width={52} />
                <Tooltip
                  formatter={(v, name) =>
                    [fmtTokens(v as number), data.by_model_type.find((m) => m.type === (name as string))?.type_label ?? (name as string)]}
                />
                <Legend iconSize={10}
                  formatter={(name) => data.by_model_type.find((m) => m.type === name)?.type_label ?? name} />
                {activeTypes.map((mt) => (
                  <Area key={mt} type="monotone" dataKey={mt} stackId="1"
                    stroke={typeColor(mt)} fill={typeColor(mt)} fillOpacity={0.75} dot={false} />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="Répartition par type (30j)" size="small" style={{ height: '100%' }}>
            {filteredModelTypes.length > 0 ? (
              <ResponsiveContainer width="100%" height={260}>
                <PieChart>
                  <Pie data={filteredModelTypes} dataKey="tokens" nameKey="type_label"
                    cx="50%" cy="50%" innerRadius={65} outerRadius={100}
                    labelLine={false} label={DonutLabel}>
                    {filteredModelTypes.map((m) => <Cell key={m.type} fill={typeColor(m.type)} />)}
                  </Pie>
                  <Tooltip formatter={(v, name) => [fmtTokens(v as number), name as string]} />
                  <Legend iconSize={10} formatter={(_, entry: any) => entry.payload.type_label} />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex items-center justify-center h-60 text-gray-400 text-sm">Aucune donnée</div>
            )}
          </Card>
        </Col>
      </Row>

      {/* Row 2: top orgs bar + provider bar */}
      <Row gutter={[16, 16]} className="mb-6">
        {filteredOrgs.some((o) => o.tokens > 0) && (
          <Col xs={24} lg={14}>
            <Card title="Tokens LLM par organisation (30j)" size="small">
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={filteredOrgs.slice(0, 10).map((o) => ({
                  name: o.org_name, llm: o.tokens, indexed: o.indexed_tokens,
                }))}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                  <XAxis dataKey="name" tick={{ fontSize: 10 }} />
                  <YAxis tickFormatter={fmtTokens} tick={{ fontSize: 10 }} width={48} />
                  <Tooltip formatter={(v, k) =>
                    [fmtTokens(v as number), k === 'llm' ? 'Tokens LLM' : 'Tokens indexés']} />
                  <Legend iconSize={10} formatter={(k) => k === 'llm' ? 'Tokens LLM' : 'Tokens indexés'} />
                  <Bar dataKey="indexed" fill="#10b981" radius={[3, 3, 0, 0]} />
                  <Bar dataKey="llm" fill="#6366f1" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </Card>
          </Col>
        )}
        {data.by_factory.length > 0 && (
          <Col xs={24} lg={10}>
            <Card title="Tokens par provider (30j)" size="small">
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={data.by_factory.slice(0, 8)} layout="vertical">
                  <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" horizontal={false} />
                  <XAxis type="number" tickFormatter={fmtTokens} tick={{ fontSize: 10 }} />
                  <YAxis type="category" dataKey="factory" tick={{ fontSize: 11 }} width={80} />
                  <Tooltip formatter={(v) => [fmtTokens(v as number), 'Tokens']} />
                  <Bar dataKey="tokens" radius={[0, 3, 3, 0]}>
                    {data.by_factory.slice(0, 8).map((_, i) => (
                      <Cell key={i} fill={ORG_COLORS[i % ORG_COLORS.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </Card>
          </Col>
        )}
      </Row>

      {/* Tables */}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <Card title="Organisations" size="small">
            <Table columns={orgColumns} dataSource={filteredOrgs}
              rowKey="org_id" size="small"
              pagination={{ pageSize: 8, hideOnSinglePage: true }} />
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card title="Modèles utilisés" size="small">
            <Table columns={modelColumns} dataSource={filteredModels}
              rowKey={(r) => `${r.model}-${r.type}`} size="small"
              pagination={{ pageSize: 8, hideOnSinglePage: true }} />
          </Card>
        </Col>
      </Row>
    </div>
  );
}

// ── ws_admin landing — list of own workspaces ─────────────────────────────────

function WsAdminDashboard({ workspaces }: {
  workspaces: Array<{ ws_id: string; ws_name: string; org_id: string; role: string }>;
}) {
  return (
    <Card title="Mes workspaces">
      <Table
        size="small"
        rowKey="ws_id"
        pagination={false}
        dataSource={workspaces}
        columns={[
          {
            title: 'Workspace',
            dataIndex: 'ws_name',
            key: 'name',
            render: (name: string, row: any) => (
              <Link to={`/workspaces/${row.ws_id}`} className="text-indigo-600 hover:underline">
                {name}
              </Link>
            ),
          },
          {
            title: 'Rôle',
            dataIndex: 'role',
            key: 'role',
            width: 120,
            render: (r: string) => <Tag color={r === 'ws_admin' ? 'blue' : 'default'}>{r}</Tag>,
          },
        ]}
      />
    </Card>
  );
}

// ── Root export ───────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const user = useAuthStore((s) => s.user);

  if (!user) return <Spin size="large" className="flex justify-center mt-20" />;

  if (user.is_superuser) return <SuperDashboard />;

  const hasOrgAdminRole = !!user.orgs?.some((o) => o.role === 'org_admin');
  if (hasOrgAdminRole) {
    const org = user.orgs.find((o) => o.role === 'org_admin') ?? user.orgs[0];
    return <OrgDashboard orgId={org.org_id} orgName={org.org_name} />;
  }

  // ws_admin landing: only their own workspaces are visible. Drilling into a
  // workspace opens the same detail page as for org_admin/superuser, with
  // backend RBAC scoping every tab to that workspace.
  const adminWorkspaces = (user.workspaces ?? []).filter(
    (w) => w.role === 'ws_admin',
  );
  if (adminWorkspaces.length > 0) {
    return <WsAdminDashboard workspaces={adminWorkspaces} />;
  }

  return (
    <Card>
      <p className="text-gray-500">
        Bienvenue, {user.email}. Aucun rôle admin n'est configuré sur votre compte.
      </p>
    </Card>
  );
}
