import { useEffect, useState } from 'react';
import {
  Tabs, Table, Button, Modal, Input, App, Tag, Space, Typography, Tooltip,
} from 'antd';
import { UndoOutlined, FireOutlined, InboxOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import api from '@/lib/api';

const { Text } = Typography;

type EntityType = 'orgs' | 'workspaces' | 'users';

interface ArchivedOrg {
  id: string;
  name: string;
  slug: string;
  archived_ts: string | null;
  archived_workspaces: number;
  archived_users: number;
  create_time: string;
}

interface ArchivedWorkspace {
  id: string;
  org_id: string;
  org_name: string | null;
  name: string;
  create_time: string;
  org_snapshot_id: string | null;
  org_snapshot_name: string | null;
}

interface ArchivedUser {
  id: string;
  email: string;
  nickname: string;
  is_superuser: boolean;
  create_time: string;
  org_snapshot_id: string | null;
  org_snapshot_name: string | null;
}

function formatDate(ts: string | number | null) {
  if (!ts) return '—';
  // RAGFlow stores create_time in milliseconds — use directly
  const d = new Date(typeof ts === 'number' && ts < 1e11 ? ts * 1000 : ts);
  return d.toLocaleString('fr-FR', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
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
      const res = await api.post(`/archives/${type}/${id}/restore`);
      if (type === 'orgs' && res.data) {
        const { restored_workspaces: ws, restored_users: us } = res.data;
        message.success(
          `Organisation restaurée — ${ws} workspace${ws !== 1 ? 's' : ''} et ${us} utilisateur${us !== 1 ? 's' : ''} restaurés`
        );
      } else {
        message.success('Entité restaurée avec succès');
      }
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
    width: 220,
    render: (_: unknown, record: any) => {
      const isPartOfSnapshot = !!record.org_snapshot_id;
      const snapshotTip = isPartOfSnapshot
        ? `Fait partie du snapshot de l'org "${record.org_snapshot_name}". Restaurez l'organisation pour restaurer cet élément.`
        : undefined;

      return isPartOfSnapshot ? (
        <Tooltip title={snapshotTip}>
          <Tag color="orange" className="cursor-default">
            Snapshot · {record.org_snapshot_name}
          </Tag>
        </Tooltip>
      ) : (
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
              type="primary"
              icon={<FireOutlined />}
              size="small"
              onClick={() => {
                setPurgeTarget({ entityType: type, id: record.id, label: record[labelKey] });
                setPurgeConfirm('');
              }}
            />
          </Tooltip>
        </Space>
      );
    },
  });

  const orgColumns: ColumnsType<ArchivedOrg> = [
    { title: 'Nom', dataIndex: 'name' },
    { title: 'Slug', dataIndex: 'slug', render: (s: string) => <Tag>{s}</Tag> },
    {
      title: 'Contenu archivé',
      key: 'archived_content',
      render: (_: unknown, r: ArchivedOrg) => (
        <Text type="secondary" className="text-xs">
          {r.archived_workspaces} workspace{r.archived_workspaces !== 1 ? 's' : ''}
          {r.archived_users > 0 ? ` · ${r.archived_users} user${r.archived_users !== 1 ? 's' : ''}` : ''}
        </Text>
      ),
    },
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
            icon={<FireOutlined />}
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
