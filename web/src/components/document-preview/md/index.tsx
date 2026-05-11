import { Authorization } from '@/constants/authorization';
import { cn } from '@/lib/utils';
import FileError from '@/pages/document-viewer/file-error';
import { getAuthorization } from '@/utils/authorization-util';
import React, { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface MdProps {
  // filePath: string;
  className?: string;
  url: string;
}

export const Md: React.FC<MdProps> = ({ url, className }) => {
  const [content, setContent] = useState<string>('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    // Inject X-Workspace-Id like the axios interceptor does for every other
    // call — the backend `@require_permission(DOCUMENT_READ)` resolves the
    // tenant from this header, and without it returns
    // `Permission denied: document.read`.
    const activeWorkspaceId = localStorage.getItem('active_workspace_id');
    const headers: Record<string, string> = {
      [Authorization]: getAuthorization(),
    };
    if (activeWorkspaceId) {
      headers['X-Workspace-Id'] = activeWorkspaceId;
    }
    fetch(url, { headers })
      .then((res) => {
        if (!res.ok) throw new Error('Failed to fetch markdown file');
        return res.text();
      })
      .then((text) => setContent(text))
      .catch((err) => setError(err.message));
  }, [url]);

  if (error) return <FileError>{error}</FileError>;

  return (
    <div
      style={{ padding: 4, overflow: 'scroll' }}
      className={cn(className, 'markdown-body h-[calc(100vh - 200px)]')}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
};

export default Md;
