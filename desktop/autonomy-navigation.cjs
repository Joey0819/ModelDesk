// A notification may arrive while the desktop still shows an old connection
// error. Keep one pending selection and reconnect before handing it to React.
function createAutonomyTaskNavigator({ getWindow, connectWorkbench, isWorkbenchURL }) {
  let pending = null;

  function clear(selection) {
    selection.contents.removeListener('did-finish-load', selection.ready);
    selection.owner.removeListener('closed', selection.closed);
    if (pending === selection) pending = null;
  }

  function deliver(selection) {
    if (pending !== selection) return false;
    const { owner, contents } = selection;
    if (owner.isDestroyed() || contents.isDestroyed()) { clear(selection); return false; }
    if (!isWorkbenchURL(contents.getURL()) || contents.isLoading()) return false;
    const target = selection.target;
    clear(selection);
    contents.send('modeldesk:open-autonomy-task', target);
    return true;
  }

  return target => {
    const owner = getWindow();
    if (!owner || owner.isDestroyed() || owner.webContents.isDestroyed()) return;
    if (owner.isMinimized()) owner.restore();
    owner.show(); owner.focus();
    if (pending && pending.owner !== owner) clear(pending);
    if (!pending) {
      const selection = { owner, contents: owner.webContents, target, connecting: false };
      selection.ready = () => deliver(selection);
      selection.closed = () => clear(selection);
      pending = selection;
      selection.contents.on('did-finish-load', selection.ready);
      owner.once('closed', selection.closed);
    }
    const selection = pending;
    selection.target = target;
    if (deliver(selection) || isWorkbenchURL(selection.contents.getURL()) || selection.connecting) return;
    selection.connecting = true;
    // connectWorkbench owns the startup/error UI. A failed attempt leaves one
    // pending target for a manual reconnect or another notification click.
    Promise.resolve().then(() => { if (pending === selection) return connectWorkbench(); }).catch(() => {}).finally(() => {
      selection.connecting = false;
      deliver(selection);
    });
  };
}

module.exports = { createAutonomyTaskNavigator };
