import { render, screen, waitFor } from '@testing-library/react';
import { WorkspaceGate } from '../workspace-gate';

const mockClear = jest.fn();
let mockUserInfo: any = {};

jest.mock('@/hooks/use-user-setting-request', () => ({
  useFetchUserInfo: () => ({ data: mockUserInfo, loading: false }),
}));
jest.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ clear: mockClear }),
}));

describe('WorkspaceGate', () => {
  beforeEach(() => {
    localStorage.clear();
    mockClear.mockReset();
    mockUserInfo = {};
  });

  it("n'affiche rien tant que la liste des workspaces n'est pas connue", () => {
    render(
      <WorkspaceGate>
        <div>page</div>
      </WorkspaceGate>,
    );
    expect(screen.queryByText('page')).toBeNull();
    expect(screen.getByTestId('workspace-gate')).toBeTruthy();
  });

  it('épingle le workspace préféré puis rend la page (cas navigation privée)', async () => {
    mockUserInfo = {
      active_workspace_id: 'ws2',
      workspaces: [{ id: 'ws1' }, { id: 'ws2' }],
    };
    render(
      <WorkspaceGate>
        <div>page</div>
      </WorkspaceGate>,
    );
    await waitFor(() => expect(screen.getByText('page')).toBeTruthy());
    expect(localStorage.getItem('active_workspace_id')).toBe('ws2');
    expect(mockClear).toHaveBeenCalledTimes(1);
  });

  it('épingle le premier workspace quand aucun préféré ne correspond', async () => {
    mockUserInfo = { workspaces: [{ id: 'ws1' }, { id: 'ws2' }] };
    render(
      <WorkspaceGate>
        <div>page</div>
      </WorkspaceGate>,
    );
    await waitFor(() => expect(screen.getByText('page')).toBeTruthy());
    expect(localStorage.getItem('active_workspace_id')).toBe('ws1');
  });

  it('rend immédiatement quand un workspace est déjà épinglé, sans vider le cache', () => {
    localStorage.setItem('active_workspace_id', 'ws1');
    render(
      <WorkspaceGate>
        <div>page</div>
      </WorkspaceGate>,
    );
    expect(screen.getByText('page')).toBeTruthy();
    expect(mockClear).not.toHaveBeenCalled();
  });

  it('laisse passer un compte sans aucun workspace', async () => {
    mockUserInfo = { workspaces: [] };
    render(
      <WorkspaceGate>
        <div>page</div>
      </WorkspaceGate>,
    );
    await waitFor(() => expect(screen.getByText('page')).toBeTruthy());
    expect(localStorage.getItem('active_workspace_id')).toBeNull();
  });
});
