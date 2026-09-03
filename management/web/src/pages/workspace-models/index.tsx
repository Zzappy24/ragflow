import { useEffect, useState } from 'react';
import {
  Card, Table, Button, Modal, Form, Input, InputNumber, Select, App,
  Popconfirm, Tag, Space, Divider, Typography, Alert, Switch, Tooltip,
} from 'antd';
import {
  PlusOutlined, DeleteOutlined, EditOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import api from '@/lib/api';

const { Text, Title } = Typography;

// Only the 3 local-inference providers supported in this panel.
const FACTORIES = [
  { label: 'Ollama (local)',            value: 'Ollama' },
  { label: 'vLLM (production)',         value: 'VLLM' },
  { label: 'OpenAI-Compatible (generic)', value: 'OpenAI-API-Compatible' },
];

const MODEL_TYPES = [
  { label: 'Chat',           value: 'chat' },
  { label: 'Embedding',      value: 'embedding' },
  { label: 'Image to Text',  value: 'image2text' },
  { label: 'Speech to Text', value: 'speech2text' },
  { label: 'Rerank',         value: 'rerank' },
  { label: 'TTS',            value: 'tts' },
];

// Mapping type de modèle → champ de défaut du tenant (PUT /models/defaults).
const TYPE_TO_DEFAULT_FIELD: Record<string, string> = {
  chat: 'llm_id',
  embedding: 'embd_id',
  image2text: 'img2txt_id',
  speech2text: 'asr_id',
  rerank: 'rerank_id',
  tts: 'tts_id',
};

const DEFAULT_BASE_URLS: Record<string, string> = {
  Ollama: 'http://localhost:11434',
  VLLM:   'http://localhost:8000/v1',
  'OpenAI-API-Compatible': '',
};

interface Provider {
  llm_factory: string;
  llm_name: string;
  model_type: string;
  api_base: string;
  max_tokens: number;
  used_tokens: number;
  status: string;
  is_tools: boolean;
}

interface Defaults {
  llm_id: string;
  embd_id: string;
  asr_id: string;
  img2txt_id: string;
  rerank_id: string;
  tts_id: string;
}

export default function WorkspaceModelsPage({ wsId }: { wsId?: string }) {
  const { message } = App.useApp();
  const [providers, setProviders] = useState<Provider[]>([]);
  const [defaults, setDefaults] = useState<Defaults | null>(null);
  const [loading, setLoading] = useState(true);

  // Add modal
  const [addOpen, setAddOpen] = useState(false);
  const [addForm] = Form.useForm();
  const [addSaving, setAddSaving] = useState(false);
  const [verifyLoading, setVerifyLoading] = useState(false);

  // Edit modal
  const [editOpen, setEditOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Provider | null>(null);
  const [editForm] = Form.useForm();
  const [editSaving, setEditSaving] = useState(false);

  // Defaults modal
  const [defaultsOpen, setDefaultsOpen] = useState(false);
  const [defaultsForm] = Form.useForm();
  const [defaultsSaving, setDefaultsSaving] = useState(false);

  const fetchAll = () => {
    if (!wsId) { setLoading(false); return; }
    setLoading(true);
    Promise.all([
      api.get(`/workspaces/${wsId}/models/providers`),
      api.get(`/workspaces/${wsId}/models/defaults`),
    ])
      .then(([pRes, dRes]) => {
        setProviders(pRes.data);
        setDefaults(dRes.data);
      })
      .catch(() => message.error('Failed to load model configuration'))
      .finally(() => setLoading(false));
  };

  useEffect(fetchAll, [wsId]);

  // ---- Add provider --------------------------------------------------------

  // Pré-coche « set as default » quand aucun défaut n'existe pour le type
  // choisi — le cas qui laissait des workspaces sans embedding par défaut.
  const addModelType = Form.useWatch('model_type', addForm);
  useEffect(() => {
    if (!addOpen || !addModelType) return;
    const field = TYPE_TO_DEFAULT_FIELD[addModelType];
    if (field && defaults && !defaults[field as keyof Defaults]) {
      addForm.setFieldValue('set_as_default', true);
    }
  }, [addOpen, addModelType, defaults, addForm]);

  const onAddProvider = async () => {
    try {
      const { set_as_default, ...values } = await addForm.validateFields();
      setAddSaving(true);
      await api.post(`/workspaces/${wsId}/models/providers`, values);
      const defaultField = TYPE_TO_DEFAULT_FIELD[values.model_type as string];
      if (set_as_default && defaultField) {
        await api.put(`/workspaces/${wsId}/models/defaults`, {
          [defaultField]: `${values.llm_name}@${values.llm_factory}`,
        });
        message.success('Model provider added and set as workspace default');
      } else {
        message.success('Model provider added');
      }
      setAddOpen(false);
      addForm.resetFields();
      fetchAll();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      if (msg) message.error(msg);
    } finally {
      setAddSaving(false);
    }
  };

  const onVerify = async () => {
    let values: Record<string, unknown>;
    try {
      values = await addForm.validateFields(['llm_factory', 'llm_name', 'model_type', 'api_key', 'api_base']);
    } catch {
      return;
    }
    setVerifyLoading(true);
    try {
      const res = await api.post(`/workspaces/${wsId}/models/verify`, values);
      if (res.data?.ok) {
        message.success(res.data.message || 'Connection successful');
      } else {
        message.error(res.data?.message || 'Verification failed');
      }
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg || 'Verification failed');
    } finally {
      setVerifyLoading(false);
    }
  };

  // ---- Toggle enable/disable -----------------------------------------------

  const onToggleStatus = async (record: Provider, enabled: boolean) => {
    try {
      await api.patch(
        `/workspaces/${wsId}/models/providers/status`,
        null,
        { params: { factory: record.llm_factory, llm_name: record.llm_name, enabled } },
      );
      message.success(enabled ? 'Model enabled' : 'Model disabled');
      fetchAll();
    } catch {
      message.error('Failed to update model status');
    }
  };

  // ---- Edit provider -------------------------------------------------------

  const openEdit = (record: Provider) => {
    setEditTarget(record);
    // is_tools DOIT être pré-rempli : sinon la checkbox part décochée et
    // toute édition (même juste max_tokens) efface le flag en base —
    // suspect n°1 de la ligne BU Cloud à false (incident 2026-09-03).
    editForm.setFieldsValue({
      api_base: record.api_base,
      max_tokens: record.max_tokens,
      is_tools: record.is_tools,
    });
    setEditOpen(true);
  };

  const onEditSave = async () => {
    if (!editTarget) return;
    try {
      const values = await editForm.validateFields();
      setEditSaving(true);
      await api.put(
        `/workspaces/${wsId}/models/providers`,
        values,
        { params: { factory: editTarget.llm_factory, llm_name: editTarget.llm_name } },
      );
      message.success('Model updated');
      setEditOpen(false);
      setEditTarget(null);
      fetchAll();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      if (msg) message.error(msg);
    } finally {
      setEditSaving(false);
    }
  };

  // ---- Delete provider -----------------------------------------------------

  const onDeleteProvider = async (factory: string, llmName: string) => {
    try {
      await api.delete(`/workspaces/${wsId}/models/providers`, { params: { factory, llm_name: llmName } });
      message.success('Model removed');
      fetchAll();
    } catch {
      message.error('Failed to remove model');
    }
  };

  // ---- Defaults modal ------------------------------------------------------

  const onSaveDefaults = async () => {
    try {
      const values = await defaultsForm.validateFields();
      const filtered = Object.fromEntries(
        Object.entries(values).filter(([, v]) => v !== undefined && v !== ''),
      );
      setDefaultsSaving(true);
      await api.put(`/workspaces/${wsId}/models/defaults`, filtered);
      message.success('Default models updated');
      setDefaultsOpen(false);
      fetchAll();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      if (msg) message.error(msg);
    } finally {
      setDefaultsSaving(false);
    }
  };

  // ---- Table columns -------------------------------------------------------

  const providerOptions = providers.map((p) => ({
    label: `${p.llm_name} (${p.llm_factory})`,
    value: `${p.llm_name}@${p.llm_factory}`,
  }));

  const columns = [
    {
      title: 'Factory',
      dataIndex: 'llm_factory',
      width: 160,
      render: (f: string) => {
        const found = FACTORIES.find((x) => x.value === f);
        return found ? found.label : f;
      },
    },
    { title: 'Model Name', dataIndex: 'llm_name' },
    {
      title: 'Type',
      dataIndex: 'model_type',
      width: 120,
      render: (t: string) => <Tag>{MODEL_TYPES.find((x) => x.value === t)?.label ?? t}</Tag>,
    },
    {
      title: 'Tools',
      dataIndex: 'is_tools',
      width: 80,
      render: (v: boolean, record: Provider) =>
        record.model_type === 'chat' ? (
          <Tag color={v ? 'green' : 'red'}>{v ? 'FC \u2713' : 'FC \u2717'}</Tag>
        ) : (
          <span className="text-gray-300">{'\u2014'}</span>
        ),
    },
    {
      title: 'Base URL',
      dataIndex: 'api_base',
      render: (v: string) =>
        v ? <Text code className="text-xs">{v}</Text> : <span className="text-gray-400">—</span>,
    },
    { title: 'Max Tokens', dataIndex: 'max_tokens', width: 110 },
    {
      title: 'Used Tokens',
      dataIndex: 'used_tokens',
      width: 110,
      render: (v: number) => <Text type="secondary">{v.toLocaleString()}</Text>,
    },
    {
      title: 'Active',
      dataIndex: 'status',
      width: 80,
      render: (s: string, record: Provider) => (
        <Switch
          size="small"
          checked={s === '1'}
          onChange={(checked) => onToggleStatus(record, checked)}
        />
      ),
    },
    {
      title: '',
      width: 80,
      render: (_: unknown, record: Provider) => (
        <Space size={4}>
          <Tooltip title="Edit">
            <Button type="text" icon={<EditOutlined />} size="small" onClick={() => openEdit(record)} />
          </Tooltip>
          <Popconfirm
            title={`Remove ${record.llm_name}?`}
            onConfirm={() => onDeleteProvider(record.llm_factory, record.llm_name)}
          >
            <Tooltip title="Delete">
              <Button type="text" danger icon={<DeleteOutlined />} size="small" />
            </Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  if (!wsId) return null;

  return (
    <div>
      {/* ---- Providers table ----------------------------------------------- */}
      <div className="flex justify-between items-center mb-4">
        <Title level={5} className="!mb-0">Model Providers</Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setAddOpen(true)}>
          Add Model
        </Button>
      </div>

      <Card className="mb-6">
        <Table
          columns={columns}
          dataSource={providers}
          rowKey={(r) => `${r.llm_factory}__${r.llm_name}`}
          loading={loading}
          pagination={false}
          size="small"
        />
      </Card>

      <Divider />

      {/* ---- Default model assignments ------------------------------------- */}
      <div className="flex justify-between items-center mb-4">
        <Title level={5} className="!mb-0">Default Models</Title>
        <Button
          onClick={() => {
            defaultsForm.setFieldsValue(defaults ?? {});
            setDefaultsOpen(true);
          }}
        >
          Edit Defaults
        </Button>
      </div>

      {defaults && (
        <Card>
          {(!defaults.embd_id || !defaults.llm_id) ? (
            <Alert
              type="warning"
              showIcon
              className="mb-4"
              message="Défauts critiques manquants"
              description={`Ce workspace n'a pas de modèle ${[
                !defaults.llm_id && 'Chat',
                !defaults.embd_id && 'Embedding',
              ].filter(Boolean).join(' ni ')} par défaut : le parsing et/ou le chat échoueront pour ses membres avec une erreur peu explicite. Cliquez sur « Edit Defaults » pour les définir. (L'héritage du workspace template ne s'applique qu'à la création du workspace, jamais rétroactivement.)`}
            />
          ) : (
            <Alert
              type="info"
              showIcon
              className="mb-4"
              message="These defaults are used for all parsing and chat operations in this workspace. Members cannot change them — only org admins via this panel."
            />
          )}
          <Space direction="vertical" className="w-full" size="small">
            {[
              { label: 'Chat (LLM)',     value: defaults.llm_id,     critical: true },
              { label: 'Embedding',      value: defaults.embd_id,    critical: true },
              { label: 'Image to Text',  value: defaults.img2txt_id, critical: false },
              { label: 'Speech to Text', value: defaults.asr_id,     critical: false },
              { label: 'Rerank',         value: defaults.rerank_id,  critical: false },
              { label: 'TTS',            value: defaults.tts_id,     critical: false },
            ].map(({ label, value, critical }) => (
              <div key={label} className="flex justify-between">
                <Text type="secondary">{label}</Text>
                {value
                  ? <Text code className="text-xs">{value}</Text>
                  : critical
                    ? <Text type="danger">non défini — requis</Text>
                    : <Text type="secondary">—</Text>}
              </div>
            ))}
          </Space>
        </Card>
      )}

      {/* ---- Add model modal ----------------------------------------------- */}
      <Modal
        title="Add Model Provider"
        open={addOpen}
        onCancel={() => { setAddOpen(false); addForm.resetFields(); }}
        footer={
          <Space>
            <Button onClick={() => { setAddOpen(false); addForm.resetFields(); }}>Cancel</Button>
            <Button
              icon={<ThunderboltOutlined />}
              loading={verifyLoading}
              onClick={onVerify}
            >
              Verify
            </Button>
            <Button type="primary" loading={addSaving} onClick={onAddProvider}>
              Add
            </Button>
          </Space>
        }
        width={520}
      >
        <Form
          form={addForm}
          layout="vertical"
          className="mt-4"
          onValuesChange={(changed) => {
            // Auto-fill base URL when factory changes
            if (changed.llm_factory) {
              const url = DEFAULT_BASE_URLS[changed.llm_factory] ?? '';
              addForm.setFieldValue('api_base', url);
            }
          }}
        >
          <Form.Item name="llm_factory" label="Factory" rules={[{ required: true }]}>
            <Select options={FACTORIES} placeholder="Select provider" />
          </Form.Item>
          <Form.Item name="llm_name" label="Model Name" rules={[{ required: true }]}
            extra="Exact name as returned by the inference server (e.g. llama3, mistral)">
            <Input placeholder="e.g. llama3, mistral, nomic-embed-text" />
          </Form.Item>
          <Form.Item name="model_type" label="Model Type" rules={[{ required: true }]}>
            <Select options={MODEL_TYPES} placeholder="Select type" />
          </Form.Item>
          <Form.Item name="api_base" label="Base URL" rules={[{ required: true }]}>
            <Input placeholder="e.g. http://localhost:11434" />
          </Form.Item>
          <Form.Item name="api_key" label="API Key"
            extra="Leave empty for local models (will use placeholder)">
            <Input.Password placeholder="Leave empty for Ollama / vLLM without auth" />
          </Form.Item>
          <Form.Item name="max_tokens" label="Max Tokens" initialValue={8192}>
            <InputNumber min={1} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="is_tools" label="Function calling (tools)" valuePropName="checked"
            initialValue={false}
            extra="Requis pour les composants Agent (tool calling). À activer pour les modèles chat qui supportent les function calls — sinon l'Agent n'appellera jamais ses outils.">
            <Switch />
          </Form.Item>
          <Form.Item name="set_as_default" label="Définir comme modèle par défaut" valuePropName="checked"
            initialValue={false}
            extra="Utilisé par défaut pour ce type (parsing, chat…) dans ce workspace. Pré-coché quand aucun défaut n'existe encore pour ce type — sans défaut, le parsing/chat échoue.">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>

      {/* ---- Edit model modal ---------------------------------------------- */}
      <Modal
        title={editTarget ? `Edit — ${editTarget.llm_name} (${editTarget.llm_factory})` : 'Edit Model'}
        open={editOpen}
        onOk={onEditSave}
        onCancel={() => { setEditOpen(false); setEditTarget(null); }}
        okText="Save"
        confirmLoading={editSaving}
        width={480}
      >
        <Form form={editForm} layout="vertical" className="mt-4">
          <Form.Item name="api_base" label="Base URL">
            <Input placeholder="e.g. http://localhost:11434" />
          </Form.Item>
          <Form.Item name="api_key" label="New API Key"
            extra="Leave empty to keep current key">
            <Input.Password placeholder="Leave empty to keep current key" />
          </Form.Item>
          <Form.Item name="max_tokens" label="Max Tokens">
            <InputNumber min={1} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="is_tools" label="Function calling (tools)"
            extra="Laisser vide pour conserver la valeur actuelle.">
            <Select
              allowClear
              placeholder="Conserver la valeur actuelle"
              options={[
                { value: true, label: 'Activé' },
                { value: false, label: 'Désactivé' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* ---- Edit defaults modal ------------------------------------------- */}
      <Modal
        title="Edit Default Models"
        open={defaultsOpen}
        onOk={onSaveDefaults}
        onCancel={() => setDefaultsOpen(false)}
        okText="Save"
        confirmLoading={defaultsSaving}
        width={520}
      >
        <Alert
          type="warning"
          showIcon
          className="mb-4"
          message="Each model must already be added as a provider above. Select from the list."
        />
        <Form form={defaultsForm} layout="vertical" className="mt-4">
          {[
            { name: 'llm_id',      label: 'Chat (LLM)' },
            { name: 'embd_id',     label: 'Embedding' },
            { name: 'img2txt_id',  label: 'Image to Text' },
            { name: 'asr_id',      label: 'Speech to Text' },
            { name: 'rerank_id',   label: 'Rerank' },
            { name: 'tts_id',      label: 'TTS' },
          ].map(({ name, label }) => (
            <Form.Item key={name} name={name} label={label}>
              <Select
                options={providerOptions}
                allowClear
                showSearch
                placeholder={`Select ${label.toLowerCase()} model`}
              />
            </Form.Item>
          ))}
        </Form>
      </Modal>
    </div>
  );
}
