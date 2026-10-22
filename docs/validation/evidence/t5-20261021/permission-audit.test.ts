import { beforeEach, describe, expect, it, vi } from 'vitest';

const fixture = vi.hoisted(() => ({
  guard: undefined as ((to: any, from: any) => Promise<any>) | undefined,
  after: undefined as (() => void) | undefined,
  authenticated: false,
  handled: false,
  relogin: { show: false },
  roles: [] as string[],
  getInfo: vi.fn(),
  logout: vi.fn(),
  generateRoutes: vi.fn(),
  addRoute: vi.fn(),
  setTitle: vi.fn(),
  error: vi.fn(),
  progress: { configure: vi.fn(), start: vi.fn(), done: vi.fn() }
}));

vi.mock('./router', () => ({ default: {
  beforeEach: (handler: typeof fixture.guard) => { fixture.guard = handler; },
  afterEach: (handler: typeof fixture.after) => { fixture.after = handler; },
  addRoute: fixture.addRoute
} }));
vi.mock('nprogress', () => ({ default: fixture.progress }));
vi.mock('element-plus/es', () => ({ ElMessage: { error: fixture.error } }));
vi.mock('@/store/modules/settings', () => ({ useSettingsStore: () => ({ setTitle: fixture.setTitle }) }));
vi.mock('@/store/modules/user', () => ({ useUserStore: () => ({
  roles: fixture.roles, getInfo: fixture.getInfo, logout: fixture.logout
}) }));
vi.mock('@/store/modules/permission', () => ({ usePermissionStore: () => ({ generateRoutes: fixture.generateRoutes }) }));
vi.mock('@/utils/auth', () => ({ getToken: () => fixture.authenticated ? 'session-present' : '' }));
vi.mock('@/utils/request', () => ({ isRelogin: fixture.relogin, isHandledRequestError: () => fixture.handled }));

await import('./permission');

function target(path = '/biz/purchaseOrder') {
  return {
    path, fullPath: `${path}?filter=draft`, meta: {},
    params: { id: 'fixture-order' }, query: { filter: 'draft' },
    hash: '#detail', name: Symbol('fixture-route')
  };
}

function navigate(to = target()) {
  if (!fixture.guard) throw new Error('Route guard was not registered');
  return fixture.guard(to, {});
}

beforeEach(() => {
  vi.clearAllMocks();
  fixture.authenticated = false;
  fixture.handled = false;
  fixture.roles = [];
  fixture.relogin.show = false;
  fixture.getInfo.mockResolvedValue(undefined);
  fixture.logout.mockResolvedValue(undefined);
  fixture.generateRoutes.mockResolvedValue([{ path: '/biz/fixture' }, { path: 'https://example.test' }]);
});

describe('permission guard preserved behavior', () => {
  it.each(['/login', '/register', '/social-callback', '/register/tenant'])('allows anonymous whitelist %s', async (path) => {
    expect(await navigate(target(path))).toBe(true);
    expect(fixture.getInfo).not.toHaveBeenCalled();
    expect(fixture.generateRoutes).not.toHaveBeenCalled();
  });

  it('preserves the complete destination in an anonymous redirect', async () => {
    const to = target();
    const result = await navigate(to);
    const redirect = new URL(result, 'http://fixture.test');
    expect(redirect.pathname).toBe('/login');
    expect(redirect.searchParams.get('redirect')).toBe(to.fullPath);
    expect(fixture.progress.done).toHaveBeenCalled();
  });

  it('redirects an authenticated login visit to the app root', async () => {
    fixture.authenticated = true;
    expect(await navigate(target('/login'))).toEqual({ path: '/' });
    expect(fixture.getInfo).not.toHaveBeenCalled();
  });

  it('allows authenticated whitelist visits before loading roles', async () => {
    fixture.authenticated = true;
    expect(await navigate(target('/social-callback'))).toBe(true);
    expect(fixture.getInfo).not.toHaveBeenCalled();
  });

  it('keeps existing roles without fetching user info or regenerating routes', async () => {
    fixture.authenticated = true;
    fixture.roles = ['fixture-role'];
    expect(await navigate()).toBe(true);
    expect(fixture.getInfo).not.toHaveBeenCalled();
    expect(fixture.generateRoutes).not.toHaveBeenCalled();
  });

  it('initializes local routes and preserves every replacement destination field', async () => {
    fixture.authenticated = true;
    const to = target();
    expect(await navigate(to)).toEqual({
      path: to.path, replace: true, params: to.params,
      query: to.query, hash: to.hash, name: to.name
    });
    expect(fixture.getInfo).toHaveBeenCalledOnce();
    expect(fixture.generateRoutes).toHaveBeenCalledOnce();
    expect(fixture.addRoute.mock.calls).toEqual([[{ path: '/biz/fixture' }]]);
    expect(fixture.relogin.show).toBe(false);
  });

  it('logs out and redirects when user info fails', async () => {
    fixture.authenticated = true;
    fixture.getInfo.mockRejectedValue(new Error('fixture info unavailable'));
    expect(await navigate()).toEqual({ path: '/' });
    expect(fixture.logout).toHaveBeenCalledOnce();
    expect(fixture.error).toHaveBeenCalledWith('fixture info unavailable');
    expect(fixture.generateRoutes).not.toHaveBeenCalled();
  });

  it('does not duplicate a handled user info error notification', async () => {
    fixture.authenticated = true;
    fixture.handled = true;
    fixture.getInfo.mockRejectedValue(new Error('fixture handled failure'));
    expect(await navigate()).toEqual({ path: '/' });
    expect(fixture.logout).toHaveBeenCalledOnce();
    expect(fixture.error).not.toHaveBeenCalled();
  });

  it('preserves route generation failures and the relogin transition', async () => {
    fixture.authenticated = true;
    fixture.generateRoutes.mockRejectedValue(new Error('fixture routes unavailable'));
    await expect(navigate()).rejects.toThrow('fixture routes unavailable');
    expect(fixture.relogin.show).toBe(false);
    expect(fixture.logout).not.toHaveBeenCalled();
  });

  it('finishes progress after navigation', () => {
    fixture.after?.();
    expect(fixture.progress.done).toHaveBeenCalledOnce();
  });
});
