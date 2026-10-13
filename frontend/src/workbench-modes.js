export const MODES = {
  chat: { label: '多模型聊天', source: 'ChatHub', description: '同一个问题，同时听取不同模型的回答。', example: '比较两种科研论文阅读方法：先读摘要与结论，或先读方法与结果。给出各自适用场景。', action: '同时发送', icon: 'chat' },
  council: { label: '匿名评审', source: 'LLM Council', description: '独立回答、匿名互评，再由主席综合结论。', example: '我有 30 分钟阅读一篇科研论文。请设计阅读与核查流程，分配时间，并说明可能遗漏的证据。', action: '开始评审', icon: 'council' },
  camel: { label: '角色对话', source: 'CAMEL', description: '研究员与审稿人轮流讨论，逐步完善方案。', example: '讨论如何评估多动物跟踪中的身份连续性，区分技术诊断与可靠的实验验证。', action: '开始对话', icon: 'dialogue' },
  crewai: { label: '任务协作', source: 'CrewAI', description: '先起草，再审查，按分工完成一项任务。', example: '制定一份科研论文阅读与核查清单，覆盖方法、数据、统计与结论的证据边界。', action: '开始协作', icon: 'tasks' },
  group: { label: '模型群聊', source: 'MultiBotChat 式协作', description: '多个模型依次接话，共享一段讨论记录。', example: '讨论如何制定一份可执行的科研软件发布检查清单。先提出方案，再质询与完善。', action: '开始群聊', icon: 'group' },
  camel_workforce: { label: 'Workforce 团队', source: 'CAMEL 原生工作队', description: '由原生工作队规划和分解任务，多个角色协作执行。', example: '制定科研软件的发布方案，分别完成测试计划、数据核查与交付检查。', action: '启动工作队', icon: 'group' },
  crewai_flow: { label: '条件流程', source: 'CrewAI 原生 Flow', description: '按真实任务结果执行条件分支，保留路由与工具记录。', example: '先评估方案是否需要修正；需要时执行修正步骤，再进行最终复核。', action: '运行流程', icon: 'tasks' },
};
