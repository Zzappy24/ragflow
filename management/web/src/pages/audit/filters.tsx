import { Select, Input, DatePicker, Space } from 'antd';
import dayjs from 'dayjs';

const { RangePicker } = DatePicker;

export interface AuditFilters {
  action?: string;
  actor_email?: string;
  resource_type?: string;
  status?: string;
  date_from?: number;
  date_to?: number;
}

const ACTION_OPTIONS = [
  { label: 'Toutes les actions', value: '' },
  {
    label: 'Organisations',
    options: [
      { label: 'ORG_ARCHIVE', value: 'ORG_ARCHIVE' },
      { label: 'ORG_RESTORE', value: 'ORG_RESTORE' },
      { label: 'ORG_PURGE', value: 'ORG_PURGE' },
    ],
  },
  {
    label: 'Workspaces',
    options: [
      { label: 'WS_CREATE', value: 'WS_CREATE' },
      { label: 'WS_ARCHIVE', value: 'WS_ARCHIVE' },
      { label: 'WS_RESTORE', value: 'WS_RESTORE' },
    ],
  },
  {
    label: 'Utilisateurs',
    options: [
      { label: 'USER_INVITE', value: 'USER_INVITE' },
      { label: 'USER_DELETE', value: 'USER_DELETE' },
      { label: 'USER_RESTORE', value: 'USER_RESTORE' },
      { label: 'USER_PURGE', value: 'USER_PURGE' },
    ],
  },
  {
    label: 'Membres org',
    options: [
      { label: 'ORG_MEMBER_ADD', value: 'ORG_MEMBER_ADD' },
      { label: 'ORG_MEMBER_REMOVE', value: 'ORG_MEMBER_REMOVE' },
      { label: 'ORG_MEMBER_ROLE_CHANGE', value: 'ORG_MEMBER_ROLE_CHANGE' },
    ],
  },
  {
    label: 'Membres workspace',
    options: [
      { label: 'WS_MEMBER_ADD', value: 'WS_MEMBER_ADD' },
      { label: 'WS_MEMBER_REMOVE', value: 'WS_MEMBER_REMOVE' },
      { label: 'WS_MEMBER_ROLE_CHANGE', value: 'WS_MEMBER_ROLE_CHANGE' },
    ],
  },
];

const RESOURCE_OPTIONS = [
  { label: 'Tous les types', value: '' },
  { label: 'user', value: 'user' },
  { label: 'workspace', value: 'workspace' },
  { label: 'organisation', value: 'organisation' },
];

const STATUS_OPTIONS = [
  { label: 'Tous', value: '' },
  { label: 'Succès', value: 'success' },
  { label: 'Échec', value: 'failure' },
];

interface Props {
  filters: AuditFilters;
  onChange: (f: AuditFilters) => void;
  showActorEmail?: boolean;
}

export function AuditFilterBar({ filters, onChange, showActorEmail = true }: Props) {
  const set = (patch: Partial<AuditFilters>) => onChange({ ...filters, ...patch });

  const handleRange = (_: unknown, [from, to]: [string, string]) => {
    set({
      date_from: from ? dayjs(from, 'DD/MM/YYYY').startOf('day').valueOf() : undefined,
      date_to: to ? dayjs(to, 'DD/MM/YYYY').endOf('day').valueOf() : undefined,
    });
  };

  return (
    <Space wrap size="small">
      <Select
        options={ACTION_OPTIONS}
        value={filters.action || ''}
        onChange={(v) => set({ action: v || undefined })}
        style={{ width: 200 }}
        placeholder="Action"
      />
      <Select
        options={RESOURCE_OPTIONS}
        value={filters.resource_type || ''}
        onChange={(v) => set({ resource_type: v || undefined })}
        style={{ width: 140 }}
        placeholder="Type de cible"
      />
      <Select
        options={STATUS_OPTIONS}
        value={filters.status || ''}
        onChange={(v) => set({ status: v || undefined })}
        style={{ width: 110 }}
        placeholder="Statut"
      />
      {showActorEmail && (
        <Input
          placeholder="Email acteur"
          allowClear
          value={filters.actor_email || ''}
          onChange={(e) => set({ actor_email: e.target.value || undefined })}
          style={{ width: 200 }}
        />
      )}
      <RangePicker
        format="DD/MM/YYYY"
        onChange={handleRange as any}
        placeholder={['Date début', 'Date fin']}
        style={{ width: 240 }}
      />
    </Space>
  );
}
