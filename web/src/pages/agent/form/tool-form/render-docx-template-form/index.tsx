/**
 * CUSTOM B2B SaaS — tool-form wrapper for RenderDocxTemplate.
 *
 * Mirrors wikipedia-form / duckduckgo-form pattern. The actual widgets +
 * schema live in ../../render-docx-template-form/ so they can be reused
 * if RAGFlow upstream ever adds canvas-node usage of this tool.
 *
 * Registered in ../constant.tsx (ToolFormConfigMap).
 */
import { FormContainer } from '@/components/form-container';
import { Form } from '@/components/ui/form';
import { zodResolver } from '@hookform/resolvers/zod';
import { memo } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { FormWrapper } from '../../components/form-wrapper';
import {
  RenderDocxTemplateFormPartialSchema,
  RenderDocxTemplateWidgets,
} from '../../render-docx-template-form';
import { useValues } from '../use-values';
import { useWatchFormChange } from '../use-watch-change';

function RenderDocxTemplateForm() {
  const values = useValues();
  const FormSchema = z.object(RenderDocxTemplateFormPartialSchema);
  const form = useForm<z.infer<typeof FormSchema>>({
    defaultValues: values,
    resolver: zodResolver(FormSchema),
  });

  useWatchFormChange(form);

  return (
    <Form {...form}>
      <FormWrapper>
        <FormContainer>
          <RenderDocxTemplateWidgets />
        </FormContainer>
      </FormWrapper>
    </Form>
  );
}

export default memo(RenderDocxTemplateForm);
