(function () {
  'use strict';

  function install() {
    if (document.getElementById('filemate-launcher')) return;
    const host = document.createElement('div');
    host.id = 'filemate-launcher';
    const shadow = host.attachShadow({mode: 'open'});
    const launch = document.createElement('button');
    launch.type = 'button';
    launch.textContent = 'FileMate';
    launch.setAttribute('aria-label', '打开 FileMate 文件工作台');
    launch.setAttribute('aria-expanded', 'false');
    launch.setAttribute('aria-controls', 'filemate-panel');
    const controlStyle = {
      font: '600 13px/1.2 system-ui, sans-serif', color: '#fff', background: '#0e6962',
      border: '1px solid #08756b', borderRadius: '8px', minHeight: '34px', cursor: 'pointer', padding: '6px 12px',
    };
    Object.assign(launch.style, controlStyle, {
      position: 'fixed', right: '18px', top: '64px', zIndex: '10000', boxShadow: '0 4px 16px #10252c38',
    });

    const panel = document.createElement('section');
    panel.id = 'filemate-panel';
    panel.setAttribute('aria-label', 'FileMate 文件工作台面板');
    Object.assign(panel.style, {
      position: 'fixed', inset: '0 0 0 auto', height: '100%',
      zIndex: '10001', background: '#f4f6f8', boxShadow: '-5px 0 24px #10252c38', flexDirection: 'column', display: 'none',
    });
    const mobile = window.matchMedia('(max-width: 675px)');
    const resize = () => { panel.style.width = mobile.matches ? '100%' : 'max(48vw, 550px)'; };
    mobile.addEventListener('change', resize);
    resize();

    const header = document.createElement('div');
    Object.assign(header.style, {
      display: 'flex', flex: 'none', alignItems: 'center', justifyContent: 'space-between', gap: '8px',
      padding: '6px 12px', minHeight: '40px', background: '#123e3d', color: '#fff', boxSizing: 'border-box',
    });
    const title = document.createElement('span');
    title.textContent = 'FileMate';
    title.style.font = '700 13px/1.3 system-ui, sans-serif';
    const close = document.createElement('button');
    close.type = 'button';
    close.textContent = '关闭面板';
    Object.assign(close.style, controlStyle, {minHeight: '28px', padding: '4px 9px'});
    header.append(title, close);

    const frame = document.createElement('iframe');
    frame.title = 'FileMate 文件工作台';
    Object.assign(frame.style, {width: '100%', flex: '1', minHeight: '0', border: '0'});
    panel.append(header, frame);
    shadow.append(launch, panel);
    document.body.append(host);

    // The pinned upstream UI does not display arbitrary hook cancellation reasons.
    // This owner-authenticated feed contains only bounded stop notices, never model text.
    const noticeBox = document.createElement('section');
    noticeBox.setAttribute('aria-label', 'FileMate 生成保护通知');
    noticeBox.setAttribute('role', 'alert');
    Object.assign(noticeBox.style, {display: 'none', position: 'fixed', bottom: '24px', right: '24px',
      zIndex: '10002', maxWidth: '420px', padding: '16px', borderRadius: '10px',
      background: '#fff7e5', color: '#442e08', boxShadow: '0 4px 20px #0004', font: '14px/1.6 system-ui'});
    const noticeText = document.createElement('div');
    const dismiss = document.createElement('button');
    dismiss.textContent = '知道了';dismiss.type = 'button';
    Object.assign(dismiss.style, controlStyle);
    noticeBox.append(noticeText, dismiss);shadow.append(noticeBox);
    const dismissed = new Set();
    try { for (const key of JSON.parse(sessionStorage.getItem('filemate-dismissed-stops') || '[]')) dismissed.add(key); } catch {}
    let pendingNotices = [], visibleNotice = null, noticeTimer, closed = false;
    const noticeKey = (n) => JSON.stringify([n.sessionId, n.attemptId, n.stoppedAt]);
    function renderNotice() {
      visibleNotice = pendingNotices.find(n => !dismissed.has(noticeKey(n)));
      noticeBox.style.display = visibleNotice ? 'block' : 'none';
      if (visibleNotice) noticeText.textContent = `会话 ${visibleNotice.sessionId}：${visibleNotice.reason}`;
    }
    dismiss.addEventListener('click', () => {
      if (visibleNotice) dismissed.add(noticeKey(visibleNotice));
      try { sessionStorage.setItem('filemate-dismissed-stops', JSON.stringify([...dismissed].slice(-100))); } catch {}
      renderNotice();
    });
    async function pollNotices() {
      try {
        if (!document.hidden) {
          const response = await fetch('/filemate/generation-status', {credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(5000)});
          if (response.ok) {
            const payload = await response.json();
            pendingNotices = Array.isArray(payload.items) ? payload.items : [];
            renderNotice();
          }
        }
      } catch { /* Status polling must never interrupt ordinary chat. */ }
      finally { if (!closed) noticeTimer = window.setTimeout(pollNotices, 3000); }
    }
    window.addEventListener('pagehide', () => { closed = true; window.clearTimeout(noticeTimer); });
    window.addEventListener('pageshow', () => { if (closed) { closed = false; pollNotices(); } });
    pollNotices();

    function setOpen(open) {
      if (open && !frame.hasAttribute('src')) frame.src = '/filemate';
      panel.style.display = open ? 'flex' : 'none';
      launch.setAttribute('aria-expanded', String(open));
      frame.contentWindow?.dispatchEvent(new Event('filemate-panel-visibility'));
      (open ? close : launch).focus();
    }

    launch.addEventListener('click', () => setOpen(true));
    close.addEventListener('click', () => setOpen(false));
    shadow.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && launch.getAttribute('aria-expanded') === 'true') setOpen(false);
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, {once: true});
  else install();
})();
