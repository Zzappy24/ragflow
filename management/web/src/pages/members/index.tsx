import { useEffect, useState } from 'react';
import { useSearchParams, useParams } from 'react-router-dom';
import { Table, Button, Card, Modal, Form, Input, Select, App, Popconfirm, Space, Typography, Tooltip, Tag } from 'antd';
import { PlusOutlined, UserAddOutlined, CopyOutlined, FireOutlined, MinusCircleOutlined } from '@ant-design/icons';
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
  is_active?: string; // '0' = invitation jamais consommée
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
  const [memberSearch, setMemberSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm();
  const { message } = App.useApp();

  // Scope workspace : les personnes ajoutables sont par définition les
  // membres de l'org parente pas encore dans le workspace — on les propose
  // dans un sélecteur au lieu de faire retaper un email déjà connu.
  const [addable, setAddable] = useState<{ user_id: string; email: string | null; nickname: string | null }[] | null>(null);
  const openAddModal = () => {
    setModalOpen(true);
    if (scope === 'ws') {
      setAddable(null);
      api.get(`/workspaces/${scopeId}/addable-members`)
        .then((res) => setAddable(res.data))
        .catch(() => setAddable([]));
    }
  };

  // Invite-user (org-scope only): creates a brand-new user atomically through
  // /api/admin/users and returns a single-use invite URL the admin can copy.
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteForm] = Form.useForm();
  const [inviteBusy, setInviteBusy] = useState(false);
  const [inviteResult, setInviteResult] = useState<{ email: string; invite_url: string; email_sent: boolean } | null>(null);

  // Flux unifié « tout est invitation » (zéro-leak) : l'admin soumet des
  // emails, le backend invite sans jamais révéler si un compte existe.
  // La distinction se joue à l'acceptation, côté invité.
  interface OrgInvitation { id: string; email: string; role: string; ws_id?: string | null; ws_role?: string | null; expires_at: string | null; expired: boolean }
  interface InviteResult { email: string; status: string; email_sent?: boolean; invite_url?: string | null; detail?: string }
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchEmails, setBatchEmails] = useState<string[]>([]);
  const [batchRole, setBatchRole] = useState('member');
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchResults, setBatchResults] = useState<InviteResult[] | null>(null);
  const [invitations, setInvitations] = useState<OrgInvitation[]>([]);
  const [batchWs, setBatchWs] = useState<string | undefined>(undefined);
  const [batchWsRole, setBatchWsRole] = useState('viewer');
  const [orgWorkspaces, setOrgWorkspaces] = useState<{ id: string; name: string }[]>([]);
  // Édition d'une invitation EN ATTENTE (sans re-mail : le lien déjà envoyé
  // ne porte que l'id — la destination est lue dans la row à l'acceptation).
  const [editInvite, setEditInvite] = useState<OrgInvitation | null>(null);
  const [editWs, setEditWs] = useState<string | undefined>(undefined);
  const [editWsRole, setEditWsRole] = useState('viewer');
  const [editRole, setEditRole] = useState('member');

  const onSaveInviteEdit = async () => {
    if (!editInvite) return;
    try {
      await api.patch(`/org-invites/${editInvite.id}`, {
        role: editRole,
        ws_id: editWs ?? '',
        ws_role: editWs ? editWsRole : undefined,
      });
      message.success('Invitation mise à jour — le lien déjà envoyé reste valable');
      setEditInvite(null);
      fetchInvitations();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg || 'Échec de la mise à jour');
    }
  };

  const fetchInvitations = () => {
    if (scope !== 'org' || !scopeId) return;
    api.get(`/orgs/${scopeId}/members/invitations`)
      .then((res) => setInvitations(res.data))
      .catch(() => setInvitations([]));
    api.get(`/orgs/${scopeId}/workspaces`)
      .then((res) => setOrgWorkspaces(res.data))
      .catch(() => {});
  };

  const onBatchInvite = async () => {
    if (batchEmails.length === 0 || batchBusy) return;
    setBatchBusy(true);
    try {
      const res = await api.post(`/orgs/${scopeId}/members/invitations`, {
        emails: batchEmails,
        role: batchRole,
        ws_id: batchWs || undefined,
        ws_role: batchWs ? batchWsRole : undefined,
      });
      setBatchResults(res.data);
      fetchInvitations();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg || "Échec de l'envoi des invitations");
    } finally {
      setBatchBusy(false);
    }
  };

  const onResendOrgInvite = async (inv: OrgInvitation) => {
    try {
      const res = await api.post(`/org-invites/${inv.id}/resend`);
      if (res.data.invite_url) {
        Modal.info({
          title: 'Email non envoyé — lien à transmettre manuellement',
          content: <Typography.Paragraph copyable code>{res.data.invite_url}</Typography.Paragraph>,
        });
      } else {
        message.success(`Invitation renvoyée à ${inv.email}`);
      }
      fetchInvitations();
    } catch {
      message.error('Échec du renvoi');
    }
  };

  const onCancelOrgInvite = async (inv: OrgInvitation) => {
    try {
      await api.delete(`/org-invites/${inv.id}`);
      message.success('Invitation annulée — le lien est invalidé');
      fetchInvitations();
    } catch {
      message.error("Échec de l'annulation");
    }
  };

  const isSuperuser = useAuthStore((s) => s.user?.is_superuser ?? false);

  const scope = wsId ? 'ws' : 'org';
  const scopeId = wsId || orgId || '';
  const memberUrl = (uid: string) =>
    scope === 'ws' ? `/workspaces/${scopeId}/members/${uid}` : `/orgs/${scopeId}/members/${uid}`;

  useEffect(fetchInvitations, [scopeId, scope]); // eslint-disable-line react-hooks/exhaustive-deps

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
      setInviteResult({ email: res.data.email, invite_url: res.data.invite_url, email_sent: res.data.email_sent });
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

  const onResendInvite = async (userId: string) => {
    try {
      const res = await api.post(`/users/${userId}/resend-invite`);
      Modal.info({
        title: res.data.email_sent
          ? 'Invitation renvoyée par email'
          : 'Nouveau lien généré — email non envoyé, transmettez-le manuellement',
        content: (
          <Typography.Paragraph copyable code>{res.data.invite_url}</Typography.Paragraph>
        ),
      });
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(msg ?? "Échec du renvoi de l'invitation");
    }
  };

  const columns: ColumnsType<Member> = [
    { title: 'Email', dataIndex: 'email' },
    {
      title: 'Nickname',
      dataIndex: 'nickname',
      render: (v: string | null, record: Member) => (
        <span>
          {v}
          {record.is_active === '0' && (
            <Tooltip title="Invitation jamais consommée — renvoyer un lien frais">
              <Button type="link" size="small" onClick={() => onResendInvite(record.user_id)}>
                Renvoyer l'invitation
              </Button>
            </Tooltip>
          )}
        </span>
      ),
    },
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
          <Input.Search allowClear placeholder="Rechercher nom ou email…"
                        style={{ width: 240 }} value={memberSearch}
                        onChange={(e) => setMemberSearch(e.target.value)} />
          <BulkDeleteButton />
          {/* Ajouter un utilisateur DÉJÀ existant (add_org_member / add_ws_member).
              Pour l'org, indispensable quand le compte existe déjà (superadmin,
              membre d'une autre org, ex-testeur) — l'invitation, elle, CRÉE un
              compte et échoue si l'email existe. */}
          {scope === 'ws' ? (
            <Button icon={<PlusOutlined />} onClick={openAddModal}>
              Ajouter depuis l'organisation
            </Button>
          ) : (
            <>
              {/* Escape hatch ops : rattacher un compte existant sans email —
                  superusers uniquement (pas de leak, ils voient tout). */}
              {isSuperuser && (
                <Button icon={<PlusOutlined />} onClick={openAddModal}>
                  Ajouter un membre existant
                </Button>
              )}
              <Button type="primary" icon={<UserAddOutlined />}
                      onClick={() => {
                        setBatchEmails([]); setBatchResults(null); setBatchWs(undefined); setBatchOpen(true);
                        api.get(`/orgs/${scopeId}/workspaces`)
                          .then((res) => setOrgWorkspaces(res.data))
                          .catch(() => setOrgWorkspaces([]));
                      }}>
                Inviter des membres
              </Button>
            </>
          )}
        </Space>
      </div>

      <Card>
        <Table
          scroll={{ x: 'max-content' }}
          columns={columns}
          dataSource={members.filter((m) => {
            const q = memberSearch.trim().toLowerCase();
            if (!q) return true;
            return (m.email ?? '').toLowerCase().includes(q)
              || (m.nickname ?? '').toLowerCase().includes(q);
          })}
          rowKey="user_id"
          loading={loading}
          pagination={{ pageSize: 25, hideOnSinglePage: true, showSizeChanger: false }}
          rowSelection={rowSelection}
        />
      </Card>

      {scope === 'org' && invitations.length > 0 && (
        <Card className="mt-4" size="small"
              title={`Invitations en attente (${invitations.length})`}>
          <Table rowKey="id" size="small" showHeader={false}
                 pagination={{ pageSize: 25, hideOnSinglePage: true, showSizeChanger: false }}
                 dataSource={invitations}
                 columns={[
                   { title: 'Email', dataIndex: 'email' },
                   { title: 'Rôle', dataIndex: 'role', width: 220,
                     render: (r: string, inv) => (
                       <Space size={4}>
                         <Tag>{r}</Tag>
                         {inv.ws_id && (
                           <Tag color="blue">
                             {orgWorkspaces.find((w) => w.id === inv.ws_id)?.name ?? 'workspace'} · {inv.ws_role}
                           </Tag>
                         )}
                       </Space>
                     ) },
                   { title: 'Expire', dataIndex: 'expires_at', width: 220,
                     render: (v: string | null, inv) => inv.expired
                       ? <Tag color="red">expirée</Tag>
                       : (v ? `expire le ${new Date(v).toLocaleString()}` : '') },
                   { title: '', width: 240,
                     render: (_: unknown, inv) => (
                       <Space size="small">
                         <Button size="small" onClick={() => {
                           setEditInvite(inv);
                           setEditRole(inv.role);
                           setEditWs(inv.ws_id ?? undefined);
                           setEditWsRole(inv.ws_role ?? 'viewer');
                         }}>Modifier</Button>
                         <Button size="small" onClick={() => onResendOrgInvite(inv)}>Renvoyer</Button>
                         <Popconfirm title={`Annuler l'invitation de ${inv.email} ?`}
                                     okText="Annuler l'invitation" okButtonProps={{ danger: true }}
                                     onConfirm={() => onCancelOrgInvite(inv)}>
                           <Button size="small" type="text" danger icon={<MinusCircleOutlined />} />
                         </Popconfirm>
                       </Space>
                     ) },
                 ]} />
        </Card>
      )}

      <Modal title="Inviter des membres" open={batchOpen}
             onOk={batchResults
               ? () => { setBatchOpen(false); setBatchResults(null); setBatchEmails([]); }
               : onBatchInvite}
             okText={batchResults ? 'Fermer' : 'Envoyer les invitations'}
             confirmLoading={batchBusy}
             cancelButtonProps={batchResults ? { style: { display: 'none' } } : undefined}
             onCancel={() => { setBatchOpen(false); setBatchResults(null); }}>
        {batchResults ? (
          <Table rowKey="email" size="small" pagination={false} dataSource={batchResults}
                 columns={[
                   { title: 'Email', dataIndex: 'email' },
                   { title: 'Statut',
                     render: (_: unknown, r) => {
                       if (r.status === 'invited') {
                         return r.email_sent
                           ? <Tag color="green">✓ invitation envoyée</Tag>
                           : (
                             <span>
                               invité — lien à transmettre :{' '}
                               <Typography.Text copyable={{ text: r.invite_url ?? '' }} code>
                                 {r.invite_url}
                               </Typography.Text>
                             </span>
                           );
                       }
                       if (r.status === 'already_member') return <Tag>déjà membre</Tag>;
                       if (r.status === 'quota_exceeded') return <Tag color="red">quota utilisateurs atteint</Tag>;
                       return <Tag color="red">email invalide</Tag>;
                     } },
                 ]} />
        ) : (
          <Form layout="vertical" className="mt-4">
            <p className="text-gray-500 mb-3">
              Chaque personne reçoit un lien d'invitation : elle crée son compte si
              elle n'en a pas, ou accepte simplement de rejoindre l'organisation.
              Personne n'est ajouté sans avoir accepté.
            </p>
            <Form.Item label="Emails" required>
              <Select mode="tags" tokenSeparators={['\n', ',', ';', ' ']}
                      placeholder="Saisir ou coller des emails…"
                      value={batchEmails} onChange={setBatchEmails}
                      open={false} suffixIcon={null} />
            </Form.Item>
            <Form.Item label="Rôle dans l'organisation">
              <Select value={batchRole} onChange={setBatchRole}
                      options={[{ value: 'member', label: 'Member' }, { value: 'org_admin', label: 'Org Admin' }]} />
            </Form.Item>
            <Form.Item label="Workspace (optionnel)"
                       extra="Pré-affectation appliquée à l'acceptation. Sans choix : un nouveau compte rejoint le workspace par défaut ; un compte existant rejoint l'organisation seule.">
              <Select allowClear placeholder="Aucun (comportement par défaut)"
                      value={batchWs} onChange={setBatchWs}
                      options={orgWorkspaces.map((w) => ({ value: w.id, label: w.name }))} />
            </Form.Item>
            {batchWs && (
              <Form.Item label="Rôle dans le workspace">
                <Select value={batchWsRole} onChange={setBatchWsRole}
                        options={[{ value: 'ws_admin', label: 'WS Admin' }, { value: 'editor', label: 'Editor' }, { value: 'viewer', label: 'Viewer' }]} />
              </Form.Item>
            )}
          </Form>
        )}
      </Modal>

      <Modal title={editInvite ? `Modifier l'invitation — ${editInvite.email}` : ''}
             open={!!editInvite} onOk={onSaveInviteEdit} okText="Enregistrer"
             onCancel={() => setEditInvite(null)}>
        <p className="text-gray-500 mb-3">
          Le lien déjà envoyé reste valable : à l'acceptation, la personne rejoindra
          la destination mise à jour. Aucun email n'est renvoyé.
        </p>
        <Form layout="vertical">
          <Form.Item label="Rôle dans l'organisation">
            <Select value={editRole} onChange={setEditRole}
                    options={[{ value: 'member', label: 'Member' }, { value: 'org_admin', label: 'Org Admin' }]} />
          </Form.Item>
          <Form.Item label="Workspace (optionnel)">
            <Select allowClear placeholder="Aucun (comportement par défaut)"
                    value={editWs} onChange={setEditWs}
                    options={orgWorkspaces.map((w) => ({ value: w.id, label: w.name }))} />
          </Form.Item>
          {editWs && (
            <Form.Item label="Rôle dans le workspace">
              <Select value={editWsRole} onChange={setEditWsRole}
                      options={[{ value: 'ws_admin', label: 'WS Admin' }, { value: 'editor', label: 'Editor' }, { value: 'viewer', label: 'Viewer' }]} />
            </Form.Item>
          )}
        </Form>
      </Modal>

      <Modal title={scope === 'ws' ? "Ajouter depuis l'organisation" : 'Ajouter un membre existant'}
             open={modalOpen} onOk={onAdd} onCancel={() => setModalOpen(false)} okText="Ajouter">
        <Form form={form} layout="vertical" className="mt-4">
          {scope === 'org' && (
            <p className="text-gray-500 mb-3">
              L'utilisateur doit déjà avoir un compte. Pour créer un nouveau compte,
              utilisez « Inviter (nouveau compte) ».
            </p>
          )}
          {scope === 'ws' ? (
            <Form.Item name="email" label="Membre de l'organisation"
                       rules={[{ required: true }]}
                       extra={addable?.length === 0
                         ? "Tous les membres de l'organisation sont déjà dans ce workspace — invitez d'abord la personne dans l'organisation."
                         : 'Seuls les membres de l\'organisation pas encore présents dans ce workspace sont proposés.'}>
              <Select
                showSearch
                loading={addable === null}
                placeholder="Rechercher par nom ou email…"
                optionFilterProp="label"
                options={(addable ?? [])
                  .filter((u) => u.email)
                  .map((u) => ({
                    value: u.email as string,
                    label: u.nickname ? `${u.nickname} — ${u.email}` : (u.email as string),
                  }))}
              />
            </Form.Item>
          ) : (
          <Form.Item name="email" label="Email" rules={[{ required: true, type: 'email' }]}>
            <Input placeholder="user@example.com" />
          </Form.Item>
          )}
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
        <p className="mb-2">
          {inviteResult?.email_sent
            ? <Tag color="green">Email d'invitation envoyé</Tag>
            : <Tag color="orange">Email non envoyé — transmettez le lien ci-dessous</Tag>}
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
