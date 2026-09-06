// CUSTOM B2B SaaS — assainissement de l'arbre HTML APRÈS rehype-raw (audit
// 2026-09-06). DOMPurify sur la source markdown ne suffit pas : tout ce qui
// redevient du HTML plus tard (décodage d'entités dans le code ou les
// équations, restauration de segments) est ré-exécuté par rehype-raw dès
// qu'un bloc HTML (<div>…</div>) l'enveloppe. rehype-sanitize travaille sur
// le résultat du parsing brut : un <iframe srcdoc>, un <script>, un
// gestionnaire on*, une URL javascript: n'atteignent jamais le DOM, quel
// que soit le chemin qui les a produits.
//
// À placer juste après rehypeRaw et AVANT rehype-katex (sa sortie est sûre
// et ses classes math-* doivent survivre) et avant nos plugins maison.
import { defaultSchema } from 'rehype-sanitize';

const extraTags = [
  // sections repliables produites par replaceThinkToSection & co.
  'think',
  'section',
  'retrieving',
  'tool_call',
  'details',
  'summary',
  'mark',
  'u',
];

export const markdownSanitizeSchema = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), ...extraTags],
  attributes: {
    ...defaultSchema.attributes,
    // class : sections think/retrieving, math-inline/math-display de
    // remark-math, language-* des blocs de code. dir : sens du texte.
    '*': [...(defaultSchema.attributes?.['*'] ?? []), 'className', 'dir'],
    code: ['className'],
    a: [...(defaultSchema.attributes?.a ?? []), 'target', 'rel'],
    img: [...(defaultSchema.attributes?.img ?? []), 'width', 'height'],
  },
  protocols: {
    ...defaultSchema.protocols,
    // images inline en base64 (vignettes de chunks) ; jamais de script
    // possible dans un <img>.
    src: ['http', 'https', 'data'],
  },
};
