import { useEffect, useState } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { Card, Tabs, Spin, Progress, Row, Col, Statistic, Breadcrumb, Typography, Tag, Button, Modal, Input, App } from 'antd';
import { AppstoreOutlined, TeamOutlined, AuditOutlined, HomeOutlined, DatabaseOutlined, FileOutlined, DeleteOutlined } from '@ant-design/icons';
import api from '@/lib/api';
import { useAuthStore } from '@/stores/auth';
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
  const navigate = useNavigate();
  const { message, modal } = App.useApp();
  const { user } = useAuthStore();
  const [org, setOrg] = useState<OrgDetail | null>(null);
  const [stats, setStats] = useState<OrgStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [archiveModalOpen, setArchiveModalOpen] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [purgeModalOpen, setPurgeModalOpen] = useState(false);
  const [purgeConfirmText, setPurgeConfirmText] = useState('');
  const [purging, setPurging] = useState(false);

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
              icon={<DeleteOutlined />}
              onClick={() => setArchiveModalOpen(true)}
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
              icon={<DeleteOutlined />}
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
