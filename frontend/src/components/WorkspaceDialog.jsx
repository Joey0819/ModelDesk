import { useLayoutEffect, useRef } from 'react';

export default function WorkspaceDialog({ onDismiss, focusRef, dismissOutside = false, children, ...props }) {
  const ref = useRef(null);
  useLayoutEffect(() => {
    const dialog = ref.current;
    dialog.showModal();
    focusRef?.current?.focus({ preventScroll: true });
    return () => dialog.close();
  }, [focusRef]);

  return <dialog {...props} ref={ref}
    onCancel={(event) => { event.preventDefault(); onDismiss(); }}
    onClick={(event) => {
      if (!dismissOutside || event.target !== ref.current) return;
      const rect = ref.current.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) onDismiss();
    }}>
    {children}
  </dialog>;
}
