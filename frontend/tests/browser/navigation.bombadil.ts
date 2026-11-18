import { always } from '@antithesishq/bombadil';
import { actions, extract, getFingerprint, registerCustomAction } from '@antithesishq/bombadil/browser';
export { noUncaughtExceptions } from '@antithesishq/bombadil/browser/defaults/properties';

const selectFixtureProject = registerCustomAction('selectFixtureProject', async (document, window) => {
  if (window.location.hostname !== '127.0.0.1' || !document.querySelector('meta[name="modeldesk-navigation-fixture"]')) return;
  const select = document.querySelector<HTMLSelectElement>('.project-hub-bar select');
  if (!select?.querySelector('option[value="fixture-project"]')) return;
  select.value = 'fixture-project';
  select.dispatchEvent(new Event('change', { bubbles: true }));
});

const navigation = extract(({ document, window }) => {
  if (window.location.hostname !== '127.0.0.1' || !document.querySelector('meta[name="modeldesk-navigation-fixture"]')) {
    return null;
  }
  const root = document.querySelector('.workspace-destinations');
  if (!root) return null;
  const main = document.querySelector('.workspace-shell');
  const modeNav = document.querySelector('.mode-navigation');
  const resourceNav = document.querySelector('.platform-navigation');
  const page = main?.firstElementChild;
  const current = '[aria-current="page"]';
  const label = (el: Element | undefined) => el?.querySelector('strong')?.textContent ?? el?.textContent;
  const destinations = root.querySelectorAll(current);
  const expected = page?.matches('.automatic-workspace') ? '协作任务'
    : page?.matches('.group-workspace') ? '项目群聊'
    : page?.matches('.agent-settings-workspace') ? 'Agent 与连接'
    : page?.matches('.todo-board') ? '任务看板'
    : page?.matches('.project-workspace') ? '项目与资料'
    : page?.matches('.platform-workspace') ? '项目资源' : '单项协作';
  const modeLabels: Record<string, string> = { 聊天: '多模型聊天', 评审: '匿名评审', 团队: '任务协作' };
  const selectedMode = modeNav?.querySelectorAll(current) ?? [];
  const selectedResource = resourceNav?.querySelectorAll(current) ?? [];
  const modeLabel = label(selectedMode[0]) ?? '';
  const modeTitle = main?.querySelector('.header-title > div > span')?.textContent ?? '';

  const projectDialog = document.querySelector('.project-assets-manager');
  const drawer = document.querySelector('.sidebar');
  const mobile = window.matchMedia('(max-width: 820px)').matches;
  const drawerOpen = drawer?.classList.contains('sidebar-open');
  const projectPicker = document.querySelector<HTMLSelectElement>('.project-hub-bar select');
  const allowed = projectDialog ? [...projectDialog.querySelectorAll('header button')]
    .filter((button) => button.textContent === '代码审查' || button.getAttribute('aria-label') === '关闭项目资料')
    : mobile && !drawerOpen ? [...document.querySelectorAll('.workspace-shell .mobile-menu')]
    : [...document.querySelectorAll('.workspace-destinations button, .sidebar-primary-navigation button, .sidebar-close-btn')];
  const clicks = allowed.flatMap((target) => {
    const rect = target.getBoundingClientRect();
    const point = { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    if (!rect.width || !rect.height || !target.contains(document.elementFromPoint(point.x, point.y))) return [];
    return [{ Click: { fingerprint: getFingerprint(target), point } }];
  });
  return {
    clicks,
    fixtureProjectAvailable: !projectDialog && !drawerOpen && projectPicker?.value === ''
      && Boolean(projectPicker.querySelector('option[value="fixture-project"]')),
    correctDrawer: !mobile || (drawerOpen
      ? drawer?.contains(document.activeElement) && Boolean(document.querySelector('.modeldesk-main[inert]'))
      : !drawer?.contains(document.activeElement)),
    correctDestination: destinations.length === 1 && label(destinations[0]) === expected,
    correctSubnavigation: Boolean(modeNav) === (expected === '单项协作') && Boolean(resourceNav) === (expected === '项目资源'),
    correctMode: !modeNav || (selectedMode.length === 1 && Boolean(modeLabel) && modeTitle.includes(modeLabels[modeLabel] ?? modeLabel)),
    correctResource: !resourceNav || (selectedResource.length === 1 && label(selectedResource[0]) === main?.querySelector('h1')?.textContent),
  };
});

export const useNavigation = actions(() => {
  const state = navigation.current;
  // The first paint can be the pairing screen while the fixture session is read.
  if (!state) return ["Wait"];
  return state.fixtureProjectAvailable ? [...state.clicks, selectFixtureProject()] : state.clicks;
});
export const oneCurrentDestination = always(() => navigation.current?.correctDestination ?? true);
export const subnavigationMatchesWorkspace = always(() => navigation.current?.correctSubnavigation ?? true);
export const currentModeMatchesHeader = always(() => navigation.current?.correctMode ?? true);
export const currentResourceMatchesHeader = always(() => navigation.current?.correctResource ?? true);
export const drawerFocusMatchesVisibility = always(() => navigation.current?.correctDrawer ?? true);
