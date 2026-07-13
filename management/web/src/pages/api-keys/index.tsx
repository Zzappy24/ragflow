import { useEffect, useState } from 'react';
import { useSearchParams, useParams } from 'react-router-dom';
import { Table, Button, Card, Modal, Form, Input, InputNumber, App, Tag, Popconfirm, Typography, Space } from 'antd';
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import api from '@/lib/api';
import { useBulkDelete } from '@/components/BulkActions';

const { Text } = Typography;

interface ApiKey {
  id: string;
  workspace_id: string;
  name: string;
  token: string | null;
  token_hint: string | null;
  expires_at: string | null;
  last_used_at: string | null;
  status: string;
  create_time: string;
}

export default function ApiKeysPage({ wsId: wsIdProp }: { wsId?: string } = {}) {
  const [searchParams] = useSearchParams();
  const params = useParams<{ wsId?: string }>();
  const wsId = wsIdProp || params.wsId || searchParams.get('ws') || '';
  const { message } = App.useApp();
  const [keys, setKeys] = useState<ApiKey[]>([]);

  const { rowSelection, BulkDeleteButton } = useBulkDelete<string>({
    entityName: 'API key',
    deleteOne: (id) => api.delete(`/workspaces/${wsId}/api-keys/${id}`),
    onDone: () => fetchKeys(),
  });

  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [newToken, setNewToken] = useState<string | null>(null);
  const [form] = Form.useForm();

  const fetchKeys = () => {
    if (!wsId) { setLoading(false); return; }
    setLoading(true);
    api.get(`/workspaces/${wsId}/api-keys`).then((res) => setKeys(res.data)).finally(() => setLoading(false));
  };

  useEffect(fetchKeys, [wsId]);

  const onCreate = async () => {
    try {
      const values = await form.validateFields();
      const res = await api.post(`/workspaces/${wsId}/api-keys`, values);
      setNewToken(res.data.token);
      message.success('API key created');
      setModalOpen(false);
      form.resetFields();
      fetchKeys();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      if (msg) message.error(msg);
    }
  };

  const onDelete = async (keyId: string) => {
    await api.delete(`/workspaces/${wsId}/api-keys/${keyId}`);
    message.success('API key revoked');
    fetchKeys();
  };

  if (!wsId) {
    return (
      <Card>
        <p className="text-gray-500">Select a workspace: <code>?ws=WS_ID</code></p>
      </Card>
    );
  }

  const columns = [
    { title: 'Name', dataIndex: 'name' },
    {
      title: 'Key',
      dataIndex: 'token_hint',
      render: (h: string | null) => h ? <Text code className="text-xs">{h}</Text> : <span className="text-gray-400">—</span>,
    },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 80,
      render: (s: string) => <Tag color={s === '1' ? 'green' : 'red'}>{s === '1' ? 'Active' : 'Revoked'}</Tag>,
    },
    {
      title: 'Expires',
      dataIndex: 'expires_at',
      render: (d: string | null) => d ? dayjs(d).format('YYYY-MM-DD') : 'Never',
    },
    {
      title: 'Last Used',
      dataIndex: 'last_used_at',
      render: (d: string | null) => d ? dayjs(d).format('YYYY-MM-DD HH:mm') : 'Never',
    },
    {
      title: '',
      width: 50,
      render: (_: unknown, record: ApiKey) => (
        <Popconfirm title="Revoke this key?" onConfirm={() => onDelete(record.id)}>
          <Button type="text" danger icon={<DeleteOutlined />} size="small" />
        </Popconfirm>
      ),
    },
  ];

  return (
    <div>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-semibold">API Keys</h2>
        <Space>
          <BulkDeleteButton />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
            New API Key
          </Button>
        </Space>
      </div>

      {newToken && (
        <Card className="mb-4 border-green-300 bg-green-50">
          <p className="font-medium mb-2">New API key created. Copy it now — it won't be shown again:</p>
          <div className="flex items-center gap-2">
            <Text code copyable className="text-sm">{newToken}</Text>
          </div>
          <Button size="small" className="mt-2" onClick={() => setNewToken(null)}>Dismiss</Button>
        </Card>
      )}

      <Card>
        <Table
          scroll={{ x: 'max-content' }}
          columns={columns}
          dataSource={keys}
          rowKey="id"
          loading={loading}
          pagination={false}
          rowSelection={rowSelection}
        />
      </Card>

      <Modal title="New API Key" open={modalOpen} onOk={onCreate} onCancel={() => setModalOpen(false)} okText="Create">
        <Form form={form} layout="vertical" className="mt-4">
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input placeholder="e.g., CI/CD Pipeline" />
          </Form.Item>
          <Form.Item name="expires_days" label="Expires in (days)" extra="Leave empty for no expiration">
            <InputNumber min={1} max={365} className="w-full" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
