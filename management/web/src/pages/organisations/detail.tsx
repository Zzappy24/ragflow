import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { Card, Tabs, Spin, Progress, Row, Col, Statistic, Breadcrumb, Typography, Tag } from 'antd';
import { AppstoreOutlined, TeamOutlined, AuditOutlined, HomeOutlined, DatabaseOutlined, FileOutlined } from '@ant-design/icons';
import api from '@/lib/api';
import WorkspacesPage from '@/pages/workspaces';
import MembersPage from '@/pages/members';
import AuditPage from '@/pages/audit';

const { Title, Text } = Typography;

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

export default function OrgDetailPage() {
  const { orgId } = useParams<{ orgId: string }>();
  const [org, setOrg] = useState<OrgDetail | null>(null);
  const [stats, setStats] = useState<OrgStats | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshStats = () => {
    if (!orgId) return;
    api.get(`/orgs/${orgId}/stats`).then((r) => setStats(r.data));
  };

  useEffect(() => {
    if (!orgId) return;
    setLoading(true);
    Promise.all([
      api.get(`/orgs/${orgId}`),
      api.get(`/orgs/${orgId}/stats`),
    ])
      .then(([orgRes, statsRes]) => {
        setOrg(orgRes.data);
        setStats(statsRes.data);
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
        <Col span={6}>
          <Card>
            <Statistic
              title="Users"
              value={usersQ?.current ?? 0}
              suffix={`/ ${usersQ?.max ?? '∞'}`}
              prefix={<TeamOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Workspaces"
              value={wsQ?.current ?? 0}
              suffix={`/ ${wsQ?.max ?? '∞'}`}
              prefix={<AppstoreOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Datasets"
              value={datasetsQ?.current ?? 0}
              suffix={`/ ${datasetsQ?.max ?? '∞'}`}
              prefix={<DatabaseOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
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
              <Card>
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
            ),
          },
          {
            key: 'audit',
            label: <span><AuditOutlined /> Audit</span>,
            children: <AuditPage orgId={orgId} />,
          },
        ]}
      />
    </div>
  );
}
