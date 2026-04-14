import { useEffect, useState } from 'react';
import {
  Tabs, Table, Button, Modal, Input, App, Tag, Space, Typography, Tooltip,
} from 'antd';
import { UndoOutlined, DeleteOutlined, InboxOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import api from '@/lib/api';

const { Text } = Typography;

type EntityType = 'orgs' | 'workspaces' | 'users';

interface ArchivedOrg {
  id: string;
  name: string;
  slug: string;
  create_time: string;
}

interface ArchivedWorkspace {
  id: string;
  org_id: string;
  org_name: string | null;
  name: string;
  create_time: string;
}

interface ArchivedUser {
  id: string;
  email: string;
  nickname: string;
  is_superuser: boolean;
  create_time: string;
}

function formatDate(ts: string | number | null) {
  if (!ts) return '—';
  const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts);
  return d.toLocaleDateString('fr-FR', { day: '2-digit', month: 'short', year: 'numeric' });
}

interface PurgeState {
  entityType: EntityType;
  id: string;
  label: string;
}

export default function ArchivesPage() {
  const { message } = App.useApp();

  const [orgs, setOrgs] = useState<ArchivedOrg[]>([]);
  const [workspaces, setWorkspaces] = useState<ArchivedWorkspace[]>([]);
  const [users, setUsers] = useState<ArchivedUser[]>([]);
  const [loading, setLoading] = useState<Record<EntityType, boolean>>({
    orgs: false, workspaces: false, users: false,
  });

  const [purgeTarget, setPurgeTarget] = useState<PurgeState | null>(null);
  const [purgeConfirm, setPurgeConfirm] = useState('');
  const [purging, setPurging] = useState(false);
  const [restoring, setRestoring] = useState<string | null>(null);

  const fetchAll = () => {
    setLoading({ orgs: true, workspaces: true, users: true });
    api.get('/archives/orgs').then(r => setOrgs(r.data)).finally(() =>
      setLoading(p => ({ ...p, orgs: false })));
    api.get('/archives/workspaces').then(r => setWorkspaces(r.data)).finally(() =>
      setLoading(p => ({ ...p, workspaces: false })));
    api.get('/archives/users').then(r => setUsers(r.data)).finally(() =>
      setLoading(p => ({ ...p, users: false })));
  };

  useEffect(fetchAll, []);

  const handleRestore = async (type: EntityType, id: string) => {
    setRestoring(id);
    try {
      await api.post(`/archives/${type}/${id}/restore`);
      message.success('Entité restaurée avec succès');
      fetchAll();
    } catch (err: any) {
      message.error(err?.response?.data?.detail ?? 'Échec de la restauration');
    } finally {
      setRestoring(null);
    }
  };

  const handlePurge = async () => {
    if (!purgeTarget || purgeConfirm !== 'PURGER') return;
    setPurging(true);
    try {
      await api.delete(`/archives/${purgeTarget.entityType}/${purgeTarget.id}/purge?confirm=DELETE`);
      message.success('Supprimé définitivement');
      fetchAll();
    } catch (err: any) {
      message.error(err?.response?.data?.detail ?? 'Échec de la purge');
    } finally {
      setPurging(false);
      setPurgeTarget(null);
      setPurgeConfirm('');
    }
  };

  const actionCol = (type: EntityType, labelKey: string) => ({
    title: 'Actions',
    key: 'actions',
    width: 160,
    render: (_: unknown, record: any) => (
      <Space>
        <Tooltip title="Restaurer">
          <Button
            icon={<UndoOutlined />}
            size="small"
            loading={restoring === record.id}
            onClick={() => handleRestore(type, record.id)}
          >
            Restaurer
          </Button>
        </Tooltip>
        <Tooltip title="Supprimer définitivement">
          <Button
            danger
            icon={<DeleteOutlined />}
            size="small"
            onClick={() => {
              setPurgeTarget({ entityType: type, id: record.id, label: record[labelKey] });
              setPurgeConfirm('');
            }}
          />
        </Tooltip>
      </Space>
    ),
  });

  const orgColumns: ColumnsType<ArchivedOrg> = [
    { title: 'Nom', dataIndex: 'name' },
    { title: 'Slug', dataIndex: 'slug', render: (s: string) => <Tag>{s}</Tag> },
    { title: 'Archivé le', dataIndex: 'create_time', render: formatDate, width: 130 },
    actionCol('orgs', 'name'),
  ];

  const wsColumns: ColumnsType<ArchivedWorkspace> = [
    { title: 'Nom', dataIndex: 'name' },
    {
      title: 'Organisation',
      dataIndex: 'org_name',
      render: (n: string | null) => n ? <Tag>{n}</Tag> : <Text type="secondary">—</Text>,
    },
    { title: 'Archivé le', dataIndex: 'create_time', render: formatDate, width: 130 },
    actionCol('workspaces', 'name'),
  ];

  const userColumns: ColumnsType<ArchivedUser> = [
    { title: 'Email', dataIndex: 'email' },
    { title: 'Nom', dataIndex: 'nickname' },
    { title: 'Archivé le', dataIndex: 'create_time', render: formatDate, width: 130 },
    actionCol('users', 'email'),
  ];

  const tabs = [
    {
      key: 'orgs',
      label: `Organisations (${orgs.length})`,
      children: (
        <Table
          columns={orgColumns}
          dataSource={orgs}
          rowKey="id"
          loading={loading.orgs}
          pagination={false}
          locale={{ emptyText: 'Aucune organisation archivée' }}
        />
      ),
    },
    {
      key: 'workspaces',
      label: `Workspaces (${workspaces.length})`,
      children: (
        <Table
          columns={wsColumns}
          dataSource={workspaces}
          rowKey="id"
          loading={loading.workspaces}
          pagination={false}
          locale={{ emptyText: 'Aucun workspace archivé' }}
        />
      ),
    },
    {
      key: 'users',
      label: `Utilisateurs (${users.length})`,
      children: (
        <Table
          columns={userColumns}
          dataSource={users}
          rowKey="id"
          loading={loading.users}
          pagination={false}
          locale={{ emptyText: 'Aucun utilisateur archivé' }}
        />
      ),
    },
  ];

  return (
    <div>
      <div className="flex items-center gap-3 mb-6">
        <InboxOutlined className="text-2xl text-gray-400" />
        <div>
          <h2 className="text-xl font-semibold m-0">Centre des Archives</h2>
          <p className="text-sm text-gray-500 m-0">
            Entités archivées (soft-deleted) — restaurer ou supprimer définitivement.
          </p>
        </div>
      </div>

      <Tabs items={tabs} />

      <Modal
        open={!!purgeTarget}
        title={<span className="text-red-600">Suppression définitive</span>}
        onCancel={() => { setPurgeTarget(null); setPurgeConfirm(''); }}
        footer={[
          <Button key="cancel" onClick={() => { setPurgeTarget(null); setPurgeConfirm(''); }}>
            Annuler
          </Button>,
          <Button
            key="purge"
            danger
            type="primary"
            disabled={purgeConfirm !== 'PURGER'}
            loading={purging}
            onClick={handlePurge}
          >
            Supprimer définitivement
          </Button>,
        ]}
      >
        <p>
          Vous êtes sur le point de supprimer définitivement{' '}
          <strong>{purgeTarget?.label}</strong>.
          Cette action est <strong>irréversible</strong>.
        </p>
        {purgeTarget?.entityType === 'orgs' && (
          <p className="text-sm text-gray-500">
            Tous les workspaces, datasets et documents associés seront également supprimés.
          </p>
        )}
        {purgeTarget?.entityType === 'users' && (
          <p className="text-sm text-gray-500">
            Les données personnelles (email, nom) seront anonymisées (tombstone RGPD).
            La ligne est conservée pour l'intégrité référentielle.
          </p>
        )}
        <p className="mt-4 mb-1 text-sm font-medium">
          Tapez <strong>PURGER</strong> pour confirmer :
        </p>
        <Input
          value={purgeConfirm}
          onChange={(e) => setPurgeConfirm(e.target.value)}
          placeholder="PURGER"
          status={purgeConfirm && purgeConfirm !== 'PURGER' ? 'error' : undefined}
        />
      </Modal>
    </div>
  );
}
