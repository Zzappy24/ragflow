import { useEffect, useState } from 'react';
import { useSearchParams, useParams } from 'react-router-dom';
import { Table, Button, Card, Modal, Form, Input, Select, App, Popconfirm, Space, Typography, Tooltip } from 'antd';
import { PlusOutlined, UserAddOutlined, CopyOutlined, UserDeleteOutlined, FireOutlined, MinusCircleOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import api from '@/lib/api';
import { useBulkDelete } from '@/components/BulkActions';
import { useAuthStore } from '@/stores/auth';

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

  // Invite-user (org-scope only): creates a brand-new user atomically through
  // /api/admin/users and returns a single-use invite URL the admin can copy.
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteForm] = Form.useForm();
  const [inviteBusy, setInviteBusy] = useState(false);
  const [inviteResult, setInviteResult] = useState<{ email: string; invite_url: string } | null>(null);

  const isSuperuser = useAuthStore((s) => s.user?.is_superuser ?? false);

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

  const onDeleteUser = async (uid: string) => {
    try {
      await api.delete(`/users/${uid}`);
      message.success('User deleted (tombstone)');
      refresh();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg || 'Failed to delete user');
    }
  };

  const onRoleChange = async (uid: string, role: string) => {
    const url = scope === 'ws' ? `/workspaces/${scopeId}/members/${uid}` : `/orgs/${scopeId}/members/${uid}`;
    await api.put(url, { role });
    message.success('Role updated');
    fetchMembers();
  };

  const onInvite = async () => {
    if (inviteBusy) return;
    let values: { email: string; nickname: string; org_role: string };
    try {
      values = await inviteForm.validateFields();
    } catch {
      return;
    }
    setInviteBusy(true);
    try {
      const res = await api.post('/users', {
        email: values.email,
        nickname: values.nickname,
        org_id: scopeId,
        org_role: values.org_role,
      });
      setInviteResult({ email: res.data.email, invite_url: res.data.invite_url });
      setInviteOpen(false);
      inviteForm.resetFields();
      refresh();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg || 'Failed to invite user');
    } finally {
      setInviteBusy(false);
    }
  };

  const copyInvite = async () => {
    if (!inviteResult) return;
    try {
      await navigator.clipboard.writeText(inviteResult.invite_url);
      message.success('Invite URL copied');
    } catch {
      message.error('Clipboard unavailable — copy manually');
    }
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
      title: 'Membership',
      width: 80,
      align: 'center' as const,
      render: (_: unknown, record: Member) => (
        <Tooltip title="Retirer l'accès (récupérable)">
          <Popconfirm title="Retirer ce membre ?" onConfirm={() => onRemove(record.user_id)}>
            <Button type="text" icon={<MinusCircleOutlined />} size="small">Retirer</Button>
          </Popconfirm>
        </Tooltip>
      ),
    },
    ...(isSuperuser ? [{
      title: 'Account',
      width: 80,
      align: 'center' as const,
      render: (_: unknown, record: Member) => (
        <Tooltip title="RGPD : efface les données personnelles définitivement">
          <Popconfirm
            title="Supprimer définitivement ce compte ?"
            description="Toutes les données personnelles seront effacées. Irréversible."
            onConfirm={() => onDeleteUser(record.user_id)}
            okText="Supprimer définitivement"
            okButtonProps={{ danger: true, type: 'primary' }}
          >
            <Button type="text" danger icon={<FireOutlined />} size="small">Supprimer</Button>
          </Popconfirm>
        </Tooltip>
      ),
    }] as ColumnsType<Member> : []),
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
          {scope === 'ws' && (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
              Add Member
            </Button>
          )}
          {scope === 'org' && (
            <Button type="primary" icon={<UserAddOutlined />} onClick={() => setInviteOpen(true)}>
              Invite User
            </Button>
          )}
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

      <Modal
        title="Invite User"
        open={inviteOpen}
        onOk={onInvite}
        onCancel={() => !inviteBusy && setInviteOpen(false)}
        okText="Send Invite"
        confirmLoading={inviteBusy}
        maskClosable={!inviteBusy}
        closable={!inviteBusy}
      >
        <p className="text-gray-500 mb-3">
          Creates a new user and returns a single-use invite link the recipient can
          use to set their password. The user is inactive until they consume the link.
        </p>
        <Form form={inviteForm} layout="vertical">
          <Form.Item name="email" label="Email" rules={[{ required: true, type: 'email' }]}>
            <Input placeholder="user@example.com" />
          </Form.Item>
          <Form.Item name="nickname" label="Name" rules={[{ required: true }]}>
            <Input placeholder="Jane Doe" />
          </Form.Item>
          <Form.Item name="org_role" label="Org Role" initialValue="member" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'org_admin', label: 'Org Admin' },
                { value: 'member', label: 'Member' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="Invite Link Generated"
        open={!!inviteResult}
        onCancel={() => setInviteResult(null)}
        footer={[
          <Button key="copy" type="primary" icon={<CopyOutlined />} onClick={copyInvite}>
            Copy Link
          </Button>,
          <Button key="close" onClick={() => setInviteResult(null)}>
            Done
          </Button>,
        ]}
      >
        <p className="text-gray-500 mb-2">
          Send this single-use link to <b>{inviteResult?.email}</b>. They will set their
          password and be logged into RAGFlow automatically.
        </p>
        <Typography.Paragraph
          code
          copyable={{ text: inviteResult?.invite_url }}
          style={{ wordBreak: 'break-all', marginTop: 12 }}
        >
          {inviteResult?.invite_url}
        </Typography.Paragraph>
        <p className="text-xs text-gray-400">
          The link expires in 48 hours and can only be used once.
        </p>
      </Modal>
    </div>
  );
}
