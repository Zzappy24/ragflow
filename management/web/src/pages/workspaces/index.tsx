import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { Table, Button, Card, Modal, Form, Input, App, Tag, Space, Switch, Popconfirm, Tooltip } from 'antd';
import { PlusOutlined, UndoOutlined, FireOutlined, ExportOutlined, StarFilled, StarOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import api from '@/lib/api';
import { useBulkDelete } from '@/components/BulkActions';
import { useAuthStore } from '@/stores/auth';

interface Workspace {
  id: string;
  org_id: string;
  tenant_id: string;
  name: string;
  description: string;
  status: string;
  bu: string;
  is_model_template: boolean;
}

export default function WorkspacesPage({
  orgId: orgIdProp,
  onChange,
}: { orgId?: string; onChange?: () => void } = {}) {
  const params = useParams<{ orgId: string }>();
  const orgId = orgIdProp || params.orgId;
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm();
  const { message } = App.useApp();
  const [showDeleted, setShowDeleted] = useState(false);
  const isSuperuser = useAuthStore((s) => s.user?.is_superuser ?? false);

  const actualOrgId = orgId || '';

  const refresh = () => {
    fetchWorkspaces();
    onChange?.();
  };

  const fetchWorkspaces = () => {
    if (!actualOrgId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    api
      .get(`/orgs/${actualOrgId}/workspaces`, { params: { include_deleted: showDeleted } })
      .then((res) => setWorkspaces(res.data))
      .finally(() => setLoading(false));
  };

  const { rowSelection, BulkDeleteButton } = useBulkDelete<string>({
    entityName: 'workspace',
    deleteOne: (id) => api.delete(`/orgs/${actualOrgId}/workspaces/${id}`),
    onDone: refresh,
    variant: 'archive',
  });

  useEffect(fetchWorkspaces, [actualOrgId, showDeleted]);

  const onRestore = async (id: string) => {
    try {
      await api.post(`/orgs/${actualOrgId}/workspaces/${id}/restore`);
      message.success('Workspace restored');
      refresh();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg || 'Restore failed');
    }
  };

  const onPurge = async (id: string) => {
    try {
      await api.delete(`/orgs/${actualOrgId}/workspaces/${id}/purge`, {
        params: { confirm: 'DELETE' },
      });
      message.success('Workspace permanently purged');
      refresh();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg || 'Purge failed');
    }
  };

  const onLaunch = async (id: string) => {
    // Open the tab SYNCHRONOUSLY in the click handler so the browser keeps
    // the user-gesture context and doesn't block it as a popup. We'll
    // navigate it to the bridge URL once the backend issues the token.
    const tab = window.open('about:blank', '_blank', 'noopener,noreferrer');
    try {
      const res = await api.post(`/workspaces/${id}/launch`);
      const url = res.data?.bridge_url;
      if (!url) {
        if (tab) tab.close();
        message.error('Launch failed: no bridge URL returned');
        return;
      }
      if (tab) {
        tab.location.href = url;
      } else {
        // Popup was blocked despite the sync open — fall back to same-tab nav.
        window.location.href = url;
      }
    } catch (err: unknown) {
      if (tab) tab.close();
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg || 'Launch failed');
    }
  };

  const onCreate = async () => {
    if (creating) return;
    let values: { name: string; description?: string };
    try {
      values = await form.validateFields();
    } catch {
      return; // validation error — antd already shows it
    }
    setCreating(true);
    try {
      await api.post(`/orgs/${actualOrgId}/workspaces`, values);
      message.success('Workspace created');
      setModalOpen(false);
      form.resetFields();
      refresh();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg || 'Failed to create workspace');
    } finally {
      setCreating(false);
    }
  };

  // Tag BU (groupement libre, ex. « Digital ») — stocké dans settings_json,
  // modifiable par org admin. "" = retirer le tag.
  const [buModalWs, setBuModalWs] = useState<Workspace | null>(null);
  const [buValue, setBuValue] = useState('');
  const onSaveBu = async () => {
    if (!buModalWs) return;
    try {
      await api.put(`/orgs/${buModalWs.org_id}/workspaces/${buModalWs.id}`, { bu: buValue });
      message.success(buValue ? `Workspace taggé « ${buValue} »` : 'Tag retiré');
      setBuModalWs(null);
      fetchWorkspaces();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg ?? 'Échec de la mise à jour du tag');
    }
  };

  const onToggleTemplate = async (record: Workspace) => {
    try {
      if (record.is_model_template) {
        await api.delete(`/workspaces/${record.id}/model-template`);
        message.success('Ce workspace n\'est plus la référence de modèles');
      } else {
        await api.post(`/workspaces/${record.id}/model-template`);
        message.success('Référence de modèles définie — les nouveaux workspaces en hériteront');
      }
      refresh();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg ?? 'Échec de la mise à jour');
    }
  };

  const columns: ColumnsType<Workspace> = [
    {
      title: 'Nom',
      dataIndex: 'name',
      render: (name: string, record: Workspace) => (
        <Space size="small">
          {record.status === '1' ? (
            <Link to={`/workspaces/${record.id}`}>{name}</Link>
          ) : (
            <span className="text-gray-400 line-through">{name}</span>
          )}
          {record.is_model_template && (
            <Tooltip title="Les nouveaux workspaces héritent des modèles de ce workspace">
              <Tag color="gold" icon={<StarFilled />}>Modèles par défaut</Tag>
            </Tooltip>
          )}
        </Space>
      ),
    },
    { title: 'Description', dataIndex: 'description', ellipsis: true },
    {
      title: 'BU',
      dataIndex: 'bu',
      width: 140,
      render: (bu: string, record: Workspace) => (
        <Tooltip title="Groupement libre (ex. BU) — cliquer pour modifier">
          <Tag color={bu ? 'geekblue' : undefined} style={{ cursor: 'pointer' }}
               onClick={() => { setBuModalWs(record); setBuValue(bu || ''); }}>
            {bu || '—'}
          </Tag>
        </Tooltip>
      ),
    },
    {
      title: 'Statut',
      dataIndex: 'status',
      width: 100,
      render: (s: string) => <Tag color={s === '1' ? 'green' : 'red'}>{s === '1' ? 'Actif' : 'Archivé'}</Tag>,
    },
    {
      title: '',
      width: 140,
      render: (_: unknown, record: Workspace) =>
        record.status === '1' ? (
          <Space size="small">
            {isSuperuser && (
              <Tooltip title={record.is_model_template
                ? 'Retirer le statut de référence de modèles'
                : 'Définir comme modèles par défaut (les nouveaux workspaces en hériteront)'}>
                <Button type="text" size="small"
                  icon={record.is_model_template ? <StarFilled style={{ color: '#d4a017' }} /> : <StarOutlined />}
                  onClick={() => onToggleTemplate(record)} />
              </Tooltip>
            )}
            <Tooltip title="Ouvrir le workspace dans RAGFlow">
              <Button type="text" icon={<ExportOutlined />} size="small"
                onClick={() => onLaunch(record.id)} />
            </Tooltip>
          </Space>
        ) : (
          <Space size="small">
            <Tooltip title="Restaurer">
              <Button
                type="text"
                icon={<UndoOutlined />}
                size="small"
                onClick={() => onRestore(record.id)}
              />
            </Tooltip>
            {isSuperuser && (
              <Tooltip title="Purger définitivement (irréversible)">
                <Popconfirm
                  title="Purger ce workspace définitivement ?"
                  description="Tous les datasets, documents et chunks seront SUPPRIMÉS. Irréversible."
                  okText="Purger"
                  okButtonProps={{ danger: true }}
                  onConfirm={() => onPurge(record.id)}
                >
                  <Button type="text" danger icon={<FireOutlined />} size="small" />
                </Popconfirm>
              </Tooltip>
            )}
          </Space>
        ),
    },
  ];

  if (!actualOrgId) {
    return (
      <Card>
        <p className="text-gray-500">
          Select an organisation to manage its workspaces.
          Go to <Link to="/organisations">Organisations</Link> and click on one.
        </p>
      </Card>
    );
  }

  return (
    <div>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-semibold">Workspaces</h2>
        <Space>
          <span className="text-sm text-gray-500">Show deleted</span>
          <Switch size="small" checked={showDeleted} onChange={setShowDeleted} />
          <BulkDeleteButton />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
            New Workspace
          </Button>
        </Space>
      </div>

      <Card>
        <Table
          scroll={{ x: 'max-content' }}
          columns={columns}
          dataSource={workspaces}
          rowKey="id"
          loading={loading}
          pagination={false}
          rowSelection={{
            ...rowSelection,
            getCheckboxProps: (record: Workspace) => ({
              disabled: record.status === '0',
            }),
          }}
        />
      </Card>

      <Modal title={`Tag BU — ${buModalWs?.name ?? ''}`} open={!!buModalWs}
             onOk={onSaveBu} okText="Enregistrer"
             onCancel={() => setBuModalWs(null)}>
        <Form layout="vertical">
          <Form.Item label="BU / groupe"
                     extra="Champ libre pour regrouper les workspaces (listes, exports). Vider = retirer le tag.">
            <Input value={buValue} onChange={(e) => setBuValue(e.target.value)}
                   placeholder="ex. Digital" maxLength={64} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="New Workspace"
        open={modalOpen}
        onOk={onCreate}
        onCancel={() => !creating && setModalOpen(false)}
        okText="Create"
        confirmLoading={creating}
        maskClosable={!creating}
        closable={!creating}
      >
        <Form form={form} layout="vertical" className="mt-4">
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="Description">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
