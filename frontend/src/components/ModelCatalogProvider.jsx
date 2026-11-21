import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { REMOTE_CLIENT } from '../api-base';
import { modelConnectionApi } from '../model-connection-api';
import { ModelCatalogContext } from '../model-catalog-context';
import { catalogOptions, failCatalogRefresh, gatewayDiscovery, isSubscriptionSource, mergeDiscoveries, publisherOnlyModels, subscriptionSources } from '../model-catalog-state';
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
  const [refreshingSource, setRefreshingSource] = useState(null);
  const pending = useRef(null);
  const [connectionsOpen, setConnectionsOpen] = useState(false);
  const refresh = useCallback((requestedSource = '') => {
    if (pending.current) return pending.current;
    const source = isSubscriptionSource(requestedSource) ? requestedSource : '';
    setRefreshingSource(source);
    pending.current = (async () => {
      if (!REMOTE_CLIENT) {
        try {
          const next = await modelConnectionApi.refreshCatalog(source);
          setDiscovery((previous) => mergeDiscoveries(previous, next));
        } catch (failure) {
          setDiscovery((previous) => failCatalogRefresh(previous, source, `无法刷新模型目录，保留上次读取的结果。${failure.message}`));
        }
      }
      // The host refreshes the gateway in the POST. Read its results only once it completes.
      await onRefresh();
    })().finally(() => { pending.current = null; setRefreshingSource(null); });
    return pending.current;
  }, [onRefresh]);
  const ensureModel = async (model) => {
    if (model.registered) return model.id;
    if (REMOTE_CLIENT) throw new Error(`请先在${machineShort(status)}上把此模型加入连接。`);
    const saved = await modelConnectionApi.addModel(model.connection_id, { model: { id: model.model_id, label: model.label || '' }, expected_updated_at: model.expected_updated_at });
    setDiscovery((previous) => previous?.sources && ({ ...previous, sources: previous.sources.map((source) => source.connection.id === saved.id ? { ...source, connection: { ...source.connection, updated_at: saved.updated_at } } : source) }) || previous);
    await onRefresh();
    return model.id;
  };
  const catalog = useMemo(() => catalogOptions(status, discovery), [status, discovery]);
  const sourceDetails = (source = '') => {
    const gateway = gatewayDiscovery(discovery, source);
    const subscription = isSubscriptionSource(source);
    const apiConnection = source.startsWith('connection:');
    const gatewayError = status?.gateway?.error || (status && !status.gateway?.online ? '订阅网关暂不可达。' : '');
    const scopedErrors = source ? [] : Object.values(discovery?.subscriptionGateways || {}).map((result) => result.error);
    const errors = [
      ...(!apiConnection ? [gateway?.error, ...scopedErrors, gatewayError] : []),
      ...(!subscription ? [status?.connections_error] : []),
    ];
    const publicationSources = subscription ? [source] : source ? [] : Object.keys(subscriptionSources);
    const publisherWarnings = publicationSources.map((id) => gatewayDiscovery(discovery, id)?.publisher_warning);
    const stale = Boolean(gateway?.stale || gateway?.error || status?.gateway?.stale || gatewayError);
    return {
      checkedAt: subscription && !REMOTE_CLIENT ? gateway?.checked_at : status?.checked_at,
      error: [...new Set(errors.filter(Boolean))].join(' '),
      stale,
      gatewayReread: !apiConnection && Boolean(gateway?.skipped),
      sources: subscription ? [] : (discovery?.sources || []).filter((item) => !apiConnection || source === `connection:${item.connection.id}`),
      publisherOnly: subscription ? publisherOnlyModels(status, discovery, source) : [],
      publisherStale: stale || Boolean(gateway?.publisher_warning),
      publisherWarning: [...new Set(publisherWarnings.filter(Boolean))].join(' '),
    };
  };
  const service = {
    catalog, refresh, refreshing: refreshingSource !== null, refreshingSource, sourceDetails, ensureModel, onOpenConnections: () => setConnectionsOpen(true),
    remote: REMOTE_CLIENT,
  };
  return <ModelCatalogContext.Provider value={service}>{children}{connectionsOpen && <ConnectionsDialog theme={theme} onClose={() => setConnectionsOpen(false)} onRefresh={refresh} />}</ModelCatalogContext.Provider>;
}
