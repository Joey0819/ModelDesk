import { memberKind, memberKindLabel } from '../mission-teams';
import './RoleContractFields.css';

const capabilityHelp = {
  model: '推理、写作与审阅；工具取决于本轮已启用的能力。',
  image: '通过所选生图接口交付真实图片，不能代替文字统筹。',
  codex: '在隔离项目副本中修改代码、运行命令并返回补丁。',
  claude: '通过已登录的 Claude Code 执行代码任务并返回记录。',
  muse: '通过本机 Muse 创作并回收真实成果，需要聊天窗口与桥接可用。',
  mcp: '使用绑定的 MCP 工具及其真实参数，回传文本或结构化结果。',
};

export function TeamRoleMap({ members = [] }) {
  return <section className="team-role-map" aria-label="角色协作关系">
    <strong>角色与交接</strong>
    <p>角色定义职责；LLM 或 Agent 提供执行能力。统筹按这些规则分配工作。</p>
    <ol>{members.map((member) => <li key={member.id}>
      <div><strong>{member.name || '未命名角色'}</strong><span>{memberKindLabel(member)}</span>{member.required && <small>必须参与</small>}</div>
      <p>{member.after_member_ids?.length ? `接收：${member.after_member_ids.map((id) => members.find((item) => item.id === id)?.name || '已移除角色').join('、')}` : '由统筹安排时机'}</p>
      {member.expected_output && <p className="team-role-output">交付：{member.expected_output}</p>}
    </li>)}</ol>
  </section>;
}

export default function RoleContractFields({ member, members, index, onChange }) {
  const predecessors = member.after_member_ids || [];
  return <details className="role-contract-fields">
    <summary>自动协作约定{member.required ? ' · 必须参与' : ''}{predecessors.length ? ` · ${predecessors.length} 个前置角色` : ''}</summary>
    <p>{capabilityHelp[memberKind(member)]}</p>
    <label className="role-contract-check"><input type="checkbox" aria-label={`成员 ${index + 1} 必须参与`} checked={Boolean(member.required)} onChange={(event) => onChange({ required: event.target.checked })} />本轮选中此角色时，必须安排实际工作</label>
    <label className="role-contract-output"><span>应交付什么</span><textarea aria-label={`成员 ${index + 1} 交付约定`} rows={2} maxLength={500} placeholder="例如：可运行的代码、测试记录和待解决问题；或完整正文与来源" value={member.expected_output || ''} onChange={(event) => onChange({ expected_output: event.target.value })} /></label>
    <fieldset><legend>先接收哪些角色的成果</legend>{members.filter((item) => item.id !== member.id).map((item) => <label className="role-contract-check" key={item.id}><input type="checkbox" aria-label={`成员 ${index + 1} 等待 ${item.name}`} checked={predecessors.includes(item.id)} onChange={(event) => onChange({ after_member_ids: event.target.checked ? [...predecessors, item.id] : predecessors.filter((id) => id !== item.id) })} />{item.name || '未命名角色'}</label>)}</fieldset>
    <small>用于项目群“自动完成”。前置工作成功后才会开始，并传入真实成果；点名时需同时选中前置角色。</small>
  </details>;
}
