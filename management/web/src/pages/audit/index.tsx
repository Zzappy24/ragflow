import { useEffect, useState } from 'react';
import { useSearchParams, useParams } from 'react-router-dom';
import { Table, Card, Space, Tag, Tooltip, Typography } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import api from '@/lib/api';
import { AuditFilterBar, AuditFilters } from './filters';

const { Text } = Typography;

interface AuditEntry {
  id: string;
  user_id: string;
  actor_email: string | null;
  action: string;
  status: string | null;
  org_id: string | null;
  workspace_id: string | null;
  resource_type: string | null;
  resource_id: string | null;
  details: Record<string, any> | null;
  diff: { before?: Record<string, any>; after?: Record<string, any>; role_deleted?: string } | null;
  ip_address: string | null;
  create_time: string | number;
}

function formatTime(t: string | number | null) {
  if (!t) return '—';
  const ms = typeof t === 'number' && t < 1e11 ? t * 1000 : t;
  return dayjs(ms).format('DD/MM/YYYY HH:mm:ss');
}

function DiffCell({ diff }: { diff: AuditEntry['diff'] }) {
  if (!diff) return null;
  const entries: string[] = [];
  if (diff.role_deleted) entries.push(`rôle supprimé: ${diff.role_deleted}`);
  if (diff.before && diff.after) {
    Object.keys(diff.after).forEach((k) => {
      entries.push(`${k}: ${diff.before![k]} → ${diff.after![k]}`);
    });
  }
  if (!entries.length) return null;
  return (
    <Tooltip title={entries.join('\n')}>
      <Tag color="blue" className="cursor-default">diff</Tag>
    </Tooltip>
  );
}

function DetailsCell({ details }: { details: AuditEntry['details'] }) {
  if (!details || !Object.keys(details).length) return null;
  const summary = Object.entries(details)
    .filter(([k, v]) => k !== 'target_display_name' && v !== null && v !== undefined)
    .map(([k, v]) => `${k}: ${v}`)
    .join('\n');
  if (!summary) return null;
  return (
    <Tooltip title={summary}>
      <Tag className="cursor-default">détails</Tag>
    </Tooltip>
  );
}

export default function AuditPage({ orgId: orgIdProp, wsId: wsIdProp }: { orgId?: string; wsId?: string } = {}) {
  const [searchParams] = useSearchParams();
  const params = useParams<{ orgId?: string; wsId?: string }>();
  const orgId = orgIdProp || params.orgId || searchParams.get('org');
  const wsId = wsIdProp || params.wsId || searchParams.get('ws');
  const [logs, setLogs] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState<AuditFilters>({});

  const scope = wsId ? 'ws' : 'org';
  const scopeId = wsId || orgId || '';

  const handleFiltersChange = (f: AuditFilters) => {
    setPage(1);
    setFilters(f);
  };

  const fetchLogs = () => {
    if (!scopeId) { setLoading(false); return; }
    setLoading(true);
    const url = scope === 'ws' ? `/workspaces/${scopeId}/audit` : `/orgs/${scopeId}/audit`;
    api
      .get(url, { params: { page, page_size: 50, ...filters } })
      .then((res) => setLogs(res.data.items || []))
      .finally(() => setLoading(false));
  };

  useEffect(fetchLogs, [scopeId, page, filters]);

  const columns = [
    {
      title: 'Date',
      dataIndex: 'create_time',
      width: 160,
      render: (t: string | number) => formatTime(t),
    },
    {
      title: 'Acteur',
      key: 'actor',
      width: 200,
      ellipsis: true,
      render: (_: unknown, r: AuditEntry) => (
        r.actor_email
          ? <Text>{r.actor_email}</Text>
          : <Text type="secondary" className="text-xs font-mono">{r.user_id}</Text>
      ),
    },
    {
      title: 'Action',
      dataIndex: 'action',
      width: 200,
      render: (a: string) => <Tag color="geekblue">{a}</Tag>,
    },
    {
      title: 'Statut',
      dataIndex: 'status',
      width: 90,
      render: (s: string | null) =>
        !s || s === 'success'
          ? <CheckCircleOutlined className="text-green-500" />
          : <Tooltip title={s}><CloseCircleOutlined className="text-red-500" /></Tooltip>,
    },
    {
      title: 'Cible',
      key: 'target',
      width: 200,
      ellipsis: true,
      render: (_: unknown, r: AuditEntry) => {
        const name = r.details?.target_display_name;
        if (name) return <Text className="text-xs">{name}</Text>;
        if (r.resource_type) return <Tag>{r.resource_type}</Tag>;
        return null;
      },
    },
    {
      title: 'Détails',
      key: 'details',
      width: 100,
      render: (_: unknown, r: AuditEntry) => (
        <Space size={4}>
          <DetailsCell details={r.details} />
          <DiffCell diff={r.diff} />
        </Space>
      ),
    },
    {
      title: 'IP',
      dataIndex: 'ip_address',
      width: 110,
      render: (ip: string | null) => ip ? <Text className="text-xs font-mono">{ip}</Text> : '—',
    },
  ];

  if (!scopeId) {
    return (
      <Card>
        <p className="text-gray-500">Sélectionnez une organisation ou un workspace.</p>
      </Card>
    );
  }

  return (
    <div>
      <div className="flex justify-between items-start mb-4">
        <h2 className="text-xl font-semibold">Audit Log</h2>
        <AuditFilterBar filters={filters} onChange={handleFiltersChange} />
      </div>
      <Card>
        <Table
          columns={columns}
          dataSource={logs}
          rowKey="id"
          loading={loading}
          pagination={{
            current: page,
            pageSize: 50,
            onChange: setPage,
            showTotal: (total) => `${total} entrées`,
          }}
          size="small"
        />
      </Card>
    </div>
  );
}
