import { isWorkspacePinned, safeListData } from '../list-fallback';

describe('safeListData', () => {
  const fallback = { chats: [], total: 0 };

  it('renvoie la donnée quand le code est 0 et la donnée un objet', () => {
    const data = { chats: [{ id: 'a' }], total: 1 };
    expect(safeListData({ code: 0, data }, fallback)).toBe(data);
  });

  it('remplace `data: false` (403 RBAC) par le repli — le bug prod du 2026-09-07', () => {
    expect(
      safeListData(
        {
          code: 403,
          data: false,
          message: 'Permission denied: chat.read',
        } as any,
        fallback,
      ),
    ).toBe(fallback);
  });

  it('remplace null, undefined et les réponses vides', () => {
    expect(safeListData(null, fallback)).toBe(fallback);
    expect(safeListData(undefined, fallback)).toBe(fallback);
    expect(safeListData({ code: 0, data: null }, fallback)).toBe(fallback);
  });

  it('accepte un validateur dédié (liste attendue)', () => {
    expect(
      safeListData({ code: 0, data: { not: 'a list' } }, [], Array.isArray),
    ).toEqual([]);
    expect(safeListData({ code: 0, data: [1] }, [], Array.isArray)).toEqual([
      1,
    ]);
  });
});

describe('isWorkspacePinned', () => {
  afterEach(() => localStorage.clear());

  it('faux sans workspace épinglé, vrai avec', () => {
    expect(isWorkspacePinned()).toBe(false);
    localStorage.setItem('active_workspace_id', 'ws1');
    expect(isWorkspacePinned()).toBe(true);
  });
});
