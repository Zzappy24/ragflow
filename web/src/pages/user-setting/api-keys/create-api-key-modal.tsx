import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ApiKeyScope } from '@/services/api-key-service';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCreateMyApiKey } from './hooks';

const ALL_PERMISSIONS = [
  'dataset.read',
  'dataset.create',
  'dataset.update',
  'dataset.delete',
  'document.read',
  'document.create',
  'document.delete',
  'agent.read',
  'agent.create',
  'agent.update',
  'agent.delete',
  'chat.read',
  'chat.create',
  'chat.update',
  'chat.delete',
  'chat.use',
];

const PRESETS: Record<string, string[]> = {
  'apiKeys.presetReadOnly': [
    'dataset.read',
    'document.read',
    'agent.read',
    'chat.read',
  ],
  'apiKeys.presetIntegration': [
    'dataset.read',
    'document.read',
    'agent.read',
    'chat.read',
    'chat.use',
  ],
  'apiKeys.presetFullWrite': ALL_PERMISSIONS,
};

type Props = {
  open: boolean;
  onClose: () => void;
  onCreated: (scope: ApiKeyScope) => void;
};

const CreateApiKeyModal = ({ open, onClose, onCreated }: Props) => {
  const { t } = useTranslation();
  const create = useCreateMyApiKey();

  const [name, setName] = useState('');
  const [permissions, setPermissions] = useState<string[]>(
    PRESETS['apiKeys.presetIntegration'],
  );
  const [expiresAt, setExpiresAt] = useState('');

  const togglePermission = (p: string) => {
    setPermissions((prev) =>
      prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p],
    );
  };

  const applyPreset = (key: string) => {
    setPermissions(Array.from(new Set(PRESETS[key])));
  };

  const reset = () => {
    setName('');
    setPermissions(PRESETS['apiKeys.presetIntegration']);
    setExpiresAt('');
  };

  const submit = async () => {
    if (!name.trim() || permissions.length === 0) return;
    const scope = await create.mutateAsync({
      name: name.trim(),
      permissions,
      expires_at: expiresAt ? expiresAt : null,
    });
    reset();
    onCreated(scope);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          reset();
          onClose();
        }
      }}
    >
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t('apiKeys.createTitle')}</DialogTitle>
          <DialogDescription>
            {t('apiKeys.createDescription')}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-2">
          <div className="grid gap-2">
            <Label htmlFor="apikey-name">{t('apiKeys.name')}</Label>
            <Input
              id="apikey-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('apiKeys.namePlaceholder') || ''}
              autoFocus
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="apikey-expiry">{t('apiKeys.expiresAt')}</Label>
            <Input
              id="apikey-expiry"
              type="date"
              value={expiresAt}
              onChange={(e) => setExpiresAt(e.target.value)}
            />
            <p className="text-xs text-text-secondary">
              {t('apiKeys.expiresAtHint')}
            </p>
          </div>

          <div className="grid gap-2">
            <Label>{t('apiKeys.permissions')}</Label>
            <div className="flex flex-wrap gap-2 mb-1">
              {Object.keys(PRESETS).map((key) => (
                <Button
                  key={key}
                  size="sm"
                  variant="outline"
                  type="button"
                  onClick={() => applyPreset(key)}
                >
                  {t(key)}
                </Button>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-2 max-h-64 overflow-auto p-2 border rounded-md">
              {ALL_PERMISSIONS.map((p) => (
                <label
                  key={p}
                  className="flex items-center gap-2 text-sm cursor-pointer"
                >
                  <Checkbox
                    checked={permissions.includes(p)}
                    onCheckedChange={() => togglePermission(p)}
                  />
                  <code className="text-xs">{p}</code>
                </label>
              ))}
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => {
              reset();
              onClose();
            }}
          >
            {t('common.cancel')}
          </Button>
          <Button
            onClick={submit}
            disabled={
              !name.trim() || permissions.length === 0 || create.isPending
            }
          >
            {t('apiKeys.create')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default CreateApiKeyModal;
