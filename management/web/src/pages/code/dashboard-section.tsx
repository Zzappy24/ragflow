import { useCallback, useEffect, useRef, useState } from 'react';
import { Card, Col, Row, Statistic, Table, Tag, Progress } from 'antd';
import { EuroOutlined, BankOutlined, TeamOutlined, KeyOutlined, WarningOutlined } from '@ant-design/icons';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import api from '@/lib/api';

interface DashboardData {
  kpis: { cycle_spend: number | null; active_orgs: number; teams: number; active_keys: number; budget_alerts: number };
  daily: { date: string; spend: number }[];
  top_orgs: { org_id: string; org_name: string; spend: number }[];
  top_teams: { code_team_id: string; name: string; org_name: string; spend: number; max_budget: number }[];
  last_housekeeping_at: string | null;
}

const REFRESH_MS = 30_000;

export default function CodeDashboardSection() {
  const [data, setData] = useState<DashboardData | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchData = useCallback(() => {
    api.get('/code/dashboard').then((res) => setData(res.data)).catch(() => {});
  }, []);

  useEffect(() => {
    fetchData();
    const start = () => { if (!timer.current) timer.current = setInterval(fetchData, REFRESH_MS); };
    const stop = () => { if (timer.current) { clearInterval(timer.current); timer.current = null; } };
    const onVisibility = () => { if (document.hidden) stop(); else { fetchData(); start(); } };
    start();
    document.addEventListener('visibilitychange', onVisibility);
    return () => { stop(); document.removeEventListener('visibilitychange', onVisibility); };
  }, [fetchData]);

  if (!data) return <Card loading className="mb-4" />;
  const { kpis } = data;

  return (
    <div className="mb-4">
      <Row gutter={12} className="mb-3">
        <Col span={5}><Card size="small"><Statistic title="Dépensé (cycle)" prefix={<EuroOutlined />}
          value={kpis.cycle_spend ?? '—'} suffix={kpis.cycle_spend != null ? '€' : ''} /></Card></Col>
        <Col span={5}><Card size="small"><Statistic title="Orgs actives" prefix={<BankOutlined />} value={kpis.active_orgs} /></Card></Col>
        <Col span={5}><Card size="small"><Statistic title="Teams" prefix={<TeamOutlined />} value={kpis.teams} /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="Clés actives" prefix={<KeyOutlined />} value={kpis.active_keys} /></Card></Col>
        <Col span={5}><Card size="small"><Statistic title="Alertes budget (≥80%)" prefix={<WarningOutlined />}
          value={kpis.budget_alerts} valueStyle={kpis.budget_alerts > 0 ? { color: '#cf1322' } : undefined} /></Card></Col>
      </Row>
      <Row gutter={12}>
        <Col span={12}>
          <Card size="small" title="Dépense par jour (30 j)">
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={data.daily}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" tickFormatter={(d: string) => d.slice(5)} />
                <YAxis />
                <Tooltip formatter={(v) => `${v} €`} />
                <Area type="monotone" dataKey="spend" stroke="#6366f1" fill="#6366f1" fillOpacity={0.25} />
              </AreaChart>
            </ResponsiveContainer>
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small" title="Top organisations">
            <Table rowKey="org_id" size="small" pagination={false} showHeader={false}
              dataSource={data.top_orgs}
              columns={[{ dataIndex: 'org_name' },
                        { dataIndex: 'spend', width: 90, render: (v: number) => `${v} €` }]} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small" title="Top teams">
            <Table rowKey="code_team_id" size="small" pagination={false} showHeader={false}
              dataSource={data.top_teams}
              columns={[
                { dataIndex: 'name', render: (v: string, r) => <>{v} <Tag>{r.org_name}</Tag></> },
                { width: 110, render: (_: unknown, r) => (
                    <Progress size="small" percent={r.max_budget ? Math.min(100, Math.round((r.spend / r.max_budget) * 100)) : 0}
                              status={r.spend >= r.max_budget ? 'exception' : undefined} />) },
              ]} />
          </Card>
        </Col>
      </Row>
      {data.last_housekeeping_at && (
        <div className="text-gray-400 text-xs mt-2">
          Dernier relevé : {new Date(data.last_housekeeping_at).toLocaleString()}
        </div>
      )}
    </div>
  );
}
