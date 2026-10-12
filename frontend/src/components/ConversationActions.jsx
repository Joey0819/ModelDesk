import { useEffect, useRef, useState } from 'react';
import { copyText } from '../artifacts';
import './ConversationActions.css';

function CopyIcon({ checked }) {
  return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{checked ? <path d="m5 12 4 4L19 6" /> : <><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V4H4v12h4" /></>}</svg>;
}

export function CopyTextButton({ text = '', label = '复制', className = '', title }) {
  const [feedback, setFeedback] = useState(null);
  const message = feedback && feedback.text === text ? feedback.message : '';
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  const copy = async () => {
    try { await copyText(text); setFeedback({ text, message: '已复制' }); }
    catch { setFeedback({ text, message: '请手动复制' }); }
    clearTimeout(timer.current); timer.current = setTimeout(() => setFeedback(null), 2200);
  };
  return <button type="button" className={`copy-text-button ${message === '已复制' ? 'copied' : ''} ${className}`} onClick={(event) => { event.stopPropagation(); copy(); }} disabled={!text} title={title || label} aria-label={message || label}><CopyIcon checked={message === '已复制'} /><span aria-live="polite">{message || label}</span></button>;
}

export default function ConversationActions({ text, label = '复制内容', children }) {
  return <div className="conversation-actions"><CopyTextButton text={text} label={label} />{children}</div>;
}
