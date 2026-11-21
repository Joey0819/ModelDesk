import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { REMOTE_CLIENT } from '../api-base';
import { modelConnectionApi } from '../model-connection-api';
import { ModelCatalogContext } from '../model-catalog-context';
import { catalogOptions, mergeDiscoveries } from '../model-catalog-state';
import ModelConnections from './ModelConnections';
import { machineShort } from '../machine';
import { Icon } from './WorkbenchResults';

function ConnectionsDialog({ onClose, onRefresh, theme }) {
  const dialog = useRef(null);
  useEffect(() => { const element = dialog.current; element.showModal(); return () => element.close(); }, []);
  return <dialog ref={dialog} className="model-connections-dialog" data-theme={theme} aria-label="模型连接设置" onCancel={(event) => { event.preventDefault(); event.stopPropagation(); onClose(); }}><header><strong>模型连接</strong><button type="button" className="icon-button" aria-label="关闭模型连接设置" onClick={onClose}><Icon name="close" size={18} /></button></header><div className="model-connections-dialog-body"><ModelConnections onRefresh={onRefresh} /></div></dialog>;
}

export default function ModelCatalogProvider({ status, onRefresh, theme, children }) {
  const [discovery, setDiscovery] = useState(null);
  const [refreshing, setRefreshing] = useState(false), [refreshError, setRefreshError] = useState('');
  const pending = useRef(null);
  const [connectionsOpen, setConnectionsOpen] = useState(false);
  const refresh = useCallback(() => {
    if (pending.current) return pending.current;
    setRefreshing(true); setRefreshError('');
    pending.current = (async () => {
      if (!REMOTE_CLIENT) {
        try {
          const next = await modelConnectionApi.refreshCatalog();
          setDiscovery((previous) => mergeDiscoveries(previous, next));
        } catch (failure) {
          setDiscovery((previous) => ({ ...previous, gateway: { stale: true }, sources: (previous?.sources || []).map((source) => ({ ...source, stale: true })) }));
          setRefreshError(`无法刷新模型目录，保留上次读取的结果。${failure.message}`);
        }
      }
      // The host refreshes the gateway in the POST. Read its results only once it completes.
      await onRefresh();
    })().finally(() => { pending.current = null; setRefreshing(false); });
    return pending.current;
  }, [onRefresh]);
  const ensureModel = async (model) => {
    if (model.registered) return model.id;
    if (REMOTE_CLIENT) throw new Error(`请先在${machineShort(status)}上把此模型加入连接。`);
    const saved = await modelConnectionApi.addModel(model.connection_id, { model: { id: model.model_id, label: model.label || '' }, expected_updated_at: model.expected_updated_at });
    setDiscovery((previous) => previous && ({ ...previous, sources: previous.sources.map((source) => source.connection.id === saved.id ? { ...source, connection: { ...source.connection, updated_at: saved.updated_at } } : source) }));
    await onRefresh();
    return model.id;
  };
  const catalog = useMemo(() => catalogOptions(status, discovery), [status, discovery]);
  const gatewayError = status?.gateway?.error || (status && !status.gateway?.online ? '订阅网关暂不可达。' : '');
  const error = [...new Set([refreshError, discovery?.gateway?.error, gatewayError, status?.connections_error].filter(Boolean))].join(' ');
  const service = {
    catalog, refresh, refreshing, error, ensureModel, onOpenConnections: () => setConnectionsOpen(true),
    checkedAt: status?.checked_at, sources: discovery?.sources || [], gatewayReread: Boolean(discovery?.gateway?.skipped), remote: REMOTE_CLIENT,
  };
  return <ModelCatalogContext.Provider value={service}>{children}{connectionsOpen && <ConnectionsDialog theme={theme} onClose={() => setConnectionsOpen(false)} onRefresh={refresh} />}</ModelCatalogContext.Provider>;
}
