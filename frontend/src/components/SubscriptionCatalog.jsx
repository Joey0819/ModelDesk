import { useModelCatalog } from '../model-catalog-context';
import { catalogReadTime, isSubscriptionSource, subscriptionSources } from '../model-catalog-state';
import { Icon } from './WorkbenchResults';
import './SubscriptionCatalog.css';

function ModelIds({ models, label }) {
  return <>
    {label && <div className="subscription-model-group">{label}</div>}
    <ul className="subscription-model-ids" aria-label={label}>{models.map((id) => <li key={id}><code>{id}</code></li>)}</ul>
  </>;
}

function GatewayModelIds({ models, source }) {
  if (source !== 'antigravity') return <ModelIds models={models} />;
  const isGemini = (id) => /(?:^|[/:])gemini(?:-|$)/i.test(id);
  const gemini = models.filter(isGemini);
  const other = models.filter((id) => !isGemini(id));
  if (!other.length) return <ModelIds models={models} />;
  return <>
    {gemini.length > 0 && <ModelIds models={gemini} label={`Gemini 系列（${gemini.length}）`} />}
    <ModelIds models={other} label={`同一网关的其他模型（${other.length}）`} />
  </>;
}

export function PublisherCatalog({ models, stale, scoped = true }) {
  if (!models?.length) return null;
  return <details className="subscription-model-details publisher-catalog">
    <summary>网关发布目录 · {scoped ? '此连接' : '当前'}暂不可选（{models.length}）{stale && ' · 上次读取'}</summary>
    <ModelIds models={models} />
  </details>;
}

function LoginConnection({ provider, models, service }) {
  const source = provider.id;
  const subscription = isSubscriptionSource(source);
  const details = subscription && service?.sourceDetails(source);
  const available = models.filter((model) => model.provider === source && !model.unavailable);
  const verified = available.filter((model) => model.verified).length;
  const refreshing = service?.refreshingSource === source;
  return <article className="subscription-connection" aria-label={provider.label}>
    <div className="subscription-connection-heading">
      <div>
        <strong>{provider.label}</strong>
        {subscription && <small>{subscriptionSources[source]}</small>}
      </div>
      <span className="connection-state">{provider.authenticated ? '已发现登录凭据' : '尚未接入'}</span>
      {subscription && service && <button type="button" className="subtle-button" aria-label={service.remote ? `读取主机的 ${provider.label} 模型列表` : `刷新 ${provider.label} 模型列表`} aria-busy={refreshing} disabled={service.refreshing} onClick={() => service.refresh(source)}>
        <Icon name="refresh" size={15} />{service.remote ? refreshing ? '正在读取' : '读取主机列表' : refreshing ? '正在刷新' : '刷新列表'}
      </button>}
    </div>
    {subscription && <div className="subscription-catalog-summary">
      {available.length ? <details className="subscription-model-details"><summary>网关可选模型（{available.length}）</summary><GatewayModelIds models={available.map((model) => model.id)} source={source} /></details> : <span>网关可选模型（0）</span>}
      <small aria-live="polite">{[details?.stale && '使用上次目录', details?.checkedAt && `检查于 ${catalogReadTime(details.checkedAt)}`, verified > 0 && `${verified} 个模型有调用记录`].filter(Boolean).join(' · ')}</small>
    </div>}
    {provider.reason && <small>{provider.reason}</small>}
    {details?.error && <p className="subscription-catalog-error" role="alert">{details.error}</p>}
    {details?.publisherWarning && <p className="publisher-catalog-hint" role="status">{details.publisherWarning}</p>}
    <PublisherCatalog models={details?.publisherOnly} stale={details?.publisherStale} />
  </article>;
}

export default function SubscriptionCatalog({ status }) {
  const service = useModelCatalog();
  const providers = (status?.providers || []).filter((provider) => isSubscriptionSource(provider.id) || provider.id === 'claude');
  return <section className="existing-connections" aria-label="已有登录连接">
    <h3>已有登录连接</h3>
    {providers.map((provider) => <LoginConnection key={provider.id} provider={provider} models={status?.models || []} service={service} />)}
  </section>;
}
