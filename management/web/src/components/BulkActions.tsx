import { useState } from 'react';
import { Button, Popconfirm, App } from 'antd';
import { MinusCircleOutlined, InboxOutlined } from '@ant-design/icons';

/**
 * Hook providing row-selection state + a bulk-delete button.
 *
 * variant:
 *   "remove"  — révocation d'accès (membres). Libellé "Retirer", icône MinusCircle, style neutre.
 *   "archive" — soft-delete (workspaces). Libellé "Archiver", icône Inbox, style orange.
 */
export function useBulkDelete<K extends React.Key = string>({
  entityName,
  deleteOne,
  onDone,
  variant = 'remove',
}: {
  entityName: string;
  deleteOne: (id: K) => Promise<unknown>;
  onDone: () => void;
  variant?: 'remove' | 'archive';
}) {
  const { message } = App.useApp();
  const [selectedIds, setSelectedIds] = useState<K[]>([]);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    const results = await Promise.allSettled(selectedIds.map((id) => deleteOne(id)));
    const failed = results.filter((r) => r.status === 'rejected').length;
    const ok = results.length - failed;
    if (ok) message.success(`${ok} ${entityName}(s) ${variant === 'archive' ? 'archivé(s)' : 'retiré(s)'}`);
    if (failed) message.error(`${failed} échec(s)`);
    setSelectedIds([]);
    setBusy(false);
    onDone();
  };

  const BulkDeleteButton = () => {
    if (selectedIds.length === 0) return null;
    if (variant === 'archive') {
      return (
        <Popconfirm
          title={`Archiver ${selectedIds.length} ${entityName}(s) ?`}
          description="Les éléments seront désactivés. Récupérables depuis les Archives."
          okText="Archiver"
          okButtonProps={{ style: { background: '#f97316', borderColor: '#f97316' } }}
          onConfirm={run}
        >
          <Button icon={<InboxOutlined />} loading={busy} style={{ color: '#f97316', borderColor: '#f97316' }}>
            Archiver ({selectedIds.length})
          </Button>
        </Popconfirm>
      );
    }
    return (
      <Popconfirm
        title={`Retirer ${selectedIds.length} ${entityName}(s) ?`}
        description="L'accès sera révoqué. Les comptes ne sont pas supprimés."
        okText="Retirer"
        onConfirm={run}
      >
        <Button icon={<MinusCircleOutlined />} loading={busy}>
          Retirer ({selectedIds.length})
        </Button>
      </Popconfirm>
    );
  };

  return {
    selectedIds,
    rowSelection: {
      selectedRowKeys: selectedIds as React.Key[],
      onChange: (keys: React.Key[]) => setSelectedIds(keys as K[]),
    },
    BulkDeleteButton,
    clear: () => setSelectedIds([]),
  };
}
