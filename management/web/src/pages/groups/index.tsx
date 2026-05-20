import { useEffect, useState } from 'react';
import { useSearchParams, useParams } from 'react-router-dom';
import { Table, Button, Card, Modal, Form, Input, App, Tabs, Popconfirm, Space } from 'antd';
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons';
import api from '@/lib/api';
import { useBulkDelete } from '@/components/BulkActions';

interface Group {
  id: string;
  workspace_id: string;
  name: string;
  description: string;
}

export default function GroupsPage({ wsId: wsIdProp }: { wsId?: string } = {}) {
  const [searchParams] = useSearchParams();
  const params = useParams<{ wsId?: string }>();
  const wsId = wsIdProp || params.wsId || searchParams.get('ws') || '';
  const [groups, setGroups] = useState<Group[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [selectedGroup, setSelectedGroup] = useState<Group | null>(null);
  const [form] = Form.useForm();
  const { message } = App.useApp();

  const { rowSelection, BulkDeleteButton } = useBulkDelete<string>({
    entityName: 'group',
    deleteOne: (id) => api.delete(`/workspaces/${wsId}/groups/${id}`),
    onDone: () => {
      setSelectedGroup(null);
      fetchGroups();
    },
  });

  const fetchGroups = () => {
    if (!wsId) { setLoading(false); return; }
    setLoading(true);
    api.get(`/workspaces/${wsId}/groups`).then((res) => setGroups(res.data)).finally(() => setLoading(false));
  };

  useEffect(fetchGroups, [wsId]);

  const onCreate = async () => {
    try {
      const values = await form.validateFields();
      await api.post(`/workspaces/${wsId}/groups`, values);
      message.success('Group created');
      setModalOpen(false);
      form.resetFields();
      fetchGroups();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      if (msg) message.error(msg);
    }
  };

  const onDelete = async (gid: string) => {
    await api.delete(`/workspaces/${wsId}/groups/${gid}`);
    message.success('Group deleted');
    if (selectedGroup?.id === gid) setSelectedGroup(null);
    fetchGroups();
  };

  if (!wsId) {
    return (
      <Card>
        <p className="text-gray-500">Select a workspace: <code>?ws=WS_ID</code></p>
      </Card>
    );
  }

  const columns = [
    {
      title: 'Name',
      dataIndex: 'name',
      render: (name: string, record: Group) => (
        <a onClick={() => setSelectedGroup(record)}>{name}</a>
      ),
    },
    { title: 'Description', dataIndex: 'description', ellipsis: true },
    {
      title: '',
      width: 50,
      render: (_: unknown, record: Group) => (
        <Popconfirm title="Delete this group?" onConfirm={() => onDelete(record.id)}>
          <Button type="text" danger icon={<DeleteOutlined />} size="small" />
        </Popconfirm>
      ),
    },
  ];

  return (
    <div>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-semibold">Groups</h2>
        <Space>
          <BulkDeleteButton />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
            New Group
          </Button>
        </Space>
      </div>

      <div className="flex gap-4">
        <Card className="w-1/3">
          <Table
            columns={columns}
            dataSource={groups}
            rowKey="id"
            loading={loading}
            pagination={false}
            size="small"
            rowSelection={rowSelection}
          />
        </Card>

        <div className="w-2/3">
          {selectedGroup ? (
            <GroupDetail wsId={wsId} group={selectedGroup} />
          ) : (
            <Card><p className="text-gray-400">Select a group to manage its members and datasets</p></Card>
          )}
        </div>
      </div>

      <Modal title="New Group" open={modalOpen} onOk={onCreate} onCancel={() => setModalOpen(false)} okText="Create">
        <Form form={form} layout="vertical" className="mt-4">
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="Description">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

function GroupDetail({ wsId, group }: { wsId: string; group: Group }) {
  const [members, setMembers] = useState<Array<{ id: string; user_id: string; email: string }>>([]);
  const [datasets, setDatasets] = useState<Array<{ id: string; dataset_id: string }>>([]);
  const { message } = App.useApp();

  const fetchData = () => {
    api.get(`/workspaces/${wsId}/groups/${group.id}/members`).then((r) => setMembers(r.data));
    api.get(`/workspaces/${wsId}/groups/${group.id}/datasets`).then((r) => setDatasets(r.data));
  };

  useEffect(fetchData, [group.id]);

  const removeMember = async (uid: string) => {
    await api.delete(`/workspaces/${wsId}/groups/${group.id}/members/${uid}`);
    message.success('Member removed from group');
    fetchData();
  };

  const removeDataset = async (did: string) => {
    await api.delete(`/workspaces/${wsId}/groups/${group.id}/datasets/${did}`);
    message.success('Dataset removed from group');
    fetchData();
  };

  return (
    <Card title={group.name}>
      <Tabs
        items={[
          {
            key: 'members',
            label: `Members (${members.length})`,
            children: (
              <Table
                size="small"
                dataSource={members}
                rowKey="id"
                pagination={false}
                columns={[
                  { title: 'Email', dataIndex: 'email' },
                  {
                    title: '',
                    width: 50,
                    render: (_: unknown, r: { user_id: string }) => (
                      <Popconfirm title="Remove?" onConfirm={() => removeMember(r.user_id)}>
                        <Button type="text" danger icon={<DeleteOutlined />} size="small" />
                      </Popconfirm>
                    ),
                  },
                ]}
              />
            ),
          },
          {
            key: 'datasets',
            label: `Datasets (${datasets.length})`,
            children: (
              <Table
                size="small"
                dataSource={datasets}
                rowKey="id"
                pagination={false}
                columns={[
                  { title: 'Dataset ID', dataIndex: 'dataset_id' },
                  {
                    title: '',
                    width: 50,
                    render: (_: unknown, r: { dataset_id: string }) => (
                      <Popconfirm title="Remove?" onConfirm={() => removeDataset(r.dataset_id)}>
                        <Button type="text" danger icon={<DeleteOutlined />} size="small" />
                      </Popconfirm>
                    ),
                  },
                ]}
              />
            ),
          },
        ]}
      />
    </Card>
  );
}
