import { useEffect, useState } from 'react';
import { skillsApi } from '../skills-api';
import { REMOTE_CLIENT } from '../api-base';

export default function SkillsSettings() {
  const [data, setData] = useState({ installed: [] }), [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [name, setName] = useState(''), [instructions, setInstructions] = useState('');
  const [filename, setFilename] = useState('');
  const [query, setQuery] = useState('');
  useEffect(() => { let active = true; skillsApi.list().then((value) => { if (active) setData(value); }).catch((e) => { if (active) setError(e.message); }); return () => { active = false; }; }, []);
  const perform = async (action) => { setBusy(true); setError(''); try { const value = await action(); setData(await skillsApi.list()); setNotice(`${value.name} 已安装。到“项目 Agent → 成员与分工”勾选它，保存后同步到当前群。`); } catch (e) { setError(e.message); } finally { setBusy(false); } };
  const readFile = async (event) => {
    const file = event.currentTarget.files[0];
    if (!file) return;
    if (file.size > 150000) { setError('文件过大，请保留核心指令。'); return; }
    setBusy(true);
    setError('');
    try {
      setInstructions(await file.text());
      setFilename(file.name);
    } catch {
      setError('无法读取文件，请重新选择 SKILL.md。');
    } finally {
      setBusy(false);
    }
  };
  const installed = Array.isArray(data.installed) ? data.installed : [];
  const lemoInstalled = installed.some((s) => s.id === 'lemo-opuscar');
  const pstackInstalled = installed.some((s) => (s.id || '').startsWith('pstack-'));
  const normalizedQuery = query.trim().toLowerCase();
  const filtered = normalizedQuery
    ? installed.filter((skill) => [skill.name, skill.id, skill.description, skill.execution]
        .some((field) => String(field || '').toLowerCase().includes(normalizedQuery)))
    : installed;
  return <section><div className="agent-section-heading"><div><h2>Skills 能力库</h2><p>安装技能 → 绑定角色 → 项目群自动协作。每次任务保留所用技能版本；流程模板暂不支持 Skills。</p></div></div>{error && <p role="alert" className="error-banner">{error}</p>}{notice && <p role="status" className="agent-notice">{notice}</p>}
    <div className="skills-bundle-grid">
      <article className="connection-action-box">
        <div className="skill-card-header"><h3>Lemo-Opuscar 动画导演</h3>{lemoInstalled && <span className="skill-status-badge">已安装</span>}</div>
        <p>为代码 Agent 提供动画导演与视觉风格指令。绑定到 Codex 成员后，由它编写动画画面，平台渲染并收回实际 MP4。支持 1–120 秒 Canvas 动画，可使用上游绘图角色的图片。旁白、字幕和时长仍按原需求验收。</p>
        <p role="status" className="skill-env-status">{data.renderer_ready === undefined ? '正在检查动画运行环境…' : data.renderer_ready ? '动画运行环境已就绪' : '动画运行环境未就绪：需要 FFmpeg 和 Playwright Chromium，请参阅项目 README 的 Skills 配置。'}</p>
        <div className="skills-bundle-footer">
          <button type="button" className={lemoInstalled ? 'subtle-button' : 'primary-button'} disabled={busy || REMOTE_CLIENT} onClick={() => perform(skillsApi.installLemo)}>{busy ? '正在处理…' : lemoInstalled ? '重新安装固定版本' : '安装 Lemo-Opuscar'}</button>
          <a className="skills-doc-link" href="https://github.com/lemomo-ai/lemo-opuscar" target="_blank" rel="noreferrer">查看项目与 MIT 许可</a>
        </div>
      </article>
      <article className="connection-action-box">
        <div className="skill-card-header"><h3>pstack 方法论精选</h3>{pstackInstalled && <span className="skill-status-badge">已安装</span>}</div>
        <p>从 <a className="skills-inline-link" href="https://github.com/cursor/plugins" target="_blank" rel="noreferrer">cursor/plugins (pstack)</a> 精选的 8 个方法论技能：TDD 缺陷修复、改动影响面分析、需求追问、去 AI 味写作、技术写作、先弄清再动手、修根因不是修症状、先证明能跑通。安装固定版本后，可在「项目 Agent → 成员与分工」勾选「成品写作复核」，审稿人会引用正文并说明修改意见。其他技能可按角色选用。</p>
        <div className="skills-bundle-footer">
          <button type="button" className={pstackInstalled ? 'subtle-button' : 'primary-button'} disabled={busy || REMOTE_CLIENT} onClick={() => perform(skillsApi.installPstack)}>{busy ? '正在处理…' : pstackInstalled ? '重新安装固定版本' : '安装 pstack 方法论'}</button>
        </div>
      </article>
    </div>
    <p className="connection-help">LLM、Codex、Claude Code 和 Muse 均可按角色配置 Skills。Muse 接收创作指引，长技能由本群统筹模型整理；不等同于在 Muse 安装插件。任务卡可查看实际传递的指引和技能版本。代码渲染仅由绑定 Lemo 的 Codex 成员执行，其他 Agent 的外部依赖需单独接入。</p>
    <section className="skills-installed-section">
      <div className="skills-installed-header">
        <div className="skills-installed-title">
          <h3>已安装技能（{installed.length}）</h3>
          {normalizedQuery && filtered.length > 0 && <span className="skills-filter-count">匹配 {filtered.length} 项</span>}
        </div>
        {installed.length > 3 && (
          <search className="skills-search-wrap">
            <input
              type="search"
              className="skills-search-input"
              aria-label="筛选已安装技能"
              placeholder="按名称或说明筛选技能…"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </search>
        )}
      </div>
      {filtered.length > 0 && (
        <div className="skills-installed-grid">
          {filtered.map((skill) => (
            <article className="skill-installed-card" key={skill.id}>
              <div className="skill-installed-card-head">
                <h4>{skill.name}</h4>
              </div>
              {skill.description && <p className="skill-installed-desc">{skill.description}</p>}
              {skill.execution && <p className="skill-installed-exec">{skill.execution}</p>}
              <footer className="skill-installed-meta">
                <small>版本 {skill.revision?.slice(0, 12) || skill.sha256?.slice(0, 12) || '—'} · {typeof skill.characters === 'number' ? skill.characters.toLocaleString() : '0'} 字符</small>
              </footer>
            </article>
          ))}
        </div>
      )}
      {installed.length > 0 && filtered.length === 0 && (
        <div className="skills-empty-search">
          <p>没有匹配“{query.trim()}”的已安装技能。</p>
          <button type="button" className="subtle-button" onClick={() => setQuery('')}>清空筛选</button>
        </div>
      )}
      {installed.length === 0 && (
        <p className="skills-empty-note">暂未安装技能，可从上方精选合集一键安装，或在下方导入自定义 SKILL.md。</p>
      )}
    </section>
    <details className="connection-action-box skills-import-disclosure">
      <summary>导入自己的 SKILL.md</summary>
      <p>适合写作、审查和任务流程指令。需要脚本、外部服务或额外文件的技能，需先接入相应执行工具；导入不会自动执行安装脚本。</p>
      <label>技能名称（可选）<input value={name} maxLength={80} placeholder="留空时使用文档名称或文件名" onChange={(e) => setName(e.target.value)} /></label>
      <label>读取 SKILL.md<input type="file" accept=".md,text/markdown,text/plain" disabled={busy || REMOTE_CLIENT} onChange={readFile} /></label>
      <label>技能指令<textarea value={instructions} maxLength={50000} rows={8} onChange={(e) => setInstructions(e.target.value)} /></label>
      <button type="button" className="primary-button" disabled={busy || REMOTE_CLIENT || !instructions.trim()} onClick={() => perform(() => skillsApi.import({ name, filename, instructions }))}>导入技能</button>
    </details>
    <p className="connection-help">{data.policy}</p>
  </section>;
}
