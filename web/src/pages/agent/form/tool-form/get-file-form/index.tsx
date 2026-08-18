/**
 * Tool-form wrapper for GetFile.
 *
 * Mirrors wikipedia-form / render-docx-template-form: the actual widgets +
 * schema live in ../../get-file-form/ so they stay reusable. Registered in
 * ../constant.tsx (ToolFormConfigMap).
 */
import { FormContainer } from '@/components/form-container';
import { Form } from '@/components/ui/form';
import { zodResolver } from '@hookform/resolvers/zod';
import { memo } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { FormWrapper } from '../../components/form-wrapper';
import { GetFileFormPartialSchema, GetFileWidgets } from '../../get-file-form';
import { useValues } from '../use-values';
import { useWatchFormChange } from '../use-watch-change';

function GetFileForm() {
  const values = useValues();
  const FormSchema = z.object(GetFileFormPartialSchema);
  const form = useForm<z.infer<typeof FormSchema>>({
    defaultValues: values,
    resolver: zodResolver(FormSchema),
  });

  useWatchFormChange(form);

  return (
    <Form {...form}>
      <FormWrapper>
        <FormContainer>
          <GetFileWidgets />
        </FormContainer>
      </FormWrapper>
    </Form>
  );
}

export default memo(GetFileForm);
