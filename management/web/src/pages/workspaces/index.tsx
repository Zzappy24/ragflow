import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { Table, Button, Card, Modal, Form, Input, App, Tag, Space, Switch, Popconfirm, Tooltip } from 'antd';
import { PlusOutlined, UndoOutlined, FireOutlined, ExportOutlined } from '@ant-design/icons';
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

  const columns: ColumnsType<Workspace> = [
    {
      title: 'Name',
      dataIndex: 'name',
      render: (name: string, record: Workspace) =>
        record.status === '1' ? (
          <Link to={`/workspaces/${record.id}`}>{name}</Link>
        ) : (
          <span className="text-gray-400 line-through">{name}</span>
        ),
    },
    { title: 'Description', dataIndex: 'description', ellipsis: true },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 100,
      render: (s: string) => <Tag color={s === '1' ? 'green' : 'red'}>{s === '1' ? 'Active' : 'Deleted'}</Tag>,
    },
    { title: 'Tenant ID', dataIndex: 'tenant_id', ellipsis: true, width: 200 },
    {
      title: '',
      width: 110,
      render: (_: unknown, record: Workspace) =>
        record.status === '1' ? (
          <Tooltip title="Open workspace in RAGFlow">
            <Button
              type="text"
              icon={<ExportOutlined />}
              size="small"
              onClick={() => onLaunch(record.id)}
            />
          </Tooltip>
        ) : (
          <Space size="small">
            <Tooltip title="Restore">
              <Button
                type="text"
                icon={<UndoOutlined />}
                size="small"
                onClick={() => onRestore(record.id)}
              />
            </Tooltip>
            {isSuperuser && (
              <Tooltip title="Purge permanently (irreversible)">
                <Popconfirm
                  title="Purge this workspace permanently?"
                  description="All datasets, documents, and chunks will be DELETED. This cannot be undone."
                  okText="Purge"
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
