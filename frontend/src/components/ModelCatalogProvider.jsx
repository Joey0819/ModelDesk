import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { REMOTE_CLIENT } from '../api-base';
import { modelConnectionApi } from '../model-connection-api';
import { ModelCatalogContext } from '../model-catalog-context';
import { catalogOptions, mergeDiscoveries } from '../model-catalog-state';
import ModelConnections from './ModelConnections';
import { Icon } from './WorkbenchResults';

function ConnectionsDialog({ onClose, onRefresh, theme }) {
  const dialog = useRef(null);
  useEffect(() => { const element = dialog.current; element.showModal(); return () => element.close(); }, []);
  return <dialog ref={dialog} className="model-connections-dialog" data-theme={theme} aria-label="模型连接设置" onCancel={(event) => { event.preventDefault(); event.stopPropagation(); onClose(); }}><header><strong>模型连接</strong><button type="button" className="icon-button" aria-label="关闭模型连接设置" onClick={onClose}><Icon name="close" size={18} /></button></header><div className="model-connections-dialog-body"><ModelConnections onRefresh={onRefresh} /></div></dialog>;
}

export default function ModelCatalogProvider({ status, onRefresh, theme, children }) {
  const [discovery, setDiscovery] = useState(null);
  const [refreshing, setRefreshing] = useState(false), [error, setError] = useState('');
  const pending = useRef(null);
  const [connectionsOpen, setConnectionsOpen] = useState(false);
  const refresh = useCallback(() => {
    if (pending.current) return pending.current;
    setRefreshing(true); setError('');
    pending.current = (async () => {
      const [current, discovered] = await Promise.allSettled([onRefresh(), REMOTE_CLIENT ? Promise.resolve(null) : modelConnectionApi.refreshCatalog()]);
      const errors = [];
      if (current.status === 'rejected' || !current.value) errors.push('模型状态暂时无法更新，保留上次列表。');
      else {
        if (!current.value.gateway?.online) errors.push(current.value.gateway?.error || '订阅网关暂不可达。');
        if (current.value.connections_error) errors.push(current.value.connections_error);
      }
      if (discovered.status === 'fulfilled' && discovered.value) setDiscovery((previous) => mergeDiscoveries(previous, discovered.value));
      else if (discovered.status === 'rejected') errors.push(discovered.reason.message);
      setError(errors.join(' '));
    })().finally(() => { pending.current = null; setRefreshing(false); });
    return pending.current;
  }, [onRefresh]);
  const ensureModel = async (model) => {
    if (model.registered) return model.id;
    if (REMOTE_CLIENT) throw new Error('请先在 Mac 上把此模型加入连接。');
    const saved = await modelConnectionApi.addModel(model.connection_id, { model: { id: model.model_id, label: model.label || '' }, expected_updated_at: model.expected_updated_at });
    setDiscovery((previous) => previous && ({ ...previous, sources: previous.sources.map((source) => source.connection.id === saved.id ? { ...source, connection: { ...source.connection, updated_at: saved.updated_at } } : source) }));
    await onRefresh();
    return model.id;
  };
  const catalog = useMemo(() => catalogOptions(status, discovery), [status, discovery]);
  return <ModelCatalogContext.Provider value={{ catalog, refresh, refreshing, error, ensureModel, onOpenConnections: () => setConnectionsOpen(true), checkedAt: discovery?.checked_at || status?.checked_at, discovered: Boolean(discovery), sources: discovery?.sources || [], remote: REMOTE_CLIENT }}>{children}{connectionsOpen && <ConnectionsDialog theme={theme} onClose={() => setConnectionsOpen(false)} onRefresh={refresh} />}</ModelCatalogContext.Provider>;
}
