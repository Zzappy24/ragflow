import {
  ChatVariableEnabledField,
  EmptyConversationId,
} from '@/constants/chat';
import { IMessage, Message } from '@/interfaces/database/chat';
import { omit } from 'lodash';
import { v4 as uuid } from 'uuid';
import {
  citationMarkerReg,
  normalizeCitationDigits,
  parseCitationIndex,
} from './citation-utils';

export const isConversationIdExist = (conversationId: string) => {
  return conversationId !== EmptyConversationId && conversationId !== '';
};

export const buildMessageUuid = (message: Partial<Message | IMessage>) => {
  if ('id' in message && message.id) {
    return message.id;
  }
  return uuid();
};

export const buildMessageListWithUuid = (messages?: Message[]) => {
  return (
    messages?.map((x: Message | IMessage) => ({
      ...omit(x, 'reference'),
      id: buildMessageUuid(x),
    })) ?? []
  );
};

export const generateConversationId = () => {
  return uuid().replace(/-/g, '');
};

// When rendering each message, add a prefix to the id to ensure uniqueness.
export const buildMessageUuidWithRole = (
  message: Partial<Message | IMessage>,
) => {
  return `${message.role}_${message.id}`;
};

// Preprocess LaTeX equations to be rendered by KaTeX
// ref: https://github.com/remarkjs/react-markdown/issues/785
//
// Delimiter matching: we only treat \] and \) as block/inline endings when they
// are not part of a LaTeX command (e.g. \right], \big), \left)). Use a negative
// lookbehind (?<![a-zA-Z]) so that \] or \) preceded by a letter (command name)
// is not considered the closing delimiter. Use greedy matching so we match up to
// the last valid delimiter and avoid cutting at the first \] or \) inside the
// equation (e.g. \frac{1}{|y|} or \right]).

const BLOCK_MATH_RE = /\\\[([\s\S]*?)(?<![a-zA-Z])\\\]/g;
const INLINE_MATH_RE = /\\\(([\s\S]*?)(?<![a-zA-Z])\\\)/g;

export const preprocessLaTeX = (content: string) => {
  const normalizedContent = content
    .replace(/\\\\\[/g, '\\[')
    .replace(/\\\\\(/g, '\\(')
    .replace(/\\\\\]/g, '\\]')
    .replace(/\\\\\)/g, '\\)')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');

  const blockProcessedContent = normalizedContent.replace(
    BLOCK_MATH_RE,
    (_, equation) => `$$${equation}$$`,
  );

  const inlineProcessedContent = blockProcessedContent.replace(
    INLINE_MATH_RE,
    (_, equation) => `$${equation}$`,
  );

  return inlineProcessedContent;
};

export function replaceThinkToSection(text: string = '') {
  // Handle closed <think>...</think> tags
  let result = text.replace(
    /<think>([\s\S]*?)<\/think>/g,
    '<details class="think"><summary>Thinking...</summary>$1</details>',
  );
  // Handle unclosed <think> tags (streaming in progress)
  result = result.replace(
    /<think>([\s\S]*)$/,
    '<details class="think" open><summary>Thinking...</summary>$1</details>',
  );
  return result;
}

export function replaceRetrievingToSection(text: string = '') {
  const pattern = /<retrieving>([\s\S]*?)<\/retrieving>/g;

  const result = text.replace(
    pattern,
    '<details class="retrieving"><summary>Retrieving...</summary>$1</details>',
  );

  return result;
}

// CUSTOM B2B SaaS — rendre visible l'activité des outils de l'agent.
// Le backend émet chaque appel d'outil en bloc <tool_call>{json}</tool_call>
// (nom, arguments, résultat). Sans transformation, le rendu markdown les
// avale et l'utilisateur ne voit RIEN pendant que l'agent travaille
// (incident FAMAT 2026-09-05). On les rend en section repliable, comme
// <think> et <retrieving>, avec le nom de l'outil en résumé et le détail
// (arguments + résultat) en JSON à l'intérieur.
function _toolCallDetails(inner: string, open: boolean): string {
  let name = 'outil';
  try {
    const parsed = JSON.parse(inner);
    if (parsed && typeof parsed.name === 'string') name = parsed.name;
  } catch {
    const m = inner.match(/"name"\s*:\s*"([^"]+)"/);
    if (m) name = m[1];
  }
  const body = '\n\n```json\n' + inner.trim() + '\n```\n';
  return `<details class="tool-call"${open ? ' open' : ''}><summary>\u{1F527} ${name}</summary>${body}</details>`;
}

export function replaceToolCallToSection(text: string = '') {
  // Blocs complets
  let result = text.replace(/<tool_call>([\s\S]*?)<\/tool_call>/g, (_, inner) =>
    _toolCallDetails(inner, false),
  );
  // Bloc en cours de streaming (balise non encore fermée)
  result = result.replace(/<tool_call>([\s\S]*)$/, (_, inner) =>
    _toolCallDetails(inner, true),
  );
  return result;
}

export function setInitialChatVariableEnabledFieldValue(
  field: ChatVariableEnabledField,
) {
  return field !== ChatVariableEnabledField.MaxTokensEnabled;
}

const ShowImageFields = ['image', 'table'];

export function showImage(filed?: string) {
  return ShowImageFields.some((x) => x === filed);
}

export function setChatVariableEnabledFieldValuePage() {
  const variableCheckBoxFieldMap = Object.values(
    ChatVariableEnabledField,
  ).reduce<Record<string, boolean>>((pre, cur) => {
    pre[cur] = cur !== ChatVariableEnabledField.MaxTokensEnabled;
    return pre;
  }, {});

  return variableCheckBoxFieldMap;
}

const oldReg = /(#{2}[0-9\u0660-\u0669\u06F0-\u06F9]+\${2})/g;
export const currentReg = citationMarkerReg;
export { normalizeCitationDigits, parseCitationIndex };

// To be compatible with the old index matching mode
export const replaceTextByOldReg = (text: string) => {
  return text?.replace(oldReg, (substring: string) => {
    return `[ID:${substring.slice(2, -2)}]`;
  });
};
