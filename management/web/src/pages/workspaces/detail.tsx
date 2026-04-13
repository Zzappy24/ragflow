import { useEffect, useState } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { Card, Tabs, Spin, Statistic, Row, Col, Tag, Breadcrumb, Typography, Skeleton, Button, Popconfirm, App, Space } from 'antd';
import { TeamOutlined, DatabaseOutlined, KeyOutlined, AuditOutlined, GroupOutlined, HomeOutlined, DeleteOutlined, ExportOutlined, RobotOutlined } from '@ant-design/icons';
import api from '@/lib/api';
import MembersPage from '@/pages/members';
import GroupsPage from '@/pages/groups';
import ApiKeysPage from '@/pages/api-keys';
import AuditPage from '@/pages/audit';
import WorkspaceModelsPage from '@/pages/workspace-models';

const { Title, Text } = Typography;

interface WsDetail {
  id: string;
  org_id: string;
  tenant_id: string;
  name: string;
  description: string;
  status: string;
}

interface WsStats {
  members_count: number;
  datasets_count: number;
}

interface OrgInfo {
  id: string;
  name: string;
}

export default function WorkspaceDetailPage() {
  const { wsId } = useParams<{ wsId: string }>();
  const navigate = useNavigate();
  const { message } = App.useApp();
  const [ws, setWs] = useState<WsDetail | null>(null);
  const [stats, setStats] = useState<WsStats | null>(null);
  const [org, setOrg] = useState<OrgInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState(false);
  const [launching, setLaunching] = useState(false);

  const onLaunch = async () => {
    if (!wsId) return;
    setLaunching(true);
    try {
      const res = await api.post(`/workspaces/${wsId}/launch`);
      const url = res.data?.bridge_url;
      if (!url) {
        message.error('Launch failed: no bridge URL returned');
        return;
      }
      window.open(url, '_blank', 'noopener,noreferrer');
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg || 'Launch failed');
    } finally {
      setLaunching(false);
    }
  };

  const isEmpty = (stats?.members_count ?? 0) <= 1 && (stats?.datasets_count ?? 0) === 0;

  const onDelete = async () => {
    if (!ws) return;
    setDeleting(true);
    try {
      await api.delete(`/orgs/${ws.org_id}/workspaces/${wsId}`);
      message.success('Workspace deleted');
      navigate(`/organisations/${ws.org_id}`);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg || 'Failed to delete workspace');
    } finally {
      setDeleting(false);
    }
  };

  useEffect(() => {
    if (!wsId) return;
    setLoading(true);
    Promise.all([
      api.get(`/workspaces/${wsId}`),
      api.get(`/workspaces/${wsId}/stats`),
    ])
      .then(([wsRes, statsRes]) => {
        setWs(wsRes.data);
        setStats(statsRes.data);
        if (wsRes.data?.org_id) {
          api.get(`/orgs/${wsRes.data.org_id}`).then((r) => setOrg(r.data)).catch(() => null);
        }
      })
      .finally(() => setLoading(false));
  }, [wsId]);

  if (loading || !ws) return <Spin size="large" className="flex justify-center mt-20" />;

  return (
    <div>
      <Breadcrumb
        className="mb-3"
        items={[
          { title: <Link to="/"><HomeOutlined /></Link> },
          { title: <Link to="/organisations">Organisations</Link> },
          {
            title: org ? <Link to={`/organisations/${org.id}`}>{org.name}</Link> : <Skeleton.Input active size="small" style={{ width: 80 }} />,
          },
          { title: ws.name },
        ]}
      />

      <div className="flex items-start justify-between mb-6">
        <div>
          <div className="flex items-center gap-3">
            <Title level={3} className="!mb-0">{ws.name}</Title>
            <Tag color={ws.status === '1' ? 'green' : 'red'}>{ws.status === '1' ? 'Active' : 'Disabled'}</Tag>
          </div>
          {ws.description && <Text type="secondary">{ws.description}</Text>}
          <div className="mt-1">
            <Text type="secondary" className="text-xs">
              Tenant&nbsp;
              <Text code copyable={{ text: ws.tenant_id }} className="text-xs">{ws.tenant_id}</Text>
            </Text>
          </div>
        </div>
        <Space>
          {ws.status === '1' && (
            <Button type="primary" icon={<ExportOutlined />} loading={launching} onClick={onLaunch}>
              Open Workspace
            </Button>
          )}
          <Popconfirm
            title="Delete this workspace?"
            description={
              isEmpty
                ? 'This action cannot be undone.'
                : `This workspace has ${stats?.members_count ?? 0} member(s) and ${stats?.datasets_count ?? 0} dataset(s). Delete anyway?`
            }
            okText="Delete"
            okButtonProps={{ danger: true }}
            onConfirm={onDelete}
          >
            <Button danger icon={<DeleteOutlined />} loading={deleting}>
              Delete workspace
            </Button>
          </Popconfirm>
        </Space>
      </div>

      <Row gutter={16} className="mb-6">
        <Col span={6}>
          <Card>
            <Statistic title="Members" value={stats?.members_count || 0} prefix={<TeamOutlined />} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="Datasets" value={stats?.datasets_count || 0} prefix={<DatabaseOutlined />} />
          </Card>
        </Col>
      </Row>

      <Tabs
        type="line"
        size="large"
        items={[
          {
            key: 'members',
            label: <span><TeamOutlined /> Members</span>,
            children: <MembersPage wsId={wsId} />,
          },
          {
            key: 'groups',
            label: <span><GroupOutlined /> Groups</span>,
            children: <GroupsPage wsId={wsId} />,
          },
          {
            key: 'api-keys',
            label: <span><KeyOutlined /> API Keys</span>,
            children: <ApiKeysPage wsId={wsId} />,
          },
          {
            key: 'models',
            label: <span><RobotOutlined /> Models</span>,
            children: <WorkspaceModelsPage wsId={wsId} />,
          },
          {
            key: 'audit',
            label: <span><AuditOutlined /> Audit</span>,
            children: <AuditPage wsId={wsId} />,
          },
        ]}
      />
    </div>
  );
}

