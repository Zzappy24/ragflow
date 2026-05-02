/**
 * CUSTOM B2B SaaS — RenderDocxTemplate tool form widgets.
 *
 * Three text inputs that bind to the canvas-design-time params of
 * agent/tools/render_docx_template.py:
 *   - template_file_id : workspace File id of the .docx template
 *   - output_folder_id : workspace Folder id where the rendered .docx goes
 *   - output_filename  : Jinja-templated filename pattern
 *
 * The LLM only ever provides the `content` JSON at runtime; the three
 * fields above are workspace-bound IDs the canvas author fills in.
 *
 * Marker: search for "RenderDocxTemplate" or "B2B SaaS" on upstream merges
 * to relocate this file if the form-widgets layout changes.
 */
import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { useTranslate } from '@/hooks/common-hooks';
import { useFormContext } from 'react-hook-form';
import { z } from 'zod';

export const RenderDocxTemplateFormPartialSchema = {
  template_file_id: z.string().min(1, 'Template file id is required'),
  output_folder_id: z.string().min(1, 'Output folder id is required'),
  output_filename: z.string().optional(),
};

export function RenderDocxTemplateWidgets() {
  const { t } = useTranslate('flow');
  const form = useFormContext();

  return (
    <>
      <FormField
        control={form.control}
        name="template_file_id"
        render={({ field }) => (
          <FormItem>
            <FormLabel
              tooltip={t(
                'renderDocxTemplate.templateFileIdTip',
                'ID of the .docx template file in this workspace.',
              )}
            >
              {t('renderDocxTemplate.templateFileId', 'Template file ID')}
            </FormLabel>
            <FormControl>
              <Input {...field} placeholder="e.g. edf9b6d4..." />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={form.control}
        name="output_folder_id"
        render={({ field }) => (
          <FormItem>
            <FormLabel
              tooltip={t(
                'renderDocxTemplate.outputFolderIdTip',
                'ID of the workspace folder where the rendered .docx is saved.',
              )}
            >
              {t('renderDocxTemplate.outputFolderId', 'Output folder ID')}
            </FormLabel>
            <FormControl>
              <Input {...field} placeholder="e.g. b2e3c71a..." />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={form.control}
        name="output_filename"
        render={({ field }) => (
          <FormItem>
            <FormLabel
              tooltip={t(
                'renderDocxTemplate.outputFilenameTip',
                "Jinja pattern. `slugify` filter and `ts` (UTC timestamp) are available. Default: {{procedure_name|default('document')|slugify}}-{{ts}}.docx",
              )}
            >
              {t(
                'renderDocxTemplate.outputFilename',
                'Output filename pattern',
              )}
            </FormLabel>
            <FormControl>
              <Input
                {...field}
                placeholder="{{procedure_name|slugify}}-{{ts}}.docx"
              />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
    </>
  );
}
