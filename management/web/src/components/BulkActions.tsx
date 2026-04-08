import { useState } from 'react';
import { Button, Popconfirm, App } from 'antd';
import { DeleteOutlined } from '@ant-design/icons';

/**
 * Hook providing row-selection state + a bulk-delete button.
 *
 * Usage:
 *   const { rowSelection, BulkDeleteButton, clear } = useBulkDelete<string>({
 *     entityName: 'member',
 *     deleteOne: (id) => api.delete(`/.../${id}`),
 *     onDone: refetch,
 *   });
 *   <Table rowSelection={rowSelection} ... />
 *   <BulkDeleteButton />
 */
export function useBulkDelete<K extends React.Key = string>({
  entityName,
  deleteOne,
  onDone,
}: {
  entityName: string;
  deleteOne: (id: K) => Promise<unknown>;
  onDone: () => void;
}) {
  const { message } = App.useApp();
  const [selectedIds, setSelectedIds] = useState<K[]>([]);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    const results = await Promise.allSettled(selectedIds.map((id) => deleteOne(id)));
    const failed = results.filter((r) => r.status === 'rejected').length;
    const ok = results.length - failed;
    if (ok) message.success(`${ok} ${entityName}(s) removed`);
    if (failed) message.error(`${failed} failed`);
    setSelectedIds([]);
    setBusy(false);
    onDone();
  };

  const BulkDeleteButton = () =>
    selectedIds.length > 0 ? (
      <Popconfirm
        title={`Delete ${selectedIds.length} ${entityName}(s)?`}
        description="This action cannot be undone."
        okText="Delete"
        okButtonProps={{ danger: true }}
        onConfirm={run}
      >
        <Button danger icon={<DeleteOutlined />} loading={busy}>
          Delete ({selectedIds.length})
        </Button>
      </Popconfirm>
    ) : null;

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
