/**
 * GetFile tool form widgets.
 *
 * The tool has exactly one user-facing setting: how long the presigned
 * download URL stays valid. The file `name` itself is not configured here —
 * the LLM supplies it at call time (see agent/tools/get_file.py meta.parameters).
 *
 * Mirrors the render-docx-template-form / message-history-window-size-item
 * pattern: a single NumberInput bound through react-hook-form.
 */
import NumberInput from '@/components/originui/number-input';
import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { useTranslate } from '@/hooks/common-hooks';
import { useFormContext } from 'react-hook-form';
import { z } from 'zod';

export const GetFileUrlExpiresMin = 60;
export const GetFileUrlExpiresMax = 3600;

export const GetFileFormPartialSchema = {
  url_expires_s: z.number().min(GetFileUrlExpiresMin).max(GetFileUrlExpiresMax),
};

export function GetFileWidgets() {
  const { t } = useTranslate('flow');
  const form = useFormContext();

  return (
    <FormField
      control={form.control}
      name="url_expires_s"
      render={({ field }) => (
        <FormItem>
          <FormLabel tooltip={t('getFileUrlExpiresSTip')}>
            {t('getFileUrlExpiresS')}
          </FormLabel>
          <FormControl>
            <NumberInput
              {...field}
              min={GetFileUrlExpiresMin}
              max={GetFileUrlExpiresMax}
              className="w-full"
            ></NumberInput>
          </FormControl>
          <FormMessage />
        </FormItem>
      )}
    />
  );
}
