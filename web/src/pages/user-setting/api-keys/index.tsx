// --- CYLLENE CUSTOM CODE ---
// Per-user API key management. Any workspace member can mint a key scoped to
// their own RBAC permissions in the active workspace. Org / workspace-wide
// service keys are managed by admins via the management panel — those are
// distinct from the personal keys here.
// --- END CYLLENE CUSTOM CODE ---
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { LucideKeyRound, LucideTrash2, Plus } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ApiKeyScope } from '@/services/api-key-service';
import { ProfileSettingWrapperCard } from '../components/user-setting-header';
import CreateApiKeyModal from './create-api-key-modal';
import { useListMyApiKeys, useRevokeMyApiKey } from './hooks';
import RevealTokenDialog from './reveal-token-dialog';

const formatTimestamp = (ms: number | null | undefined) => {
  if (!ms) return '—';
  return new Date(ms).toLocaleString();
};

const formatExpiry = (iso: string | null) => {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString();
};

const ApiKeysPage = () => {
  const { t } = useTranslation();
  const { keys, isFetching } = useListMyApiKeys();
  const revoke = useRevokeMyApiKey();

  const [createOpen, setCreateOpen] = useState(false);
  const [revealedKey, setRevealedKey] = useState<ApiKeyScope | null>(null);

  return (
    <>
      <ProfileSettingWrapperCard
        header={
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <LucideKeyRound className="size-5 text-text-secondary" />
              <span className="font-bold text-xl">{t('setting.apiKeys')}</span>
            </div>
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="size-4 mr-2" />
              {t('apiKeys.createButton')}
            </Button>
          </div>
        }
      >
        <div className="p-5 overflow-auto">
          {isFetching ? (
            <div className="text-text-secondary">{t('common.loading')}</div>
          ) : keys.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 gap-3 text-text-secondary">
              <LucideKeyRound className="size-8" />
              <p>{t('apiKeys.emptyState')}</p>
              <Button variant="outline" onClick={() => setCreateOpen(true)}>
                <Plus className="size-4 mr-2" />
                {t('apiKeys.createButton')}
              </Button>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t('apiKeys.name')}</TableHead>
                  <TableHead>{t('apiKeys.tokenPreview')}</TableHead>
                  <TableHead>{t('apiKeys.permissions')}</TableHead>
                  <TableHead>{t('apiKeys.expiresAt')}</TableHead>
                  <TableHead>{t('apiKeys.lastUsed')}</TableHead>
                  <TableHead className="w-12"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {keys.map((k) => (
                  <TableRow key={k.id}>
                    <TableCell className="font-medium">{k.name}</TableCell>
                    <TableCell>
                      <code className="text-xs">{k.token_preview}</code>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {k.permissions.map((p) => (
                          <Badge
                            key={p}
                            variant="secondary"
                            className="text-[10px]"
                          >
                            {p}
                          </Badge>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell>{formatExpiry(k.expires_at)}</TableCell>
                    <TableCell>{formatTimestamp(k.update_time)}</TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={() => revoke.mutate(k.id)}
                        disabled={revoke.isPending}
                      >
                        <LucideTrash2 className="size-4 text-state-error" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      </ProfileSettingWrapperCard>

      <CreateApiKeyModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(scope) => {
          setCreateOpen(false);
          setRevealedKey(scope);
        }}
      />

      <RevealTokenDialog
        scope={revealedKey}
        onClose={() => setRevealedKey(null)}
      />
    </>
  );
};

export default ApiKeysPage;
