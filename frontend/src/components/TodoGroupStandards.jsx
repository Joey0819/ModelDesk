import { useEffect, useState } from 'react';
import { groupApi } from '../group-api';
import { hasRoleContract } from '../mission-teams';
import { TeamRoleMap } from './RoleContractFields';
import WorkspaceDialog from './WorkspaceDialog';

function SavedRoomStandards({ roomId, projectId, onDismiss }) {
  const [room, setRoom] = useState(null);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let current = true;
    groupApi.get(roomId, projectId).then((value) => {
      if (value?.id !== roomId || (value.project_id ?? null) !== (projectId || null) ||
          !Array.isArray(value.team_snapshot?.members)) {
        throw new Error('群聊信息与当前看板不符，请重新打开。');
      }
      if (current) setRoom(value);
    }).catch((failure) => { if (current) setError(failure.message); });
    return () => { current = false; };
  }, [roomId, projectId, attempt]);

  const team = room?.team_snapshot;
  return <WorkspaceDialog className="group-options-dialog" aria-label="群协作约定" onDismiss={onDismiss} dismissOutside>
    <header><div><h2>群协作约定</h2>{room && <p>{room.title}</p>}</div>
      <button type="button" className="subtle-button" onClick={onDismiss}>返回看板</button>
    </header>
    <div className="group-options-body">
      {error ? <div role="alert"><p>{error}</p><button type="button" className="subtle-button"
        onClick={() => { setError(''); setAttempt((n) => n + 1); }}>重新读取</button></div> :
        !team ? <p role="status">正在读取群协作约定…</p> : <>
          {team.description && <p>{team.description}</p>}
          {team.members.some(hasRoleContract) ? <TeamRoleMap members={team.members} /> :
            <p>这个群还没有设置交付约定。可在群聊的「成员与分工」中设置每个角色的交付内容和交接顺序。</p>}
        </>}
    </div>
  </WorkspaceDialog>;
}

export default function TodoGroupStandards({ roomId, projectId }) {
  const [open, setOpen] = useState(false);
  return <>
    <button type="button" className="subtle-button" aria-haspopup="dialog" onClick={() => setOpen(true)}>群协作约定</button>
    {open && <SavedRoomStandards key={roomId} roomId={roomId} projectId={projectId} onDismiss={() => setOpen(false)} />}
  </>;
}
