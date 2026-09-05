/**
 * Pin du repli libellés → icônes de la nav (zoom > 125 %, petits écrans).
 * ResizeObserver et largeurs sont simulés : on vérifie la logique d'état,
 * pas le rendu pixel.
 */
jest.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'fr' },
  }),
}));
jest.mock('react-router', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const R = require('react');
  return {
    Link: ({ to, children, ...props }: any) =>
      R.createElement('a', { href: to, ...props }, children),
    useLocation: () => ({ pathname: '/' }),
  };
});
jest.mock('@/utils/css-support', () => ({ supportsCssAnchor: false }));
jest.mock('@/routes', () => ({
  Routes: {
    Root: '/',
    Datasets: '/datasets',
    DatasetBase: '/dataset',
    Chats: '/chats',
    Chat: '/chat',
    Searches: '/searches',
    Search: '/search',
    Agents: '/agents',
    AgentTemplates: '/agent-templates',
    Memories: '/memories',
    Memory: '/memory',
    MemoryMessage: '/memory-message',
    Files: '/files',
  },
}));

import { act, render } from '@testing-library/react';
import * as React from 'react';

// esbuild-jest transforme le JSX en React.createElement sans injecter
// l'import : on expose React en global et on charge le composant après.
(global as any).React = React;
// eslint-disable-next-line @typescript-eslint/no-require-imports
const GlobalNavbar = require('./global-navbar').default;
const renderNavbar = () => render(React.createElement(GlobalNavbar));

const layout = { containerW: 1000, navW: 600 };
const roCallbacks: Array<() => void> = [];

beforeAll(() => {
  const widthOf = function (this: HTMLElement) {
    return this.tagName === 'NAV' ? layout.navW : layout.containerW;
  };
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
    configurable: true,
    get: widthOf,
  });
  Object.defineProperty(HTMLElement.prototype, 'scrollWidth', {
    configurable: true,
    get: widthOf,
  });
  (global as any).ResizeObserver = class {
    cb: () => void;
    constructor(cb: () => void) {
      this.cb = cb;
      roCallbacks.push(cb);
    }
    observe() {}
    disconnect() {
      // comme un vrai observateur : plus de callback après disconnect
      const i = roCallbacks.indexOf(this.cb);
      if (i >= 0) roCallbacks.splice(i, 1);
    }
  };
});

const fireResize = () => act(() => roCallbacks.forEach((cb) => cb()));
const counts = (el: HTMLElement) => ({
  labels: el.querySelectorAll('nav a span').length,
  icons: el.querySelectorAll('nav a svg').length,
});

test('libellés quand ça tient, icônes quand ça déborde, retour quand la place revient', () => {
  layout.containerW = 1000;
  layout.navW = 600;
  const { container } = renderNavbar();
  // 7 entrées : Accueil toujours en icône, 6 libellés
  expect(counts(container)).toEqual({ labels: 6, icons: 1 });

  // Le conteneur rétrécit sous la largeur pleine → tout en icônes
  layout.containerW = 300;
  fireResize();
  expect(counts(container)).toEqual({ labels: 0, icons: 7 });

  // Replié, la nav est plus étroite ; un léger gain de place ne suffit pas
  layout.navW = 320;
  layout.containerW = 604;
  fireResize();
  expect(counts(container)).toEqual({ labels: 0, icons: 7 });

  // La place pleine revient (hystérésis 8 px) → libellés rétablis
  layout.containerW = 608;
  fireResize();
  layout.navW = 600;
  expect(counts(container)).toEqual({ labels: 6, icons: 1 });
});

test('chaque entrée garde son libellé en title/aria-label, même repliée', () => {
  layout.containerW = 200;
  layout.navW = 600;
  const { container } = renderNavbar();
  expect(counts(container).icons).toBe(7);
  const links = Array.from(container.querySelectorAll('nav a'));
  expect(
    links.every((a) => a.getAttribute('aria-label') && a.getAttribute('title')),
  ).toBe(true);
});
