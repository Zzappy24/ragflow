import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Table, Button, Card, Space, Tag, Modal, Form, Input, InputNumber, App } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import api from '@/lib/api';
import { useAuthStore } from '@/stores/auth';

interface Org {
  id: string;
  name: string;
  slug: string;
  max_users: number;
  max_workspaces: number;
  max_datasets: number;
  create_time: string;
}

export default function OrganisationsPage() {
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm();
  const { message } = App.useApp();
  const { user } = useAuthStore();

  const fetchOrgs = () => {
    setLoading(true);
    api.get('/orgs').then((res) => setOrgs(res.data)).finally(() => setLoading(false));
  };

  useEffect(fetchOrgs, []);

  const onCreate = async () => {
    try {
      const values = await form.validateFields();
      await api.post('/orgs', values);
      message.success('Organisation created');
      setModalOpen(false);
      form.resetFields();
      fetchOrgs();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      if (msg) message.error(msg);
    }
  };

  const columns: ColumnsType<Org> = [
    {
      title: 'Name',
      dataIndex: 'name',
      render: (name: string, record: Org) => <Link to={`/organisations/${record.id}`}>{name}</Link>,
    },
    { title: 'Slug', dataIndex: 'slug', render: (slug: string) => <Tag>{slug}</Tag> },
    { title: 'Max Users', dataIndex: 'max_users', width: 100 },
    { title: 'Max Workspaces', dataIndex: 'max_workspaces', width: 130 },
    { title: 'Max Datasets', dataIndex: 'max_datasets', width: 120 },
  ];

  return (
    <div>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-semibold">Organisations</h2>
        {user?.is_superuser && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
            New Organisation
          </Button>
        )}
      </div>

      <Card>
        <Table
          columns={columns}
          dataSource={orgs}
          rowKey="id"
          loading={loading}
          pagination={false}
        />
      </Card>

      <Modal title="New Organisation" open={modalOpen} onOk={onCreate} onCancel={() => setModalOpen(false)} okText="Create">
        <Form form={form} layout="vertical" className="mt-4">
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="slug" label="Slug" rules={[{ required: true, pattern: /^[a-z0-9-]+$/, message: 'Lowercase letters, numbers and hyphens only' }]}>
            <Input />
          </Form.Item>
          <Space>
            <Form.Item name="max_users" label="Max Users" initialValue={50}>
              <InputNumber min={1} />
            </Form.Item>
            <Form.Item name="max_workspaces" label="Max Workspaces" initialValue={10}>
              <InputNumber min={1} />
            </Form.Item>
            <Form.Item name="max_datasets" label="Max Datasets" initialValue={100}>
              <InputNumber min={1} />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </div>
  );
}
