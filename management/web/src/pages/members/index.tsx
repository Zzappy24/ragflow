import { useEffect, useState } from 'react';
import { useSearchParams, useParams } from 'react-router-dom';
import { Table, Button, Card, Modal, Form, Input, Select, App, Popconfirm, Space } from 'antd';
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import api from '@/lib/api';
import { useBulkDelete } from '@/components/BulkActions';

interface Member {
  id: string;
  user_id: string;
  email: string | null;
  nickname: string | null;
  role: string;
}

export default function MembersPage({
  orgId: orgIdProp,
  wsId: wsIdProp,
  onChange,
}: { orgId?: string; wsId?: string; onChange?: () => void } = {}) {
  const [searchParams] = useSearchParams();
  const params = useParams<{ orgId?: string; wsId?: string }>();
  const orgId = orgIdProp || params.orgId || searchParams.get('org');
  const wsId = wsIdProp || params.wsId || searchParams.get('ws');
  const [members, setMembers] = useState<Member[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm();
  const { message } = App.useApp();

  const scope = wsId ? 'ws' : 'org';
  const scopeId = wsId || orgId || '';
  const memberUrl = (uid: string) =>
    scope === 'ws' ? `/workspaces/${scopeId}/members/${uid}` : `/orgs/${scopeId}/members/${uid}`;

  const refresh = () => {
    fetchMembers();
    onChange?.();
  };

  const { rowSelection, BulkDeleteButton } = useBulkDelete<string>({
    entityName: 'member',
    deleteOne: (uid) => api.delete(memberUrl(uid)),
    onDone: refresh,
  });

  const fetchMembers = () => {
    if (!scopeId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    const url = scope === 'ws' ? `/workspaces/${scopeId}/members` : `/orgs/${scopeId}/members`;
    api.get(url).then((res) => setMembers(res.data)).finally(() => setLoading(false));
  };

  useEffect(fetchMembers, [scopeId, scope]);

  const onAdd = async () => {
    try {
      const values = await form.validateFields();
      const url = scope === 'ws' ? `/workspaces/${scopeId}/members` : `/orgs/${scopeId}/members`;
      await api.post(url, values);
      message.success('Member added');
      setModalOpen(false);
      form.resetFields();
      refresh();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      if (msg) message.error(msg);
    }
  };

  const onRemove = async (uid: string) => {
    const url = scope === 'ws' ? `/workspaces/${scopeId}/members/${uid}` : `/orgs/${scopeId}/members/${uid}`;
    await api.delete(url);
    message.success('Member removed');
    refresh();
  };

  const onRoleChange = async (uid: string, role: string) => {
    const url = scope === 'ws' ? `/workspaces/${scopeId}/members/${uid}` : `/orgs/${scopeId}/members/${uid}`;
    await api.put(url, { role });
    message.success('Role updated');
    fetchMembers();
  };

  const roleOptions = scope === 'ws'
    ? [{ value: 'ws_admin', label: 'WS Admin' }, { value: 'editor', label: 'Editor' }, { value: 'viewer', label: 'Viewer' }]
    : [{ value: 'org_admin', label: 'Org Admin' }, { value: 'member', label: 'Member' }];

  const columns: ColumnsType<Member> = [
    { title: 'Email', dataIndex: 'email' },
    { title: 'Nickname', dataIndex: 'nickname' },
    {
      title: 'Role',
      dataIndex: 'role',
      render: (role: string, record: Member) => (
        <Select
          value={role}
          size="small"
          style={{ width: 120 }}
          options={roleOptions}
          onChange={(val) => onRoleChange(record.user_id, val)}
        />
      ),
    },
    {
      title: '',
      width: 50,
      render: (_: unknown, record: Member) => (
        <Popconfirm title="Remove this member?" onConfirm={() => onRemove(record.user_id)}>
          <Button type="text" danger icon={<DeleteOutlined />} size="small" />
        </Popconfirm>
      ),
    },
  ];

  if (!scopeId) {
    return (
      <Card>
        <p className="text-gray-500">
          Select an organisation or workspace to manage members.
          Use the URL parameters: <code>?org=ORG_ID</code> or <code>?ws=WS_ID</code>
        </p>
      </Card>
    );
  }

  return (
    <div>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-semibold">
          Members {scope === 'ws' ? '(Workspace)' : '(Organisation)'}
        </h2>
        <Space>
          <BulkDeleteButton />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
            Add Member
          </Button>
        </Space>
      </div>

      <Card>
        <Table
          columns={columns}
          dataSource={members}
          rowKey="user_id"
          loading={loading}
          pagination={false}
          rowSelection={rowSelection}
        />
      </Card>

      <Modal title="Add Member" open={modalOpen} onOk={onAdd} onCancel={() => setModalOpen(false)} okText="Add">
        <Form form={form} layout="vertical" className="mt-4">
          <Form.Item name="email" label="Email" rules={[{ required: true, type: 'email' }]}>
            <Input placeholder="user@example.com" />
          </Form.Item>
          <Form.Item name="role" label="Role" initialValue={roleOptions[roleOptions.length - 1].value}>
            <Select options={roleOptions} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
