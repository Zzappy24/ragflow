import { useEffect, useState } from 'react';
import { useSearchParams, useParams } from 'react-router-dom';
import { Table, Card, Input, Space } from 'antd';
import dayjs from 'dayjs';
import api from '@/lib/api';

interface AuditEntry {
  id: string;
  user_id: string;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  ip_address: string | null;
  create_time: string;
}

export default function AuditPage({ orgId: orgIdProp, wsId: wsIdProp }: { orgId?: string; wsId?: string } = {}) {
  const [searchParams] = useSearchParams();
  const params = useParams<{ orgId?: string; wsId?: string }>();
  const orgId = orgIdProp || params.orgId || searchParams.get('org');
  const wsId = wsIdProp || params.wsId || searchParams.get('ws');
  const [logs, setLogs] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [actionFilter, setActionFilter] = useState<string | undefined>();

  const scope = wsId ? 'ws' : 'org';
  const scopeId = wsId || orgId || '';

  const fetchLogs = () => {
    if (!scopeId) { setLoading(false); return; }
    setLoading(true);
    const url = scope === 'ws' ? `/workspaces/${scopeId}/audit` : `/orgs/${scopeId}/audit`;
    api
      .get(url, { params: { page, page_size: 50, action: actionFilter } })
      .then((res) => setLogs(res.data.items || []))
      .finally(() => setLoading(false));
  };

  useEffect(fetchLogs, [scopeId, page, actionFilter]);

  const columns = [
    {
      title: 'Time',
      dataIndex: 'create_time',
      width: 180,
      render: (t: string) => t ? dayjs(t).format('YYYY-MM-DD HH:mm:ss') : '',
    },
    { title: 'User', dataIndex: 'user_id', ellipsis: true, width: 200 },
    { title: 'Action', dataIndex: 'action', width: 150 },
    { title: 'Resource', dataIndex: 'resource_type', width: 120 },
    { title: 'Resource ID', dataIndex: 'resource_id', ellipsis: true },
    { title: 'IP', dataIndex: 'ip_address', width: 130 },
  ];

  if (!scopeId) {
    return (
      <Card>
        <p className="text-gray-500">
          Select an organisation or workspace: <code>?org=ORG_ID</code> or <code>?ws=WS_ID</code>
        </p>
      </Card>
    );
  }

  return (
    <div>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-semibold">Audit Log</h2>
        <Space>
          <Input
            placeholder="Filter by action..."
            allowClear
            onChange={(e) => setActionFilter(e.target.value || undefined)}
            style={{ width: 200 }}
          />
        </Space>
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
          }}
          size="small"
        />
      </Card>
    </div>
  );
}
