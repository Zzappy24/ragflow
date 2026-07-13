import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, App, Button, Card, Col, Row, Statistic, Table, Tag, Progress } from 'antd';
import { EuroOutlined, BankOutlined, TeamOutlined, KeyOutlined, WarningOutlined, SyncOutlined } from '@ant-design/icons';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import api from '@/lib/api';
import { useAuthStore } from '@/stores/auth';

interface DashboardData {
  kpis: {
    cycle_spend: number | null; active_orgs: number; teams: number; active_keys: number; budget_alerts: number;
    tokens_30d: number | null; errors_30d: number | null;
  };
  daily: { date: string; spend: number; tokens: number | null; errors: number | null }[];
  top_orgs: { org_id: string; org_name: string; spend: number }[];
  top_teams: { code_team_id: string; name: string; org_name: string; spend: number; max_budget: number; tokens: number | null }[];
  last_housekeeping_at: string | null;
}

const REFRESH_MS = 30_000;

export const fmtTokens = (n: number): string => {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n ?? 0);
};

export default function CodeDashboardSection() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [reconciling, setReconciling] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const { user } = useAuthStore();
  const { message } = App.useApp();

  const fetchData = useCallback(() => {
    api.get('/code/dashboard')
      .then((res) => { setData(res.data); setLoadFailed(false); })
      .catch(() => setLoadFailed(true));
  }, []);

  useEffect(() => {
    fetchData();
    const start = () => { if (!timer.current) timer.current = setInterval(fetchData, REFRESH_MS); };
    const stop = () => { if (timer.current) { clearInterval(timer.current); timer.current = null; } };
    const onVisibility = () => { if (document.hidden) stop(); else { fetchData(); start(); } };
    // Guard against starting the refresh timer while the tab is mounted but
    // hidden (e.g. opened in a background tab) — no point polling a page
    // nobody is looking at until visibilitychange fires.
    if (!document.hidden) start();
    document.addEventListener('visibilitychange', onVisibility);
    return () => { stop(); document.removeEventListener('visibilitychange', onVisibility); };
  }, [fetchData]);

  // Superuser only (la route est require_superuser) : force un passage
  // reconcile LiteLLM + snapshot tokens sans attendre le tick 15 min du
  // scheduler — utile après un incident gateway (teams/clés en pending).
  const onReconcile = async () => {
    setReconciling(true);
    try {
      const res = await api.post('/code/reconcile');
      const r = res.data;
      message.success(
        `Resynchronisé : ${r.teams_synced ?? 0} team(s), ${r.keys_synced ?? 0} clé(s), ` +
        `${r.teams_snapshotted ?? 0} relevé(s) tokens, ${r.errors ?? 0} erreur(s)`);
      fetchData();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg ?? 'Échec de la resynchronisation');
    } finally {
      setReconciling(false);
    }
  };

  if (loadFailed && !data) {
    return <Alert className="mb-4" type="error" message="Dashboard indisponible"
      description="Impossible de charger les données du tableau de bord Code. Nouvelle tentative automatique en cours." />;
  }
  if (!data) return <Card loading className="mb-4" />;
  const { kpis } = data;
  // Relevés interrompus : scheduler mort (lifespan pas parti — la leçon
  // asgi.py), pod mgmt down, ou DB.lock bloqué. Le spend € reste temps réel
  // mais tokens/erreurs/alertes budget ne bougent plus.
  const staleMs = data.last_housekeeping_at
    ? Date.now() - new Date(data.last_housekeeping_at).getTime() : null;
  const staleMin = staleMs != null ? Math.round(staleMs / 60000) : null;
  const housekeepingStale = staleMin != null && staleMin > 60;

  return (
    <div className="mb-4">
      {housekeepingStale && (
        <Alert className="mb-3" type="warning" showIcon
          message={`Relevés tokens interrompus depuis ${staleMin} min`}
          description="Le housekeeping (15 min) ne tourne plus : vérifier le pod mgmt-backend (ligne « code housekeeping scheduler started » dans ses logs). Dépenses € toujours en temps réel ; tokens, erreurs et alertes budget figés."
          action={user?.is_superuser
            ? <Button size="small" onClick={onReconcile} loading={reconciling}>Relancer</Button>
            : undefined} />
      )}
      <Row gutter={[12, 12]} className="mb-3">
        <Col xs={12} md={8} lg={4}><Card size="small"><Statistic title="Dépensé (cycle)" prefix={<EuroOutlined />}
          value={kpis.cycle_spend ?? '—'} suffix={kpis.cycle_spend != null ? '€' : ''} /></Card></Col>
        <Col xs={12} md={8} lg={3}><Card size="small"><Statistic title="Orgs actives" prefix={<BankOutlined />} value={kpis.active_orgs} /></Card></Col>
        <Col xs={12} md={8} lg={3}><Card size="small"><Statistic title="Teams" prefix={<TeamOutlined />} value={kpis.teams} /></Card></Col>
        <Col xs={12} md={8} lg={3}><Card size="small"><Statistic title="Clés actives" prefix={<KeyOutlined />} value={kpis.active_keys} /></Card></Col>
        <Col xs={12} md={8} lg={3}><Card size="small"><Statistic title="Alertes budget (≥80%)" prefix={<WarningOutlined />}
          value={kpis.budget_alerts} valueStyle={kpis.budget_alerts > 0 ? { color: '#cf1322' } : undefined} /></Card></Col>
        <Col xs={12} md={8} lg={4}><Card size="small"><Statistic title="Tokens (30 j)"
          value={kpis.tokens_30d == null ? '—' : fmtTokens(kpis.tokens_30d)} /></Card></Col>
        <Col xs={12} md={8} lg={4}><Card size="small"><Statistic title="Erreurs (30 j)"
          value={kpis.errors_30d ?? '—'}
          valueStyle={(kpis.errors_30d ?? 0) > 0 ? { color: '#cf1322' } : undefined} /></Card></Col>
      </Row>
      <Row gutter={12}>
        <Col xs={24} lg={12}>
          <Card size="small" title="Dépense par jour (30 j)">
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={data.daily}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" tickFormatter={(d: string) => d.slice(5)} />
                <YAxis yAxisId="left" />
                <YAxis yAxisId="right" orientation="right" tickFormatter={(v: number) => fmtTokens(v)} />
                <Tooltip formatter={(v, name) => (name === 'tokens' ? (v == null ? '—' : fmtTokens(Number(v))) : `${v} €`)} />
                <Area yAxisId="left" type="monotone" dataKey="spend" stroke="#6366f1" fill="#6366f1" fillOpacity={0.25} />
                <Area yAxisId="right" type="monotone" dataKey="tokens" stroke="#10b981" fill="#10b981" fillOpacity={0.15} connectNulls={false} />
              </AreaChart>
            </ResponsiveContainer>
          </Card>
        </Col>
        <Col xs={24} md={12} lg={6}>
          <Card size="small" title="Top organisations">
            <Table rowKey="org_id" size="small" pagination={false} showHeader={false}
              dataSource={data.top_orgs}
              columns={[{ dataIndex: 'org_name' },
                        { dataIndex: 'spend', width: 90, render: (v: number) => `${v} €` }]} />
          </Card>
        </Col>
        <Col xs={24} md={12} lg={6}>
          <Card size="small" title="Top teams">
            <Table rowKey="code_team_id" size="small" pagination={false} showHeader={false}
              dataSource={data.top_teams}
              columns={[
                { dataIndex: 'name', render: (v: string, r) => <>{v} <Tag>{r.org_name}</Tag></> },
                { width: 110, render: (_: unknown, r) => (
                    <Progress size="small" percent={r.max_budget ? Math.min(100, Math.round((r.spend / r.max_budget) * 100)) : 0}
                              status={r.spend >= r.max_budget ? 'exception' : undefined} />) },
                { width: 80, align: 'right' as const, render: (_: unknown, r) => (
                    <span className="text-gray-400 text-xs">{r.tokens == null ? '—' : `${fmtTokens(r.tokens)} tok`}</span>) },
              ]} />
          </Card>
        </Col>
      </Row>
      <div className="flex items-center gap-3 mt-2">
        {data.last_housekeeping_at && (
          <div className="text-gray-400 text-xs">
            Tokens et erreurs relevés toutes les 15 min · dernier relevé
            : {new Date(data.last_housekeeping_at).toLocaleString()} · dépenses € en temps réel
          </div>
        )}
        {user?.is_superuser && (
          <Button size="small" icon={<SyncOutlined spin={reconciling} />} loading={reconciling}
                  onClick={onReconcile}>
            Resynchroniser
          </Button>
        )}
      </div>
    </div>
  );
}
