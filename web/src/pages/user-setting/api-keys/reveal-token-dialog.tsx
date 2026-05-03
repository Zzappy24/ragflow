import CopyToClipboard from '@/components/copy-to-clipboard';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { ApiKeyScope } from '@/services/api-key-service';
import { LucideTriangleAlert } from 'lucide-react';
import { useTranslation } from 'react-i18next';

type Props = {
  scope: ApiKeyScope | null;
  onClose: () => void;
};

const RevealTokenDialog = ({ scope, onClose }: Props) => {
  const { t } = useTranslation();
  const open = !!scope;

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <LucideTriangleAlert className="size-5 text-state-warning" />
            {t('apiKeys.revealTitle')}
          </DialogTitle>
          <DialogDescription>
            {t('apiKeys.revealDescription')}
          </DialogDescription>
        </DialogHeader>

        {scope?.token ? (
          <div className="bg-bg-card p-3 rounded-md flex items-center gap-2 my-2">
            <code className="flex-1 break-all text-sm">{scope.token}</code>
            <CopyToClipboard text={scope.token} />
          </div>
        ) : null}

        <p className="text-sm text-text-secondary">
          {t('apiKeys.revealUsageHint')}
        </p>

        <DialogFooter>
          <Button onClick={onClose}>{t('apiKeys.iSavedIt')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default RevealTokenDialog;
